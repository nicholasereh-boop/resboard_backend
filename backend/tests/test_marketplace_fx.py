"""Dollar prices, naira charges: the server converts, the buyer's browser never supplies a rate or an amount."""
import pytest
import requests
from django.core.cache import cache

from apps.source_marketplace.models import SourcePurchase
from apps.source_marketplace.services import fx

from .marketplace_helpers import MP, client_for, new_project, paystack, private_dirs, publish, seller, buyer, upload  # noqa: F401

pytestmark = pytest.mark.django_db


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body


@pytest.fixture(autouse=True)
def _fresh_cache(settings):
    settings.MARKETPLACE_CHARGE_CURRENCY = "NGN"
    settings.MARKETPLACE_FX_LIVE = False
    settings.MARKETPLACE_FX_RATE = "1500"
    cache.clear()
    yield
    cache.clear()


def _listing(seller, price="49"):
    c = client_for(seller)
    p = new_project(c, backend_price=price)
    upload(c, p["id"], kind="backend")
    publish(p["id"])
    return p


def test_the_live_rate_is_used_and_cached(settings, monkeypatch):
    settings.MARKETPLACE_FX_LIVE = True
    calls = []
    monkeypatch.setattr(requests, "get", lambda url, timeout: calls.append(url) or Resp({"result": "success", "rates": {"NGN": 1612.5}}))
    assert str(fx.get_rate("USD", "NGN")) == "1612.5" and str(fx.get_rate("USD", "NGN")) == "1612.5"
    assert len(calls) == 1 and calls[0].endswith("/USD")  # second answer came from the cache


@pytest.mark.parametrize("answer", [Resp({}, 500), Resp({"result": "error"}), Resp({"result": "success", "rates": {}}),
                                    Resp({"result": "success", "rates": {"NGN": -5}}), Resp({"result": "success", "rates": {"NGN": "abc"}})])
def test_a_bad_live_answer_falls_back_to_the_fixed_rate(settings, monkeypatch, answer):
    settings.MARKETPLACE_FX_LIVE = True
    monkeypatch.setattr(requests, "get", lambda url, timeout: answer)
    assert str(fx.get_rate("USD", "NGN")) == "1500"


def test_a_network_failure_falls_back_to_the_fixed_rate(settings, monkeypatch):
    settings.MARKETPLACE_FX_LIVE = True

    def boom(url, timeout):
        raise requests.ConnectionError()

    monkeypatch.setattr(requests, "get", boom)
    assert str(fx.get_rate("USD", "NGN")) == "1500"


def test_with_no_rate_at_all_checkout_is_refused_and_nothing_is_started(paystack, seller, buyer, settings):
    settings.MARKETPLACE_FX_RATE = ""
    p = _listing(seller)
    r = client_for(buyer).post(MP + f"projects/{p['id']}/checkout/", {"kind": "backend"}, format="json")
    assert r.status_code == 503 and SourcePurchase.objects.count() == 0 and paystack.initialized == []


def test_the_same_currency_needs_no_conversion(settings):
    settings.MARKETPLACE_CHARGE_CURRENCY = "USD"
    amount, currency, rate = fx.charge_quote("49", "USD")
    assert (str(amount), currency, str(rate)) == ("49.00", "USD", "1")


def test_conversion_rounds_to_the_kobo(settings):
    settings.MARKETPLACE_FX_RATE = "1523.3333"
    assert str(fx.charge_quote("9.99", "USD")[0]) == "15218.10"


def test_a_paid_naira_charge_unlocks_the_backend_only_after_verification(paystack, seller, buyer):
    p = _listing(seller)
    d = client_for(buyer).post(MP + f"projects/{p['id']}/checkout/", {"kind": "backend", "amount": 1, "rate": 1}, format="json").json()["data"]
    assert d["currency"] == "NGN" and d["amount"] == "73500.00"  # the browser's "amount" and "rate" were ignored
    r = client_for(buyer).post(MP + "payments/verify/", {"reference": d["reference"]}, format="json")
    assert r.json()["data"]["status"] == "success"
    shown = r.json()["data"]
    assert (shown["amount"], shown["currency"], shown["list_price"], shown["list_currency"]) == ("73500.00", "NGN", "49.00", "USD")
