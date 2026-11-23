import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import app as app_module
from storage import add_subscription, find_subscription, load_subscriptions, save_subscriptions


@pytest.fixture
def client():
    test_client = app_module.app.test_client()
    with test_client.session_transaction() as session:
        session["logged_in"] = True  # skip the login form; auth itself is tested in test_auth.py
    return test_client


VALID = {"name": "Netflix", "amount": 15.99, "billing_cycle": "monthly", "next_renewal_date": "2099-11-08", "category": "Video"}


def test_added_subscription_is_saved_and_reloaded():
    added = add_subscription(VALID)
    loaded = load_subscriptions()
    assert loaded == [added]
    assert loaded[0]["status"] == "active"


def test_subscriptions_keep_their_order():
    first = add_subscription({**VALID, "name": "First"})
    second = add_subscription({**VALID, "name": "Second"})
    assert [s["id"] for s in load_subscriptions()] == [first["id"], second["id"]]


def test_changes_to_a_subscription_are_saved():
    added = add_subscription(VALID)
    subs = load_subscriptions()
    find_subscription(subs, added["id"])["status"] = "cancelled"
    save_subscriptions(subs)
    assert load_subscriptions()[0]["status"] == "cancelled"


def test_find_subscription_returns_none_for_unknown_id():
    add_subscription(VALID)
    assert find_subscription(load_subscriptions(), "nope") is None


def test_api_creates_lists_renews_and_cancels(client):
    created = client.post("/api/subscriptions", json=VALID)
    assert created.status_code == 201
    sub_id = created.get_json()["id"]

    assert [s["id"] for s in client.get("/api/subscriptions").get_json()] == [sub_id]

    renewed = client.post(f"/api/subscriptions/{sub_id}/renew").get_json()
    assert renewed["next_renewal_date"] == "2099-12-08"

    cancelled = client.post(f"/api/subscriptions/{sub_id}/cancel").get_json()
    assert cancelled["status"] == "cancelled"
    assert client.get("/api/dashboard").get_json()["totals"]["active_count"] == 0


def test_api_unknown_subscription_returns_404(client):
    assert client.post("/api/subscriptions/nope/renew").status_code == 404
    assert client.post("/api/subscriptions/nope/cancel").status_code == 404


@pytest.mark.parametrize("change,message", [
    ({"name": ""}, "Missing required field"),
    ({"billing_cycle": "daily"}, "billing_cycle must be"),
    ({"amount": "abc"}, "amount must be a number"),
    ({"amount": "nan"}, "amount must be between"),
    ({"amount": -5}, "amount must be between"),
    ({"next_renewal_date": "not-a-date"}, "YYYY-MM-DD"),
    ({"next_renewal_date": "2026-13-45"}, "YYYY-MM-DD"),
    ({"name": "x" * 101}, "100 characters"),
])
def test_api_rejects_invalid_subscriptions(client, change, message):
    response = client.post("/api/subscriptions", json={**VALID, **change})
    assert response.status_code == 400
    assert message in response.get_json()["error"]


def test_a_bad_date_can_no_longer_break_the_dashboard(client):
    client.post("/api/subscriptions", json={**VALID, "next_renewal_date": "<img src=x>"})
    assert client.get("/api/dashboard").status_code == 200


def test_shared_list_can_be_capped(client, monkeypatch):
    monkeypatch.setenv("MAX_SUBSCRIPTIONS", "2")
    assert client.post("/api/subscriptions", json=VALID).status_code == 201
    assert client.post("/api/subscriptions", json=VALID).status_code == 201
    full = client.post("/api/subscriptions", json=VALID)
    assert full.status_code == 400
    assert "full" in full.get_json()["error"]


def test_writes_can_be_rate_limited_but_reads_are_not(client, monkeypatch):
    import limits

    limits._recent_requests.clear()
    monkeypatch.setenv("WRITE_LIMIT_PER_MINUTE", "2")
    codes = [client.post("/api/subscriptions", json=VALID).status_code for _ in range(4)]
    assert codes == [201, 201, 429, 429]
    assert client.get("/api/subscriptions").status_code == 200
    limits._recent_requests.clear()


def test_not_indexed_by_search_engines(client):
    assert "noindex" in client.get("/").headers["X-Robots-Tag"]
    assert "Disallow: /" in client.get("/robots.txt").get_data(as_text=True)


def test_api_lists_a_passed_renewal_moved_into_the_future(client):
    client.post("/api/subscriptions", json={**VALID, "next_renewal_date": "2020-01-15"})
    listed = client.get("/api/subscriptions").get_json()[0]
    assert date.fromisoformat(listed["next_renewal_date"]) >= date.today()
    assert listed["next_renewal_date"].endswith("-15")


def test_api_renewing_a_passed_renewal_lands_in_the_future(client):
    sub_id = client.post("/api/subscriptions", json={**VALID, "next_renewal_date": "2020-01-15"}).get_json()["id"]
    renewed = client.post(f"/api/subscriptions/{sub_id}/renew").get_json()
    assert date.fromisoformat(renewed["next_renewal_date"]) > date.today()
