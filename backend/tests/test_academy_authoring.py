"""Phase 2: staff create, change, publish and order courses and lessons. Learners and visitors can do none of it."""
import pytest
from rest_framework.test import APIClient

from apps.academy.models import Course, CourseEntitlement, Enrollment, Lesson, LessonProgress
from apps.audit.models import AuditLog
from apps.media.models import MediaAsset

from .academy_helpers import API, learner, make_course, make_lesson  # noqa: F401
from .marketplace_helpers import client_for

pytestmark = pytest.mark.django_db
ADMIN = API + "admin/"
GOOD_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def new_course(client, **overrides):
    body = {"title": "HTML Basics", "summary": "Start here", "description": "Learn HTML from scratch."}
    body.update(overrides)
    r = client.post(ADMIN + "courses/", body, format="json")
    assert r.status_code == 201, r.content
    return r.json()["data"]


def new_lesson(client, course_id, **overrides):
    body = {"title": "Tags and elements", "youtube_url": GOOD_URL}
    body.update(overrides)
    r = client.post(ADMIN + f"courses/{course_id}/lessons/", body, format="json")
    assert r.status_code == 201, r.content
    return r.json()["data"]


def ready_course(client, **overrides):
    c = new_course(client, video_rights_confirmed=True, **overrides)
    new_lesson(client, c["id"], is_published=True)
    return c


# ---- who may do this
def test_only_staff_can_use_the_authoring_endpoints(learner, staff_client):
    course = make_course()
    for method, url in [("get", "courses/"), ("post", "courses/"), ("get", f"courses/{course.pk}/"), ("patch", f"courses/{course.pk}/"),
                        ("delete", f"courses/{course.pk}/"), ("post", f"courses/{course.pk}/publish/"), ("post", f"courses/{course.pk}/lessons/")]:
        assert getattr(APIClient(), method)(ADMIN + url, {}, format="json").status_code in (401, 403), url
        assert getattr(client_for(learner), method)(ADMIN + url, {}, format="json").status_code == 403, url
    assert staff_client.get(ADMIN + "courses/").status_code == 200


def test_switching_the_academy_off_closes_the_staff_endpoints_too(staff_client, settings):
    settings.ACADEMY_ENABLED = False
    assert staff_client.get(ADMIN + "courses/").status_code == 404


# ---- courses
def test_a_free_course_is_created_as_a_draft_with_a_safe_unique_slug(staff_client, staff):
    a = new_course(staff_client)
    b = new_course(staff_client)
    assert (a["status"], a["access_type"], a["price"], a["currency"]) == ("draft", "free", "0.00", "USD")
    assert a["slug"] == "html-basics" and b["slug"] == "html-basics-2"
    assert Course.objects.get(pk=a["id"]).creator == staff


def test_a_paid_course_needs_a_price_and_a_free_course_always_costs_nothing(staff_client):
    assert staff_client.post(ADMIN + "courses/", {"title": "X", "access_type": "paid"}, format="json").status_code == 400
    assert staff_client.post(ADMIN + "courses/", {"title": "X", "access_type": "paid", "price": "20000"}, format="json").status_code == 400  # above the cap
    paid = new_course(staff_client, title="Pro", access_type="paid", price="49")
    assert paid["price"] == "49.00"
    free = new_course(staff_client, title="Free one", access_type="free", price="99")
    assert free["price"] == "0.00"


def test_clients_cannot_set_status_creator_or_dates(staff_client):
    c = new_course(staff_client, status="published", published_at="2020-01-01T00:00:00Z", creator=1)
    assert c["status"] == "draft" and c["published_at"] is None


def test_text_fields_are_cleaned_and_markup_in_short_fields_is_refused(staff_client):
    assert staff_client.post(ADMIN + "courses/", {"title": "<b>x</b>"}, format="json").status_code == 400
    c = new_course(staff_client, description="Use <div> for blocks")
    assert c["description"] == "Use <div> for blocks"  # plain text: kept as typed, always shown as text


def test_a_custom_slug_must_be_valid_and_free(staff_client):
    new_course(staff_client, slug="my-course")
    assert staff_client.post(ADMIN + "courses/", {"title": "Y", "slug": "my-course"}, format="json").status_code == 400
    assert staff_client.post(ADMIN + "courses/", {"title": "Y", "slug": "Bad Slug!"}, format="json").status_code == 400
    assert staff_client.post(ADMIN + "courses/", {"title": "Y", "slug": "12345"}, format="json").status_code == 400


def test_the_thumbnail_must_be_an_existing_library_image(staff_client):
    assert staff_client.post(ADMIN + "courses/", {"title": "Z", "thumbnail_id": 999}, format="json").status_code == 400
    asset = MediaAsset.objects.create(file="uploads/2026/01/x.png", width=10, height=10)
    c = new_course(staff_client, title="With image", thumbnail_id=asset.pk)
    assert c["thumbnail"]["id"] == asset.pk


def test_updating_a_course_and_the_price_change_is_audited(staff_client):
    c = new_course(staff_client, access_type="paid", price="49")
    r = staff_client.patch(ADMIN + f"courses/{c['id']}/", {"price": "59", "summary": "New summary"}, format="json")
    assert r.status_code == 200 and r.json()["data"]["price"] == "59.00"
    entry = AuditLog.objects.filter(action="academy.course_update").latest("id")
    assert entry.metadata["price_before"] == "49.00" and entry.metadata["price_after"] == "59.00"


def test_a_course_with_learners_cannot_switch_between_free_and_paid_or_be_deleted(staff_client, learner):
    c = new_course(staff_client)
    Enrollment.objects.create(learner=learner, course_id=c["id"])
    assert staff_client.patch(ADMIN + f"courses/{c['id']}/", {"access_type": "paid", "price": "10"}, format="json").status_code == 400
    assert staff_client.delete(ADMIN + f"courses/{c['id']}/").status_code == 409
    assert Course.objects.filter(pk=c["id"]).exists()


def test_an_untouched_course_can_be_deleted_and_it_is_audited(staff_client):
    c = new_course(staff_client)
    assert staff_client.delete(ADMIN + f"courses/{c['id']}/").status_code == 204
    assert not Course.objects.filter(pk=c["id"]).exists()
    assert AuditLog.objects.filter(action="academy.course_delete").exists()


def test_the_course_list_filters_by_status_access_and_search(staff_client):
    new_course(staff_client, title="Alpha")
    new_course(staff_client, title="Beta", access_type="paid", price="10")
    assert staff_client.get(ADMIN + "courses/?access=paid").json()["data"][0]["title"] == "Beta"
    assert len(staff_client.get(ADMIN + "courses/?q=alp").json()["data"]) == 1
    assert staff_client.get(ADMIN + "courses/?status=published").json()["data"] == []


# ---- publishing
def test_a_course_cannot_be_published_until_it_is_ready_and_the_reasons_are_listed(staff_client):
    c = new_course(staff_client)
    r = staff_client.post(ADMIN + f"courses/{c['id']}/publish/")
    assert r.status_code == 400
    reasons = " ".join(r.json()["errors"]["publish"])
    assert "lesson" in reasons and "right to use the videos" in reasons
    new_lesson(staff_client, c["id"], is_published=True, is_intro=True)  # an intro alone is not enough
    assert staff_client.post(ADMIN + f"courses/{c['id']}/publish/").status_code == 400


def test_publish_unpublish_archive_work_and_are_audited(staff_client):
    c = ready_course(staff_client)
    assert staff_client.post(ADMIN + f"courses/{c['id']}/publish/").json()["data"]["status"] == "published"
    first = Course.objects.get(pk=c["id"]).published_at
    assert first is not None
    assert staff_client.post(ADMIN + f"courses/{c['id']}/unpublish/").json()["data"]["status"] == "draft"
    staff_client.post(ADMIN + f"courses/{c['id']}/publish/")
    assert Course.objects.get(pk=c["id"]).published_at == first  # the original publishing date is kept
    assert staff_client.post(ADMIN + f"courses/{c['id']}/archive/").json()["data"]["status"] == "archived"
    assert staff_client.post(ADMIN + f"courses/{c['id']}/explode/").status_code == 400
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"academy.course_publish", "academy.course_unpublish", "academy.course_archive"} <= actions


# ---- lessons
def test_lessons_are_added_in_order_and_only_the_video_id_is_kept(staff_client):
    c = new_course(staff_client)
    a = new_lesson(staff_client, c["id"], title="One")
    b = new_lesson(staff_client, c["id"], title="Two", youtube_url="https://youtu.be/9bZkp7q19f0?t=5")
    assert (a["position"], b["position"]) == (0, 1)
    assert a["youtube_id"] == "dQw4w9WgXcQ" and b["youtube_id"] == "9bZkp7q19f0"
    assert a["completion_threshold"] == 90 and a["requires_comment_to_unlock_next"] is True  # the site defaults


@pytest.mark.parametrize("bad", ["https://evil.example/watch?v=dQw4w9WgXcQ", "<iframe src=x></iframe>", "javascript:alert(1)", "not a link"])
def test_a_lesson_video_must_be_a_real_youtube_link(staff_client, bad):
    c = new_course(staff_client)
    r = staff_client.post(ADMIN + f"courses/{c['id']}/lessons/", {"title": "T", "youtube_url": bad}, format="json")
    assert r.status_code == 400 and "youtube_url" in r.json()["errors"]


def test_a_lesson_cannot_be_published_without_a_video(staff_client):
    c = new_course(staff_client)
    r = staff_client.post(ADMIN + f"courses/{c['id']}/lessons/", {"title": "T", "is_published": True}, format="json")
    assert r.status_code == 400
    assert new_lesson(staff_client, c["id"], youtube_url="", title="Draft lesson")["is_published"] is False


def test_only_one_intro_per_course_and_the_intro_never_gates_the_next_lesson(staff_client):
    c = new_course(staff_client)
    intro = new_lesson(staff_client, c["id"], title="Welcome", is_intro=True, requires_comment_to_unlock_next=True)
    assert intro["requires_comment_to_unlock_next"] is False
    r = staff_client.post(ADMIN + f"courses/{c['id']}/lessons/", {"title": "Second intro", "is_intro": True}, format="json")
    assert r.status_code == 400
    other = new_lesson(staff_client, c["id"], title="Real")
    r = staff_client.patch(ADMIN + f"lessons/{other['id']}/", {"is_intro": True}, format="json")
    assert r.status_code == 400


def test_the_completion_threshold_stays_between_10_and_100(staff_client):
    c = new_course(staff_client)
    for bad in (5, 150):
        assert staff_client.post(ADMIN + f"courses/{c['id']}/lessons/", {"title": "T", "completion_threshold": bad}, format="json").status_code == 400
    assert new_lesson(staff_client, c["id"], completion_threshold=75)["completion_threshold"] == 75


def test_updating_and_deleting_a_lesson(staff_client, learner):
    c = new_course(staff_client)
    lesson = new_lesson(staff_client, c["id"])
    r = staff_client.patch(ADMIN + f"lessons/{lesson['id']}/", {"title": "Renamed", "is_preview": True}, format="json")
    assert r.json()["data"]["title"] == "Renamed" and r.json()["data"]["is_preview"] is True
    LessonProgress.objects.create(learner=learner, lesson_id=lesson["id"], watched_seconds=5)
    assert staff_client.delete(ADMIN + f"lessons/{lesson['id']}/").status_code == 409  # learners have progress: unpublish instead
    other = new_lesson(staff_client, c["id"], title="Unused")
    assert staff_client.delete(ADMIN + f"lessons/{other['id']}/").status_code == 204


def test_reordering_rewrites_positions_and_rejects_a_wrong_list(staff_client):
    c = new_course(staff_client)
    ids = [new_lesson(staff_client, c["id"], title=t)["id"] for t in ("A", "B", "C")]
    r = staff_client.post(ADMIN + f"courses/{c['id']}/lessons/reorder/", {"order": [ids[2], ids[0], ids[1]]}, format="json")
    assert [x["title"] for x in r.json()["data"]] == ["C", "A", "B"] and [x["position"] for x in r.json()["data"]] == [0, 1, 2]
    for bad in ([ids[0]], [ids[0], ids[0], ids[1]], [ids[0], ids[1], 99999], "nope"):
        assert staff_client.post(ADMIN + f"courses/{c['id']}/lessons/reorder/", {"order": bad}, format="json").status_code == 400
    assert Lesson.objects.get(pk=ids[2]).position == 0  # unchanged by the failed attempts


def test_lesson_changes_are_audited_without_storing_text(staff_client):
    c = new_course(staff_client)
    new_lesson(staff_client, c["id"], title="Secret plan", description="private words")
    entry = AuditLog.objects.filter(action="academy.lesson_create").latest("id")
    assert entry.metadata["title"] == "Secret plan" and "private words" not in str(entry.metadata)
