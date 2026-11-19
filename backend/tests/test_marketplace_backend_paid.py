"""An old backend listing priced at 0 can still not be downloaded without a verified payment."""
import pytest

from apps.source_marketplace.models import SourceProject

from .marketplace_helpers import MP, client_for, new_project, paystack, private_dirs, publish, seller, buyer, upload  # noqa: F401

pytestmark = pytest.mark.django_db


def test_a_published_backend_priced_at_zero_is_not_a_free_download(private_dirs, paystack, seller, buyer):
    c = client_for(seller)
    p = new_project(c, backend_price="49")
    upload(c, p["id"], kind="backend")
    publish(p["id"])
    SourceProject.objects.filter(pk=p["id"]).update(backend_price=0)  # listed before the rule existed
    offers = client_for(buyer).get(MP + f"projects/{p['slug']}/").json()["data"]["offers"]
    backend = [o for o in offers if o["kind"] == "backend"][0]
    assert backend["is_free"] is False
    assert client_for(buyer).get(MP + f"packages/{backend['package_id']}/download/").status_code in (403, 404)
    r = client_for(buyer).post(MP + f"projects/{p['id']}/checkout/", {"kind": "backend"}, format="json")
    assert r.status_code == 400
