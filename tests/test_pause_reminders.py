"""
Pausing and resuming a subscription's reminders. The hourly job sends nothing for a paused subscription and
does not mark it as reminded, so resuming it sends whatever is due on the next run. The flag is kept by the
API and by both kinds of storage, and taking the reminder away clears it.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
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

needs_database = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="runs against PostgreSQL when DATABASE_URL is set")


def utc(month: int, day: int, hour: int = 9) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=timezone.utc)


def sub(**changes) -> dict:
    return {**REMINDER, "id": "x", "status": "active", "reminded_for_date": None, **changes}


def set_paused(sub_id: str, paused: bool) -> None:
    subscriptions = load_subscriptions()
    next(s for s in subscriptions if s["id"] == sub_id)["reminders_paused"] = paused
    save_subscriptions(subscriptions)


def run_every_hour(start: datetime, hours: int) -> list:
    """Runs the job once an hour, the way Render does, and returns the UTC time of every email sent."""
    sent_at = []
    for step in range(hours):
        now = start + timedelta(hours=step)
        run_reminders(now=now, send=lambda subject, body, now=now: sent_at.append(now))
    return sent_at


# ---------- who is due ----------

def test_a_paused_subscription_is_not_due_inside_its_window():
    assert reminders_due([sub(reminders_paused=True)], now=utc(11, 24)) == []
    assert reminders_due([sub(reminders_paused=False)], now=utc(11, 24)) != []


@pytest.mark.parametrize("flag", [False, None, "missing"])
def test_a_subscription_that_is_not_paused_is_due(flag):
    one = sub()
    if flag != "missing":
        one["reminders_paused"] = flag
    assert reminders_due([one], now=utc(11, 24)) == [one]


def test_a_paused_subscription_does_not_hold_back_the_others():
    paused = sub(id="a", reminders_paused=True)
    running = sub(id="b")
    assert [s["id"] for s in reminders_due([paused, running], now=utc(11, 24))] == ["b"]


def test_a_paused_repeat_is_not_due_even_after_its_interval_has_passed():
    repeating = sub(remind_repeat_days=2, reminded_for_date="2026-12-01", last_reminder_on="2026-11-24", reminders_paused=True)
    assert reminders_due([repeating], now=utc(11, 28)) == []
    repeating["reminders_paused"] = False
    assert reminders_due([repeating], now=utc(11, 28)) == [repeating]


# ---------- the hourly job ----------

def test_the_job_sends_nothing_for_a_paused_subscription_and_leaves_it_unmarked():
    created = add_subscription(REMINDER)
    set_paused(created["id"], True)
    sent = []

    assert run_reminders(now=utc(11, 24), send=lambda subject, body: sent.append(subject)) == 0

    assert sent == []
    saved = load_subscriptions()[0]
    assert saved["reminders_paused"] is True
    assert saved["reminded_for_date"] is None
    assert saved["last_reminder_on"] is None


def test_resuming_inside_the_window_sends_the_reminder_on_the_next_run():
    created = add_subscription(REMINDER)
    set_paused(created["id"], True)
    assert run_every_hour(utc(11, 24, 0), hours=48) == []

    set_paused(created["id"], False)

    assert run_every_hour(utc(11, 26, 0), hours=24) == [utc(11, 26, 9)]


def test_a_reminder_that_was_already_sent_is_not_sent_again_after_a_pause():
    created = add_subscription(REMINDER)
    assert run_every_hour(utc(11, 24, 0), hours=24) == [utc(11, 24, 9)]
    set_paused(created["id"], True)
    set_paused(created["id"], False)

    assert run_every_hour(utc(11, 25, 0), hours=24) == []


def test_a_pause_in_the_middle_of_a_repeat_sends_one_email_after_it_not_a_catch_up():
    created = add_subscription({**REMINDER, "remind_repeat_days": 2})
    assert run_every_hour(utc(11, 24, 0), hours=24) == [utc(11, 24, 9)]
    set_paused(created["id"], True)
    assert run_every_hour(utc(11, 25, 0), hours=96) == []

    set_paused(created["id"], False)

    assert run_every_hour(utc(11, 29, 0), hours=24) == [utc(11, 29, 9)]


def test_pausing_one_subscription_does_not_stop_the_job_for_another():
    paused = add_subscription({**REMINDER, "name": "Paused"})
    add_subscription({**REMINDER, "name": "Running"})
    set_paused(paused["id"], True)
    subjects = []

    assert run_reminders(now=utc(11, 24), send=lambda subject, body: subjects.append(subject)) == 1

    assert subjects == ["Reminder: Running renews on 2026-12-01"]


# ---------- the API ----------

@pytest.fixture
def client():
    test_client = app_module.app.test_client()
    with test_client.session_transaction() as session:
        session["logged_in"] = True
    return test_client


def create(client, **changes) -> str:
    return client.post("/api/subscriptions", json={**FAR, **changes}).get_json()["id"]


def test_a_new_subscription_starts_with_its_reminders_running(client):
    created = client.post("/api/subscriptions", json=FAR).get_json()

    assert created["reminders_paused"] is False


def test_pausing_and_resuming_flip_the_flag_and_it_is_kept(client):
    sub_id = create(client)

    paused = client.post(f"/api/subscriptions/{sub_id}/pause")
    assert paused.status_code == 200
    assert paused.get_json()["reminders_paused"] is True
    assert client.get("/api/subscriptions").get_json()[0]["reminders_paused"] is True
    assert load_subscriptions()[0]["reminders_paused"] is True

    resumed = client.post(f"/api/subscriptions/{sub_id}/resume")
    assert resumed.status_code == 200
    assert resumed.get_json()["reminders_paused"] is False
    assert client.get("/api/subscriptions").get_json()[0]["reminders_paused"] is False
    assert load_subscriptions()[0]["reminders_paused"] is False


def test_pausing_twice_or_resuming_when_not_paused_is_fine(client):
    sub_id = create(client)

    assert client.post(f"/api/subscriptions/{sub_id}/resume").get_json()["reminders_paused"] is False
    assert client.post(f"/api/subscriptions/{sub_id}/pause").status_code == 200
    assert client.post(f"/api/subscriptions/{sub_id}/pause").get_json()["reminders_paused"] is True


def test_pausing_changes_nothing_else_and_leaves_the_other_subscriptions_alone(client):
    sub_id = create(client, remind_repeat_days=2)
    other_id = create(client, name="Spotify")
    before = {s["id"]: s for s in client.get("/api/subscriptions").get_json()}

    paused = client.post(f"/api/subscriptions/{sub_id}/pause").get_json()

    assert {**paused, "reminders_paused": False} == before[sub_id]
    assert client.get("/api/subscriptions").get_json()[1] == before[other_id]


def test_an_unknown_subscription_is_not_found(client):
    for action in ("pause", "resume"):
        response = client.post(f"/api/subscriptions/nope/{action}")
        assert response.status_code == 404
        assert response.get_json() == {"error": "Subscription not found"}


def test_a_subscription_with_no_reminder_cannot_be_paused(client):
    sub_id = create(client, remind_days_before="")

    response = client.post(f"/api/subscriptions/{sub_id}/pause")

    assert response.status_code == 400
    assert response.get_json() == {"error": "Set a reminder before pausing it"}
    assert load_subscriptions()[0]["reminders_paused"] is False
    assert client.post(f"/api/subscriptions/{sub_id}/resume").status_code == 200


def test_the_routes_only_answer_post(client):
    sub_id = create(client)

    assert client.get(f"/api/subscriptions/{sub_id}/pause").status_code == 405
    assert client.get(f"/api/subscriptions/{sub_id}/resume").status_code == 405


def test_pausing_needs_a_login_and_changes_nothing_without_one():
    anonymous = app_module.app.test_client()
    created = add_subscription(FAR)

    assert anonymous.post(f"/api/subscriptions/{created['id']}/pause").status_code == 401
    assert load_subscriptions()[0]["reminders_paused"] is False


def test_pausing_counts_as_a_change_for_the_write_limit(client, monkeypatch):
    import limits

    sub_id = create(client)
    limits._recent_requests.clear()
    monkeypatch.setenv("WRITE_LIMIT_PER_MINUTE", "1")
    codes = [client.post(f"/api/subscriptions/{sub_id}/pause").status_code for _ in range(2)]
    limits._recent_requests.clear()

    assert codes == [200, 429]


def test_taking_the_reminder_away_clears_the_pause(client):
    sub_id = create(client)
    client.post(f"/api/subscriptions/{sub_id}/pause")

    cleared = client.post(f"/api/subscriptions/{sub_id}/reminder", json={"days": None}).get_json()
    assert cleared["reminders_paused"] is False

    again = client.post(f"/api/subscriptions/{sub_id}/reminder", json={"days": 7}).get_json()
    assert again["reminders_paused"] is False


def test_changing_the_reminder_settings_keeps_it_paused(client):
    sub_id = create(client)
    client.post(f"/api/subscriptions/{sub_id}/pause")

    changed = client.post(f"/api/subscriptions/{sub_id}/reminder", json={"days": 14, "send_hour": 18}).get_json()

    assert changed["remind_days_before"] == 14
    assert changed["send_hour"] == 18
    assert changed["reminders_paused"] is True


# ---------- storage ----------

def test_the_paused_flag_is_saved_and_reloaded():
    created = add_subscription(BASE)
    assert created["reminders_paused"] is False
    assert load_subscriptions()[0]["reminders_paused"] is False

    set_paused(created["id"], True)
    assert load_subscriptions()[0]["reminders_paused"] is True

    set_paused(created["id"], False)
    assert load_subscriptions()[0]["reminders_paused"] is False


def test_a_subscription_saved_without_the_flag_is_saved_as_not_paused():
    row = storage._row({"id": "x"})

    assert set(row) == set(storage.COLUMNS)
    assert row["reminders_paused"] is False
    assert storage._row({"id": "x", "reminders_paused": None})["reminders_paused"] is False
    assert storage._row({"id": "x", "reminders_paused": True})["reminders_paused"] is True
    assert row["name"] is None


@needs_database
def test_a_table_from_before_pausing_gets_the_new_column_and_keeps_its_rows():
    import psycopg

    old_table = """
        CREATE TABLE subscriptions (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, amount DOUBLE PRECISION NOT NULL, billing_cycle TEXT NOT NULL,
            next_renewal_date TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'Other', status TEXT NOT NULL DEFAULT 'active',
            remind_days_before INTEGER, reminded_for_date TEXT, remind_repeat_days INTEGER, last_reminder_on TEXT,
            send_hour INTEGER, time_zone TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DROP TABLE subscriptions")
        conn.execute(old_table)
        conn.execute(
            "INSERT INTO subscriptions (id, name, amount, billing_cycle, next_renewal_date, remind_days_before)"
            " VALUES ('old', 'Old', 5, 'monthly', '2099-01-01', 3)"
        )

    loaded = load_subscriptions()

    assert [(s["id"], s["name"], s["remind_days_before"]) for s in loaded] == [("old", "Old", 3)]
    assert loaded[0]["reminders_paused"] is False
    set_paused("old", True)
    assert load_subscriptions()[0]["reminders_paused"] is True
