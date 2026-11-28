"""
The start date and the cancellation date of a subscription. A new subscription starts today unless it is given
an earlier start date, and cancelling it records the day it was cancelled. Both dates are the date where the
subscription's owner is, which is the date in the subscription's own time zone, and both are kept by both kinds
of storage. Subscriptions saved before these dates existed have no start date.
"""

import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import app as app_module
import storage
from storage import add_subscription, find_subscription, load_subscriptions, save_subscriptions
from tracker import cancel_subscription

# The renewal is far in the future, so the list never moves it while a test runs.
VALID = {"name": "Netflix", "amount": 15.99, "billing_cycle": "monthly", "next_renewal_date": "2099-11-08", "category": "Video"}
BAD_FORMAT = "start_date must be a date in YYYY-MM-DD format"
IN_THE_FUTURE = "start_date must be today or an earlier date"

needs_database = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="runs against PostgreSQL when DATABASE_URL is set")


# ---------- cancelling, as plain logic ----------

def test_cancelling_records_the_status_and_the_date_it_is_given():
    sub = {"status": "active", "start_date": "2026-01-10"}

    result = cancel_subscription(sub, date(2026, 11, 24))

    assert result is sub
    assert (sub["status"], sub["cancelled_date"]) == ("cancelled", "2026-11-24")


def test_cancelling_without_a_date_uses_todays_date():
    before = date.today().isoformat()
    sub = cancel_subscription({"status": "active"})
    after = date.today().isoformat()

    assert sub["cancelled_date"] in (before, after)


def test_cancelling_again_keeps_the_first_date():
    sub = cancel_subscription({"status": "active"}, date(2026, 11, 24))

    cancel_subscription(sub, date(2026, 12, 15))

    assert (sub["status"], sub["cancelled_date"]) == ("cancelled", "2026-11-24")


def test_a_subscription_cancelled_before_dates_were_saved_stays_without_one():
    sub = cancel_subscription({"status": "cancelled"}, date(2026, 12, 15))

    assert "cancelled_date" not in sub


@pytest.mark.parametrize("start, expected", [
    pytest.param("2026-11-20", "2026-11-24", id="started-earlier"),
    pytest.param("2026-11-24", "2026-11-24", id="started-today"),
    pytest.param("2026-11-25", "2026-11-25", id="starts-after-today-so-the-start-date-is-used"),
    pytest.param(None, "2026-11-24", id="no-start-date"),
    pytest.param("", "2026-11-24", id="empty-start-date"),
])
def test_a_subscription_is_never_cancelled_before_it_started(start, expected):
    sub = {"status": "active", "start_date": start}

    cancel_subscription(sub, date(2026, 11, 24))

    assert sub["cancelled_date"] == expected


def test_a_subscription_with_no_start_date_key_can_be_cancelled():
    assert cancel_subscription({"status": "active"}, date(2026, 11, 24))["cancelled_date"] == "2026-11-24"


# ---------- the dates through the API ----------

class FixedClock(datetime):
    """The moment is 23:30 UTC on 24 Nov 2026: already the 25th in Auckland and still the 24th in Los Angeles."""

    at = datetime(2026, 11, 24, 23, 30, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.at.astimezone(tz)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "datetime", FixedClock)
    test_client = app_module.app.test_client()
    with test_client.session_transaction() as session:
        session["logged_in"] = True  # skip the login form; auth itself is tested in test_auth.py
    return test_client


def create(client, **changes):
    return client.post("/api/subscriptions", json={**VALID, **changes})


@pytest.mark.parametrize("zone, today", [
    pytest.param("Pacific/Auckland", "2026-11-25", id="ahead-of-utc"),
    pytest.param("America/Los_Angeles", "2026-11-24", id="behind-utc"),
    pytest.param("UTC", "2026-11-24", id="utc"),
    pytest.param("", "2026-11-24", id="no-zone-means-utc"),
])
def test_a_new_subscription_starts_today_in_its_own_time_zone(client, zone, today):
    created = create(client, time_zone=zone)

    assert created.status_code == 201
    assert created.get_json()["start_date"] == today
    assert created.get_json()["cancelled_date"] is None
    assert load_subscriptions()[0]["start_date"] == today


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_start_date_means_today(client, blank):
    assert create(client, start_date=blank).get_json()["start_date"] == "2026-11-24"


def test_a_start_date_in_the_past_is_kept(client):
    created = create(client, start_date="2025-03-10")

    assert created.status_code == 201
    assert created.get_json()["start_date"] == "2025-03-10"
    assert client.get("/api/subscriptions").get_json()[0]["start_date"] == "2025-03-10"
    assert load_subscriptions()[0]["start_date"] == "2025-03-10"


def test_spaces_around_the_start_date_are_dropped(client):
    assert create(client, start_date=" 2025-03-10 ").get_json()["start_date"] == "2025-03-10"


def test_a_start_date_in_the_future_is_refused_and_nothing_is_saved(client):
    response = create(client, start_date="2026-11-26")

    assert response.status_code == 400
    assert response.get_json() == {"error": IN_THE_FUTURE}
    assert load_subscriptions() == []


def test_today_is_judged_in_the_subscriptions_time_zone(client):
    assert create(client, time_zone="Pacific/Auckland", start_date="2026-11-25").status_code == 201
    assert create(client, time_zone="Pacific/Auckland", start_date="2026-11-26").status_code == 400
    assert create(client, time_zone="America/Los_Angeles", start_date="2026-11-24").status_code == 201
    assert create(client, time_zone="America/Los_Angeles", start_date="2026-11-25").status_code == 400
    assert create(client, time_zone="", start_date="2026-11-25").status_code == 400


@pytest.mark.parametrize("bad", [
    "2026-13-01", "2026-02-30", "12/31/2025", "20250310", "2025-03-10T10:00", "2025-3-1", "yesterday", 20250310, 0, True,
    ["2025-03-10"],
])
def test_a_start_date_that_is_not_yyyy_mm_dd_is_refused_and_nothing_is_saved(client, bad):
    response = create(client, start_date=bad)

    assert response.status_code == 400
    assert response.get_json() == {"error": BAD_FORMAT}
    assert load_subscriptions() == []


def test_the_end_date_message_still_names_its_own_field(client):
    response = create(client, remind_until="31 December")

    assert response.get_json() == {"error": "remind_until must be a date in YYYY-MM-DD format"}


def test_cancelling_records_todays_date_in_the_subscriptions_time_zone(client):
    started = {"start_date": "2025-03-10"}
    in_auckland = create(client, time_zone="Pacific/Auckland", **started).get_json()["id"]
    in_los_angeles = create(client, time_zone="America/Los_Angeles", **started).get_json()["id"]
    in_utc = create(client, time_zone="", **started).get_json()["id"]

    assert client.post(f"/api/subscriptions/{in_auckland}/cancel").get_json()["cancelled_date"] == "2026-11-25"
    assert client.post(f"/api/subscriptions/{in_los_angeles}/cancel").get_json()["cancelled_date"] == "2026-11-24"
    assert client.post(f"/api/subscriptions/{in_utc}/cancel").get_json()["cancelled_date"] == "2026-11-24"
    assert [s["cancelled_date"] for s in load_subscriptions()] == ["2026-11-25", "2026-11-24", "2026-11-24"]


def test_cancelling_changes_only_the_status_and_the_date(client):
    sub_id = create(client, start_date="2025-03-10").get_json()["id"]
    before = client.get("/api/subscriptions").get_json()[0]

    cancelled = client.post(f"/api/subscriptions/{sub_id}/cancel").get_json()

    assert {**cancelled, "status": "active", "cancelled_date": None} == before
    assert cancelled["start_date"] == "2025-03-10"
    assert client.get("/api/subscriptions").get_json()[0] == cancelled


def test_cancelling_twice_keeps_the_first_cancellation_date(client, monkeypatch):
    sub_id = create(client).get_json()["id"]
    client.post(f"/api/subscriptions/{sub_id}/cancel")
    monkeypatch.setattr(FixedClock, "at", datetime(2026, 12, 15, 9, tzinfo=timezone.utc))

    second = client.post(f"/api/subscriptions/{sub_id}/cancel")

    assert second.status_code == 200
    assert second.get_json()["cancelled_date"] == "2026-11-24"
    assert load_subscriptions()[0]["cancelled_date"] == "2026-11-24"


def test_a_subscription_saved_before_start_dates_can_still_be_cancelled(client):
    added = add_subscription({**VALID, "time_zone": "UTC"})
    assert added["start_date"] is None

    cancelled = client.post(f"/api/subscriptions/{added['id']}/cancel").get_json()

    assert cancelled["status"] == "cancelled"
    assert cancelled["start_date"] is None
    assert cancelled["cancelled_date"] == "2026-11-24"


def test_changing_the_time_zone_cannot_make_a_subscription_cancelled_before_it_started(client):
    sub_id = create(client, time_zone="Pacific/Auckland").get_json()["id"]  # starts 25 Nov in Auckland
    client.post(f"/api/subscriptions/{sub_id}/reminder", json={"days": 7, "time_zone": "America/Los_Angeles"})

    cancelled = client.post(f"/api/subscriptions/{sub_id}/cancel").get_json()

    assert cancelled["start_date"] == "2026-11-25"
    assert cancelled["cancelled_date"] == "2026-11-25"  # it is still the 24th in Los Angeles


def test_an_unknown_subscription_is_still_not_found_when_cancelling(client):
    response = client.post("/api/subscriptions/nope/cancel")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Subscription not found"}


def test_a_cancelled_subscription_stays_out_of_the_totals(client):
    sub_id = create(client).get_json()["id"]
    client.post(f"/api/subscriptions/{sub_id}/cancel")

    assert client.get("/api/dashboard").get_json()["totals"]["active_count"] == 0


# ---------- storage ----------

def test_the_start_date_is_saved_and_reloaded():
    added = add_subscription({**VALID, "start_date": "2025-03-10"})

    assert added["start_date"] == "2025-03-10"
    assert added["cancelled_date"] is None
    assert load_subscriptions() == [added]


def test_a_subscription_added_without_a_start_date_has_none():
    assert add_subscription(VALID)["start_date"] is None
    assert load_subscriptions()[0].get("start_date") is None


def test_the_cancellation_date_is_saved_and_reloaded():
    added = add_subscription({**VALID, "start_date": "2025-03-10"})
    subscriptions = load_subscriptions()
    cancel_subscription(find_subscription(subscriptions, added["id"]), date(2026, 11, 24))

    save_subscriptions(subscriptions)

    saved = load_subscriptions()[0]
    assert (saved["status"], saved["start_date"], saved["cancelled_date"]) == ("cancelled", "2025-03-10", "2026-11-24")


def test_both_dates_are_among_the_saved_values():
    row = storage._row({"id": "x", "start_date": "2025-03-10", "cancelled_date": "2026-11-24"})

    assert (row["start_date"], row["cancelled_date"]) == ("2025-03-10", "2026-11-24")
    assert (storage._row({"id": "x"})["start_date"], storage._row({"id": "x"})["cancelled_date"]) == (None, None)


@needs_database
def test_a_table_from_before_these_dates_gets_the_new_columns_and_keeps_its_rows():
    import psycopg

    old_table = """
        CREATE TABLE subscriptions (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, amount DOUBLE PRECISION NOT NULL, billing_cycle TEXT NOT NULL,
            next_renewal_date TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'Other', status TEXT NOT NULL DEFAULT 'active',
            remind_days_before INTEGER, reminded_for_date TEXT, remind_repeat_days INTEGER, last_reminder_on TEXT,
            send_hour INTEGER, time_zone TEXT, reminders_paused BOOLEAN NOT NULL DEFAULT FALSE, remind_until TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DROP TABLE subscriptions")
        conn.execute(old_table)
        conn.execute(
            "INSERT INTO subscriptions (id, name, amount, billing_cycle, next_renewal_date, status)"
            " VALUES ('old', 'Old', 5, 'monthly', '2099-01-01', 'cancelled')"
        )

    loaded = load_subscriptions()

    assert [(s["id"], s["name"], s["status"]) for s in loaded] == [("old", "Old", "cancelled")]
    assert (loaded[0]["start_date"], loaded[0]["cancelled_date"]) == (None, None)
    loaded[0]["start_date"] = "2025-01-01"
    loaded[0]["cancelled_date"] = "2026-06-30"
    save_subscriptions(loaded)
    saved = load_subscriptions()[0]
    assert (saved["start_date"], saved["cancelled_date"]) == ("2025-01-01", "2026-06-30")
