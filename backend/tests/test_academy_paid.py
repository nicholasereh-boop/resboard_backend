"""Phase 4: paid courses. The price comes from the database, Paystack is asked from the server, and access appears only after a
payment that matches exactly. Webhooks are signed, idempotent and cannot be replayed into a second grant."""
import hashlib
import hmac
import json

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academy.models import AcademyPaymentEvent, CourseEntitlement, CourseOrder, Enrollment
from apps.academy.services import progression
from apps.audit.models import AuditLog

from .academy_helpers import API, learner, learner2, make_course, make_lesson  # noqa: F401
from .marketplace_helpers import SECRET, client_for, paystack, private_dirs  # noqa: F401

pytestmark = pytest.mark.django_db
HOOK = API + "payments/webhook/"


@pytest.fixture
def paid_course():
    course = make_course("pro-react", title="Pro React", access_type="paid", price=49)
    lessons = [make_lesson(course, 1, is_preview=True), make_lesson(course, 2), make_lesson(course, 3)]
    return course, lessons


def checkout(client, course, **extra):
    return client.post(API + f"courses/{course.pk}/checkout/", extra, format="json")


def verify(client, reference):
    return client.post(API + "payments/verify/", {"reference": reference}, format="json")


def webhook(body, signature=None):
    raw = json.dumps(body).encode()
    sig = signature or hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
    return APIClient().post(HOOK, raw, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)


def charge_success(reference):
    return {"event": "charge.success", "data": {"reference": reference}}


def can_open(user, course, lesson):
    states, _ = progression.compute_states(user, course)
    return states[lesson.pk]["can_view"]


# ---- checkout
def test_the_price_is_read_from_the_database_and_converted_to_naira(paid_course, learner, paystack):
    course, _ = paid_course
    r = checkout(client_for(learner), course, amount=1, price="0.01", currency="USD", rate=1)  # browser claims are ignored
    assert r.status_code == 200
    d = r.json()["data"]
    assert (d["amount"], d["currency"], d["list_price"], d["list_currency"]) == ("73500.00", "NGN", "49.00", "USD")
    assert d["authorization_url"].startswith("https://") and d["reference"].startswith("rbac_")
    sent = paystack.initialized[0]
    assert (sent["amount_minor"], sent["currency"], sent["email"]) == (7350000, "NGN", learner.email)
    order = CourseOrder.objects.get()
    assert (order.status, order.amount_minor, str(order.fx_rate)) == ("pending", 7350000, "1500.000000")
    assert not CourseEntitlement.objects.exists()  # nothing is granted by starting a payment


def test_checkout_refuses_free_unpublished_owned_and_anonymous_requests(paid_course, learner, paystack):
    course, _ = paid_course
    free = make_course("free-one")
    draft = make_course("draft-paid", status="draft", access_type="paid", price=10)
    me = client_for(learner)
    assert checkout(me, free).status_code == 409
    assert checkout(me, draft).status_code == 404
    assert checkout(APIClient(), course).status_code in (401, 403)
    CourseEntitlement.objects.create(learner=learner, course=course, source="grant")
    assert checkout(me, course).status_code == 409
    assert CourseOrder.objects.count() == 0


def test_checkout_is_unavailable_without_a_payment_key_or_exchange_rate(paid_course, learner, settings, paystack):
    course, _ = paid_course
    settings.MARKETPLACE_FX_RATE = ""
    assert checkout(client_for(learner), course).status_code == 503
    assert CourseOrder.objects.count() == 0 and paystack.initialized == []


def test_checkout_says_unavailable_when_no_secret_key_is_configured(paid_course, learner, settings):
    course, _ = paid_course
    settings.PAYSTACK_SECRET_KEY = ""
    settings.MARKETPLACE_FX_LIVE = False
    settings.MARKETPLACE_FX_RATE = "1500"
    assert checkout(client_for(learner), course).status_code == 503


def test_a_paystack_outage_marks_the_order_failed(paid_course, learner, paystack):
    course, _ = paid_course
    paystack.fail_initialize = True
    assert checkout(client_for(learner), course).status_code in (502, 503)
    assert CourseOrder.objects.get().failure_reason == "initialize_failed"


# ---- paying
def test_paid_lessons_stay_locked_until_the_server_verifies_the_payment(paid_course, learner, paystack):
    course, (preview, l2, l3) = paid_course
    assert can_open(learner, course, preview) is True and can_open(learner, course, l2) is False
    ref = checkout(client_for(learner), course).json()["data"]["reference"]
    assert can_open(learner, course, l2) is False  # an order alone opens nothing
    r = verify(client_for(learner), ref)
    assert r.status_code == 200 and r.json()["data"]["status"] == "success" and r.json()["data"]["has_access"] is True
    assert paystack.verified == [ref]
    assert can_open(learner, course, l2) and can_open(learner, course, l3)
    ent = CourseEntitlement.objects.get()
    assert (ent.source, ent.status, ent.order_id) == ("purchase", "active", CourseOrder.objects.get().pk)
    assert Enrollment.objects.get(learner=learner, course=course).source == "purchase"
    assert AuditLog.objects.filter(action="academy.purchase").count() == 1


def test_verifying_twice_never_grants_or_logs_twice(paid_course, learner, paystack):
    course, _ = paid_course
    me = client_for(learner)
    ref = checkout(me, course).json()["data"]["reference"]
    verify(me, ref)
    assert verify(me, ref).status_code == 200
    assert CourseEntitlement.objects.count() == 1 and AuditLog.objects.filter(action="academy.purchase").count() == 1
    assert paystack.verified == [ref]  # the second call did not even ask Paystack


@pytest.mark.parametrize("override,reason", [
    ({"amount": 100}, "amount_mismatch"), ({"currency": "USD"}, "currency_mismatch"), ({"reference": "someone-else"}, "reference_mismatch")])
def test_a_payment_that_does_not_match_exactly_grants_nothing(paid_course, learner, paystack, override, reason):
    course, (_p, l2, _l3) = paid_course
    me = client_for(learner)
    ref = checkout(me, course).json()["data"]["reference"]
    paystack.result = override
    assert verify(me, ref).json()["data"]["status"] == "failed"
    assert CourseOrder.objects.get().failure_reason == reason
    assert not CourseEntitlement.objects.exists() and can_open(learner, course, l2) is False


@pytest.mark.parametrize("gateway", ["failed", "abandoned", "reversed"])
def test_a_failed_payment_grants_nothing(paid_course, learner, paystack, gateway):
    course, (_p, l2, _l3) = paid_course
    me = client_for(learner)
    ref = checkout(me, course).json()["data"]["reference"]
    paystack.result = {"status": gateway}
    assert verify(me, ref).json()["data"]["status"] == "failed"
    assert not CourseEntitlement.objects.exists()


def test_a_payment_still_in_progress_stays_pending(paid_course, learner, paystack):
    course, _ = paid_course
    me = client_for(learner)
    ref = checkout(me, course).json()["data"]["reference"]
    paystack.result = {"status": "ongoing"}
    assert verify(me, ref).json()["data"]["status"] == "pending"
    paystack.result = None
    assert verify(me, ref).json()["data"]["status"] == "success"  # it can still complete later


def test_one_learner_cannot_verify_or_see_another_learners_order(paid_course, learner, learner2, paystack):
    course, _ = paid_course
    ref = checkout(client_for(learner), course).json()["data"]["reference"]
    assert verify(client_for(learner2), ref).status_code == 404
    assert client_for(learner2).get(API + "orders/").json()["data"] == []
    assert [o["reference"] for o in client_for(learner).get(API + "orders/").json()["data"]] == [ref]


def test_when_paystack_cannot_be_reached_the_verify_answers_unavailable_and_changes_nothing(paid_course, learner, paystack):
    course, _ = paid_course
    me = client_for(learner)
    ref = checkout(me, course).json()["data"]["reference"]
    paystack.fail_verify = True
    assert verify(me, ref).status_code in (502, 503)
    assert CourseOrder.objects.get().status == "pending" and not CourseEntitlement.objects.exists()


# ---- the webhook
def test_the_webhook_needs_a_valid_signature(paid_course, learner, paystack):
    course, _ = paid_course
    ref = checkout(client_for(learner), course).json()["data"]["reference"]
    assert webhook(charge_success(ref), signature="0" * 128).status_code == 401
    assert APIClient().post(HOOK, json.dumps(charge_success(ref)), content_type="application/json").status_code == 401
    assert not CourseEntitlement.objects.exists() and paystack.verified == []


def test_a_signed_webhook_grants_access_once_even_when_delivered_many_times(paid_course, learner, paystack):
    course, (_p, l2, _l3) = paid_course
    ref = checkout(client_for(learner), course).json()["data"]["reference"]
    assert webhook(charge_success(ref)).status_code == 200
    assert can_open(learner, course, l2)
    again = webhook(charge_success(ref))
    assert again.status_code == 200 and again.json()["data"].get("duplicate") is True
    assert CourseEntitlement.objects.count() == 1 and AuditLog.objects.filter(action="academy.purchase").count() == 1
    assert AcademyPaymentEvent.objects.count() == 1 and AcademyPaymentEvent.objects.get().outcome == "paid"


def test_the_webhook_ignores_other_events_and_unknown_references(paid_course, learner, paystack):
    assert webhook({"event": "transfer.success", "data": {"reference": "x"}}).status_code == 200
    assert webhook(charge_success("rb_a_marketplace_reference")).status_code == 200  # not ours: quietly ignored
    assert AcademyPaymentEvent.objects.filter(outcome="unknown_reference").count() == 1
    assert paystack.verified == [] and not CourseEntitlement.objects.exists()


def test_a_webhook_whose_verification_fails_is_retried_not_lost(paid_course, learner, paystack):
    course, _ = paid_course
    ref = checkout(client_for(learner), course).json()["data"]["reference"]
    paystack.fail_verify = True
    assert webhook(charge_success(ref)).status_code == 503
    assert AcademyPaymentEvent.objects.get().processed_at is None  # so Paystack's retry is processed
    paystack.fail_verify = False
    assert webhook(charge_success(ref)).status_code == 200
    assert CourseEntitlement.objects.count() == 1


def test_the_marketplace_webhook_does_not_break_on_an_academy_reference(paid_course, learner, paystack):
    course, _ = paid_course
    ref = checkout(client_for(learner), course).json()["data"]["reference"]
    raw = json.dumps(charge_success(ref)).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
    r = APIClient().post("/api/v1/marketplace/payments/webhook/", raw, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
    assert r.status_code == 200  # the existing handler treats it as an unknown reference; the Academy return-verify path still works
    assert verify(client_for(learner), ref).json()["data"]["status"] == "success"


# ---- staff: orders, refunds, complimentary access
def test_only_staff_see_orders_and_use_the_money_and_access_tools(paid_course, learner):
    course, _ = paid_course
    for method, url in [("get", "admin/orders/"), ("post", "admin/orders/1/refund/"), ("get", f"admin/courses/{course.pk}/access/"), ("post", f"admin/courses/{course.pk}/access/")]:
        assert getattr(APIClient(), method)(API + url, {}, format="json").status_code in (401, 403)
        assert getattr(client_for(learner), method)(API + url, {}, format="json").status_code == 403


def test_staff_list_orders_with_the_learner_email_and_filters(paid_course, learner, staff_client, paystack):
    course, _ = paid_course
    checkout(client_for(learner), course)
    rows = staff_client.get(API + "admin/orders/").json()["data"]
    assert rows[0]["learner_email"] == learner.email and rows[0]["status"] == "pending"
    assert staff_client.get(API + "admin/orders/?status=success").json()["data"] == []


def test_a_refund_ends_the_access_it_gave_and_a_late_retry_cannot_bring_it_back(paid_course, learner, staff_client, paystack):
    course, (_p, l2, _l3) = paid_course
    me = client_for(learner)
    ref = checkout(me, course).json()["data"]["reference"]
    verify(me, ref)
    order = CourseOrder.objects.get()
    assert staff_client.post(API + f"admin/orders/{order.pk}/refund/").json()["data"]["status"] == "refunded"
    assert can_open(learner, course, l2) is False
    assert AuditLog.objects.filter(action="academy.order_refund").count() == 1
    webhook(charge_success(ref))  # Paystack retries an old success event
    assert CourseOrder.objects.get().status == "refunded" and can_open(learner, course, l2) is False
    assert staff_client.post(API + f"admin/orders/{order.pk}/refund/").status_code == 409


def test_an_unpaid_order_cannot_be_refunded(paid_course, learner, staff_client, paystack):
    course, _ = paid_course
    checkout(client_for(learner), course)
    assert staff_client.post(API + f"admin/orders/{CourseOrder.objects.get().pk}/refund/").status_code == 409


def test_staff_can_grant_and_revoke_complimentary_access_by_email(paid_course, learner, staff_client):
    course, (_p, l2, _l3) = paid_course
    url = API + f"admin/courses/{course.pk}/access/"
    r = staff_client.post(url, {"email": learner.email.upper(), "note": "Prize winner"}, format="json")
    assert r.status_code == 201 and r.json()["data"]["source"] == "grant" and r.json()["data"]["learner_email"] == learner.email
    assert can_open(learner, course, l2)
    assert Enrollment.objects.get(learner=learner, course=course).source == "grant"
    assert staff_client.post(url, {"email": learner.email}, format="json").status_code == 200  # idempotent
    assert CourseEntitlement.objects.count() == 1 and AuditLog.objects.filter(action="academy.access_grant").count() == 1
    assert staff_client.delete(url, {"email": learner.email}, format="json").json()["data"] == {"revoked": 1}
    assert can_open(learner, course, l2) is False
    assert AuditLog.objects.filter(action="academy.access_revoke").count() == 1


def test_a_grant_needs_a_real_account_a_paid_course_and_a_future_expiry(paid_course, learner, staff_client):
    course, _ = paid_course
    url = API + f"admin/courses/{course.pk}/access/"
    assert staff_client.post(url, {"email": "nobody@example.com"}, format="json").status_code == 400
    assert staff_client.post(url, {"email": ""}, format="json").status_code == 400
    assert staff_client.post(url, {"email": learner.email, "expires_on": "2020-01-01"}, format="json").status_code == 400
    free = make_course("free-one")
    assert staff_client.post(API + f"admin/courses/{free.pk}/access/", {"email": learner.email}, format="json").status_code == 409


def test_access_stops_when_an_entitlement_expires(paid_course, learner):
    course, (_p, l2, _l3) = paid_course
    CourseEntitlement.objects.create(learner=learner, course=course, source="grant", expires_at=timezone.now() + timezone.timedelta(days=1))
    assert can_open(learner, course, l2) is True
    CourseEntitlement.objects.update(expires_at=timezone.now() - timezone.timedelta(seconds=1))
    assert can_open(learner, course, l2) is False


def test_staff_can_open_every_paid_lesson_without_buying(paid_course, staff):
    course, (_p, l2, l3) = paid_course
    assert can_open(staff, course, l3) is True


def test_switching_the_academy_off_closes_checkout_and_the_webhook(paid_course, learner, settings, paystack):
    course, _ = paid_course
    settings.ACADEMY_ENABLED = False
    assert checkout(client_for(learner), course).status_code == 404
    assert webhook(charge_success("x")).status_code == 404


# ---- reconciling orders that never got confirmed
def test_the_reconcile_command_grants_access_for_an_order_that_was_paid_but_never_confirmed(paid_course, learner, paystack):
    from io import StringIO

    from django.core.management import call_command

    course, (_p, l2, _l3) = paid_course
    checkout(client_for(learner), course)
    CourseOrder.objects.update(created_at=timezone.now() - timezone.timedelta(minutes=10))  # old enough to check
    out = StringIO()
    call_command("academy_reconcile_orders", stdout=out)
    assert "paid 1" in out.getvalue()
    assert can_open(learner, course, l2)
    assert AuditLog.objects.get(action="academy.purchase").metadata["via"] == "reconcile"
    call_command("academy_reconcile_orders", stdout=StringIO())  # running it again changes nothing
    assert CourseEntitlement.objects.count() == 1


def test_the_reconcile_command_leaves_brand_new_and_very_old_orders_alone(paid_course, learner, paystack):
    from io import StringIO

    from django.core.management import call_command

    course, _ = paid_course
    checkout(client_for(learner), course)  # just created
    call_command("academy_reconcile_orders", stdout=StringIO())
    assert paystack.verified == []
    CourseOrder.objects.update(created_at=timezone.now() - timezone.timedelta(days=30))
    call_command("academy_reconcile_orders", stdout=StringIO())
    assert paystack.verified == [] and not CourseEntitlement.objects.exists()
