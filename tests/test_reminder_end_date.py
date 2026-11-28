"""
The end date for reminders (remind_until). It is the last day a subscription may be emailed, as a date in the
subscription's own time zone: its emails still go out on that day and none go out after it, for this renewal
or any later one. The API checks it, and both kinds of storage keep it.
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import app as app_module
import storage
from reminders import reminders_due, run_reminders
from storage import add_subscription, load_subscriptions, save_subscriptions

BASE = {"name": "Netflix", "amount": 15.99, "billing_cycle": "monthly", "next_renewal_date": "2026-12-01", "category": "Video"}
REMINDER = {**BASE, "remind_days_before": 7, "send_hour": 9, "time_zone": "UTC"}
# The API tests use a renewal far in the future, so the list never moves it while a test runs.
FAR = {**REMINDER, "next_renewal_date": "2099-12-01"}
BAD_FORMAT = "remind_until must be a date in YYYY-MM-DD format"
NEEDS_REMINDER = "Set a reminder before choosing an end date"
IN_THE_PAST = "remind_until must be today or a later date"

needs_database = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="runs against PostgreSQL when DATABASE_URL is set")


def utc(month: int, day: int, hour: int = 9) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=timezone.utc)


def sub(**changes) -> dict:
    return {**REMINDER, "id": "x", "status": "active", "reminded_for_date": None, **changes}


def run_every_hour(start: datetime, hours: int) -> list:
    """Runs the job once an hour, the way Render does, and returns the UTC time of every email sent."""
    sent_at = []
    for step in range(hours):
        now = start + timedelta(hours=step)
        run_reminders(now=now, send=lambda subject, body, now=now: sent_at.append(now))
    return sent_at


# ---------- who is due ----------

def test_reminders_go_out_up_to_and_including_the_end_date():
    ending = sub(remind_until="2026-11-28")

    assert reminders_due([ending], now=utc(11, 28)) == [ending]
    assert reminders_due([ending], now=utc(11, 29)) == []


@pytest.mark.parametrize("end", [None, "", "missing"])
def test_a_subscription_with_no_end_date_keeps_getting_reminders(end):
    one = sub()
    if end != "missing":
        one["remind_until"] = end

    assert reminders_due([one], now=utc(11, 24)) == [one]
    assert reminders_due([one], now=utc(12, 1)) == [one]


def test_the_end_date_is_the_date_in_the_subscriptions_own_time_zone_ahead_of_utc():
    auckland = sub(time_zone="Pacific/Auckland", remind_until="2026-11-24")

    assert reminders_due([auckland], now=utc(11, 24, 7)) == [auckland]  # 20:00 on the 24th in Auckland
    assert reminders_due([auckland], now=utc(11, 24, 22)) == []  # already the 25th in Auckland
    assert reminders_due([{**auckland, "remind_until": "2026-11-25"}], now=utc(11, 24, 22)) != []


def test_the_end_date_is_the_date_in_the_subscriptions_own_time_zone_behind_utc():
    honolulu = sub(time_zone="Pacific/Honolulu", remind_until="2026-11-24")

    assert reminders_due([honolulu], now=utc(11, 25, 3)) == [honolulu]  # 17:00 on the 24th in Honolulu
    assert reminders_due([{**honolulu, "remind_until": "2026-11-23"}], now=utc(11, 25, 3)) == []


def test_an_old_subscription_with_no_time_zone_uses_utc_for_the_end_date():
    legacy = sub(time_zone=None, send_hour=None, remind_until="2026-11-24")

    assert reminders_due([legacy], now=utc(11, 24, 13)) == [legacy]
    assert reminders_due([legacy], now=utc(11, 25, 13)) == []


def test_the_end_date_stops_repeats_too():
    repeating = sub(remind_repeat_days=2, reminded_for_date="2026-12-01", last_reminder_on="2026-11-24", remind_until="2026-11-25")

    assert reminders_due([repeating], now=utc(11, 26)) == []
    repeating["remind_until"] = "2026-11-26"
    assert reminders_due([repeating], now=utc(11, 26)) == [repeating]


def test_an_end_date_that_has_passed_also_stops_the_next_renewal():
    after_the_end = sub(next_renewal_date="2027-01-01", remind_until="2026-11-28", reminded_for_date="2026-12-01")

    assert reminders_due([after_the_end], now=utc(12, 28)) == []
    after_the_end["remind_until"] = None
    assert reminders_due([after_the_end], now=utc(12, 28)) == [after_the_end]


def test_a_subscription_past_its_end_date_does_not_hold_back_the_others():
    ended = sub(id="a", remind_until="2026-11-20")
    running = sub(id="b", remind_until="2026-12-31")
    no_end = sub(id="c")

    assert [s["id"] for s in reminders_due([ended, running, no_end], now=utc(11, 24))] == ["b", "c"]


# ---------- the hourly job ----------

def test_the_job_sends_its_last_email_on_the_end_date_and_none_after_it():
    add_subscription({**REMINDER, "remind_repeat_days": 2, "remind_until": "2026-11-28"})

    assert run_every_hour(utc(11, 24, 0), hours=24 * 9) == [utc(11, 24, 9), utc(11, 26, 9), utc(11, 28, 9)]


def test_a_subscription_past_its_end_date_is_left_exactly_as_it_was():
    created = add_subscription({**REMINDER, "remind_until": "2026-11-20"})
    sent = []

    assert run_reminders(now=utc(11, 24), send=lambda subject, body: sent.append(subject)) == 0

    assert sent == []
    saved = load_subscriptions()[0]
    assert saved["id"] == created["id"]
    assert saved["remind_until"] == "2026-11-20"
    assert saved["reminded_for_date"] is None
    assert saved["last_reminder_on"] is None


def set_paused(sub_id: str, paused: bool) -> None:
    subscriptions = load_subscriptions()
    next(s for s in subscriptions if s["id"] == sub_id)["reminders_paused"] = paused
    save_subscriptions(subscriptions)


def test_pausing_does_not_extend_the_end_date():
    created = add_subscription({**REMINDER, "remind_until": "2026-11-26"})
    set_paused(created["id"], True)
    assert run_every_hour(utc(11, 24, 0), hours=72) == []

    set_paused(created["id"], False)

    assert run_every_hour(utc(11, 27, 0), hours=24) == []
    assert load_subscriptions()[0]["reminded_for_date"] is None


def test_ending_one_subscription_does_not_stop_the_job_for_another():
    add_subscription({**REMINDER, "name": "Ended", "remind_until": "2026-11-20"})
    add_subscription({**REMINDER, "name": "Running"})
    subjects = []

    assert run_reminders(now=utc(11, 24), send=lambda subject, body: subjects.append(subject)) == 1

    assert subjects == ["Reminder: Running renews on 2026-12-01"]


# ---------- the end date as the API accepts it ----------

class FixedClock(datetime):
    """The moment is 23:30 UTC on 24 Nov 2026: already the 25th in Auckland and still the 24th in Los Angeles."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 24, 23, 30, tzinfo=timezone.utc).astimezone(tz)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "datetime", FixedClock)
    test_client = app_module.app.test_client()
    with test_client.session_transaction() as session:
        session["logged_in"] = True
    return test_client


def create(client, **changes):
    return client.post("/api/subscriptions", json={**FAR, **changes})


def set_reminder(client, sub_id, **body):
    return client.post(f"/api/subscriptions/{sub_id}/reminder", json=body)


def test_today_is_read_in_the_zone_it_is_asked_for(monkeypatch):
    monkeypatch.setattr(app_module, "datetime", FixedClock)

    assert app_module.today_in("Pacific/Auckland") == date(2026, 11, 25)
    assert app_module.today_in("America/Los_Angeles") == date(2026, 11, 24)
    assert app_module.today_in("UTC") == date(2026, 11, 24)
    assert app_module.today_in(None) == date(2026, 11, 24)


def test_a_new_subscription_can_have_an_end_date(client):
    created = create(client, remind_until="2026-12-31")

    assert created.status_code == 201
    assert created.get_json()["remind_until"] == "2026-12-31"
    assert load_subscriptions()[0]["remind_until"] == "2026-12-31"
    assert client.get("/api/subscriptions").get_json()[0]["remind_until"] == "2026-12-31"


@pytest.mark.parametrize("blank", ["", "   ", None, "missing"])
def test_a_new_subscription_without_an_end_date_has_none(client, blank):
    changes = {} if blank == "missing" else {"remind_until": blank}

    created = create(client, **changes)

    assert created.status_code == 201
    assert created.get_json()["remind_until"] is None


def test_spaces_around_the_date_are_dropped(client):
    assert create(client, remind_until="  2026-12-31 ").get_json()["remind_until"] == "2026-12-31"


@pytest.mark.parametrize("bad", [
    "2026-13-01", "2026-02-30", "0000-01-01", "12/31/2026", "20261231", "2026-12-31T10:00", "2026-12-31\n2026-12-31",
    "2026-1-5", "26-12-31", "tomorrow", "٢٠٢٦-١٢-٣١", 20261231, 0, True, ["2026-12-31"], {"date": "2026-12-31"},
])
def test_a_date_that_is_not_yyyy_mm_dd_is_refused_and_nothing_is_saved(client, bad):
    response = create(client, remind_until=bad)

    assert response.status_code == 400
    assert response.get_json() == {"error": BAD_FORMAT}
    assert load_subscriptions() == []


def test_an_end_date_needs_a_reminder(client):
    response = create(client, remind_days_before="", remind_until="2026-12-31")

    assert response.status_code == 400
    assert response.get_json() == {"error": NEEDS_REMINDER}
    assert load_subscriptions() == []


def test_a_date_that_has_already_passed_is_refused(client):
    response = create(client, remind_until="2026-11-23")

    assert response.status_code == 400
    assert response.get_json() == {"error": IN_THE_PAST}
    assert load_subscriptions() == []


def test_today_is_allowed_as_the_end_date(client):
    assert create(client, remind_until="2026-11-24").status_code == 201


def test_today_is_judged_in_the_subscriptions_time_zone(client):
    ahead = create(client, time_zone="Pacific/Auckland", remind_until="2026-11-24")
    behind = create(client, time_zone="America/Los_Angeles", remind_until="2026-11-24")

    assert ahead.status_code == 400
    assert ahead.get_json() == {"error": IN_THE_PAST}
    assert behind.status_code == 201
    assert [s["time_zone"] for s in load_subscriptions()] == ["America/Los_Angeles"]


def test_a_subscription_with_no_time_zone_is_judged_in_utc(client):
    assert create(client, time_zone="", remind_until="2026-11-23").status_code == 400
    assert create(client, time_zone="", remind_until="2026-11-24").status_code == 201


# ---------- changing it on a subscription that exists ----------

def test_setting_the_end_date_saves_it_and_returns_it(client):
    sub_id = create(client).get_json()["id"]

    response = set_reminder(client, sub_id, days=7, until="2026-12-31")

    assert response.status_code == 200
    assert response.get_json()["remind_until"] == "2026-12-31"
    assert load_subscriptions()[0]["remind_until"] == "2026-12-31"


def test_a_request_without_until_keeps_the_end_date(client):
    sub_id = create(client, remind_until="2026-12-31").get_json()["id"]

    changed = set_reminder(client, sub_id, days=14, send_hour=18).get_json()

    assert changed["remind_days_before"] == 14
    assert changed["remind_until"] == "2026-12-31"
    assert load_subscriptions()[0]["remind_until"] == "2026-12-31"


@pytest.mark.parametrize("blank", [None, "", "  "])
def test_a_blank_until_removes_the_end_date(client, blank):
    sub_id = create(client, remind_until="2026-12-31").get_json()["id"]

    response = set_reminder(client, sub_id, days=7, until=blank)

    assert response.status_code == 200
    assert response.get_json()["remind_until"] is None
    assert load_subscriptions()[0]["remind_until"] is None


def test_changing_the_end_date_replaces_it(client):
    sub_id = create(client, remind_until="2026-12-31").get_json()["id"]

    assert set_reminder(client, sub_id, days=7, until="2027-03-01").get_json()["remind_until"] == "2027-03-01"


def test_turning_the_reminder_off_removes_the_end_date_and_it_does_not_come_back(client):
    sub_id = create(client, remind_until="2026-12-31").get_json()["id"]

    off = set_reminder(client, sub_id, days=None, repeat_days=None).get_json()
    assert off["remind_days_before"] is None
    assert off["remind_until"] is None

    again = set_reminder(client, sub_id, days=7).get_json()
    assert again["remind_until"] is None


def test_an_end_date_without_a_reminder_is_refused_and_nothing_changes(client):
    sub_id = create(client, remind_until="2026-12-31").get_json()["id"]

    response = set_reminder(client, sub_id, days=None, until="2027-01-31")

    assert response.status_code == 400
    assert response.get_json() == {"error": NEEDS_REMINDER}
    saved = load_subscriptions()[0]
    assert (saved["remind_days_before"], saved["remind_until"]) == (7, "2026-12-31")


def test_a_bad_date_is_refused_and_nothing_else_in_the_request_is_saved(client):
    sub_id = create(client, remind_until="2026-12-31").get_json()["id"]

    response = set_reminder(client, sub_id, days=3, send_hour=20, until="31 December")

    assert response.status_code == 400
    assert response.get_json() == {"error": BAD_FORMAT}
    saved = load_subscriptions()[0]
    assert (saved["remind_days_before"], saved["send_hour"], saved["remind_until"]) == (7, 9, "2026-12-31")


def test_a_new_date_that_has_passed_is_refused_and_nothing_changes(client):
    sub_id = create(client, remind_until="2026-12-31").get_json()["id"]

    response = set_reminder(client, sub_id, days=3, until="2026-11-23")

    assert response.status_code == 400
    assert response.get_json() == {"error": IN_THE_PAST}
    saved = load_subscriptions()[0]
    assert (saved["remind_days_before"], saved["remind_until"]) == (7, "2026-12-31")


def test_an_end_date_that_passed_after_it_was_saved_does_not_block_other_changes(client):
    sub_id = create(client).get_json()["id"]
    subscriptions = load_subscriptions()
    subscriptions[0]["remind_until"] = "2020-01-01"
    save_subscriptions(subscriptions)

    kept = set_reminder(client, sub_id, days=5, until="2020-01-01")
    assert kept.status_code == 200
    assert kept.get_json()["remind_until"] == "2020-01-01"

    untouched = set_reminder(client, sub_id, days=5)
    assert untouched.status_code == 200
    assert untouched.get_json()["remind_until"] == "2020-01-01"

    another_old_date = set_reminder(client, sub_id, days=5, until="2020-01-02")
    assert another_old_date.status_code == 400
    assert load_subscriptions()[0]["remind_until"] == "2020-01-01"

    cleared = set_reminder(client, sub_id, days=5, until=None)
    assert cleared.status_code == 200
    assert cleared.get_json()["remind_until"] is None


def test_the_time_zone_in_the_same_request_decides_what_today_is(client):
    sub_id = create(client).get_json()["id"]

    refused = set_reminder(client, sub_id, days=7, until="2026-11-24", time_zone="Pacific/Auckland")
    assert refused.status_code == 400
    assert refused.get_json() == {"error": IN_THE_PAST}

    accepted = set_reminder(client, sub_id, days=7, until="2026-11-24", time_zone="America/Los_Angeles")
    assert accepted.status_code == 200


def test_the_subscriptions_saved_time_zone_decides_when_the_request_has_none(client):
    sub_id = create(client, time_zone="Pacific/Auckland").get_json()["id"]

    assert set_reminder(client, sub_id, days=7, until="2026-11-24").status_code == 400
    assert set_reminder(client, sub_id, days=7, until="2026-11-25").status_code == 200


def test_changing_only_the_end_date_does_not_make_the_reminder_fire_again(client):
    sub_id = create(client).get_json()["id"]
    subscriptions = load_subscriptions()
    subscriptions[0]["reminded_for_date"] = FAR["next_renewal_date"]
    subscriptions[0]["last_reminder_on"] = "2099-11-24"
    save_subscriptions(subscriptions)

    set_reminder(client, sub_id, days=7, until="2099-11-30")

    saved = load_subscriptions()[0]
    assert saved["reminded_for_date"] == FAR["next_renewal_date"]
    assert saved["last_reminder_on"] == "2099-11-24"


def test_an_unknown_subscription_is_still_not_found(client):
    response = set_reminder(client, "nope", days=7, until="2026-12-31")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Subscription not found"}


# ---------- storage ----------

def test_the_end_date_is_saved_and_reloaded():
    created = add_subscription({**REMINDER, "remind_until": "2026-12-31"})
    assert created["remind_until"] == "2026-12-31"
    assert load_subscriptions()[0]["remind_until"] == "2026-12-31"

    subscriptions = load_subscriptions()
    subscriptions[0]["remind_until"] = None
    save_subscriptions(subscriptions)
    assert load_subscriptions()[0]["remind_until"] is None


def test_a_subscription_added_without_an_end_date_has_none():
    assert add_subscription(BASE)["remind_until"] is None
    assert load_subscriptions()[0].get("remind_until") is None


def test_the_end_date_is_one_of_the_saved_values():
    assert storage._row({"id": "x"})["remind_until"] is None
    assert storage._row({"id": "x", "remind_until": "2026-12-31"})["remind_until"] == "2026-12-31"


@needs_database
def test_a_table_from_before_end_dates_gets_the_new_column_and_keeps_its_rows():
    import psycopg

    old_table = """
        CREATE TABLE subscriptions (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, amount DOUBLE PRECISION NOT NULL, billing_cycle TEXT NOT NULL,
            next_renewal_date TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'Other', status TEXT NOT NULL DEFAULT 'active',
            remind_days_before INTEGER, reminded_for_date TEXT, remind_repeat_days INTEGER, last_reminder_on TEXT,
            send_hour INTEGER, time_zone TEXT, reminders_paused BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DROP TABLE subscriptions")
        conn.execute(old_table)
        conn.execute(
            "INSERT INTO subscriptions (id, name, amount, billing_cycle, next_renewal_date, remind_days_before, reminders_paused)"
            " VALUES ('old', 'Old', 5, 'monthly', '2099-01-01', 3, TRUE)"
        )

    loaded = load_subscriptions()

    assert [(s["id"], s["name"], s["remind_days_before"], s["reminders_paused"]) for s in loaded] == [("old", "Old", 3, True)]
    assert loaded[0]["remind_until"] is None
    loaded[0]["remind_until"] = "2099-02-01"
    save_subscriptions(loaded)
    assert load_subscriptions()[0]["remind_until"] == "2099-02-01"
