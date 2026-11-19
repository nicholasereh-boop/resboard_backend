"""Nobody but the seller gets a published backend without a verified payment (not even staff); moderators still get it for review."""
import pytest

from apps.source_marketplace.models import SourceProject

from .marketplace_helpers import MP, client_for, new_project, private_dirs, publish, seller, buyer, upload  # noqa: F401

pytestmark = pytest.mark.django_db


def _published_backend(seller):
    c = client_for(seller)
    p = new_project(c, backend_price="49")
    upload(c, p["id"], kind="backend")
    publish(p["id"])
    pid = [o for o in client_for(seller).get(MP + f"projects/{p['slug']}/").json()["data"]["offers"] if o["kind"] == "backend"][0]["package_id"]
    return p, pid


def test_a_buyer_without_payment_cannot_download_the_backend(private_dirs, seller, buyer):
    _, pid = _published_backend(seller)
    assert client_for(buyer).get(MP + f"packages/{pid}/download/").status_code == 403


def test_staff_must_pay_for_a_published_backend_but_the_seller_does_not(private_dirs, seller, buyer):
    _, pid = _published_backend(seller)
    buyer.is_staff = True
    buyer.save()
    assert client_for(buyer).get(MP + f"packages/{pid}/download/").status_code == 403
    assert client_for(seller).get(MP + f"packages/{pid}/download/").status_code == 200


def test_staff_can_still_open_a_backend_that_is_waiting_for_review(private_dirs, seller, buyer):
    c = client_for(seller)
    p = new_project(c, backend_price="49")
    upload(c, p["id"], kind="backend")
    pid = SourceProject.objects.get(pk=p["id"]).packages.get(kind="backend").pk
    buyer.is_staff = True
    buyer.save()
    assert client_for(buyer).get(MP + f"packages/{pid}/download/").status_code == 200
