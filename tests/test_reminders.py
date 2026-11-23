import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import app as app_module
from reminders import reminders_due, run_reminders
from storage import add_subscription, load_subscriptions

TODAY = date(2026, 11, 21)
BASE = {"name": "Netflix", "amount": 15.99, "billing_cycle": "monthly", "next_renewal_date": "2026-12-01", "category": "Video"}
LAGOS = "Africa/Lagos"  # UTC+1 all year
NEW_YORK = "America/New_York"  # UTC-5 in late November


def utc(day: date, hour: int = 13, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


def sub(**changes):
    return {**BASE, "id": "x", "status": "active", "remind_days_before": 7, "reminded_for_date": None, **changes}


def test_reminder_is_due_inside_the_window():
    assert reminders_due([sub()], now=utc(date(2026, 11, 24))) != []  # 7 days before


def test_reminder_is_not_due_before_the_window():
    assert reminders_due([sub()], now=utc(date(2026, 11, 23))) == []


def test_reminder_is_due_on_renewal_day_if_missed_earlier():
    assert reminders_due([sub()], now=utc(date(2026, 12, 1))) != []


def test_reminder_is_not_due_after_renewal_day():
    assert reminders_due([sub()], now=utc(date(2026, 12, 2))) == []


def test_no_reminder_when_none_is_set():
    assert reminders_due([sub(remind_days_before=None)], now=utc(TODAY)) == []


def test_cancelled_subscriptions_are_skipped():
    assert reminders_due([sub(status="cancelled")], now=utc(date(2026, 11, 26))) == []


def test_already_reminded_for_this_renewal_is_skipped():
    assert reminders_due([sub(reminded_for_date="2026-12-01")], now=utc(date(2026, 11, 26))) == []


def test_reminder_rearms_after_renewal_moves_the_date():
    renewed = sub(reminded_for_date="2026-12-01", next_renewal_date="2027-01-01")
    assert reminders_due([renewed], now=utc(date(2026, 12, 27))) != []


def test_zero_days_means_reminder_on_renewal_day_only():
    assert reminders_due([sub(remind_days_before=0)], now=utc(date(2026, 11, 30))) == []
    assert reminders_due([sub(remind_days_before=0)], now=utc(date(2026, 12, 1))) != []


def test_run_reminders_emails_once_and_records_it():
    add_subscription({**BASE, "remind_days_before": 7})
    sent = []
    fake_send = lambda subject, body: sent.append(subject)

    assert run_reminders(now=utc(date(2026, 11, 24)), send=fake_send) == 1
    assert run_reminders(now=utc(date(2026, 11, 25)), send=fake_send) == 0
    assert len(sent) == 1
    assert load_subscriptions()[0]["reminded_for_date"] == "2026-12-01"


def test_run_reminders_moves_a_passed_renewal_forward_before_reminding():
    add_subscription({**BASE, "next_renewal_date": "2026-11-01", "remind_days_before": 30})
    sent = []

    assert run_reminders(now=utc(date(2026, 11, 22)), send=lambda subject, body: sent.append(subject)) == 1
    saved = load_subscriptions()[0]
    assert saved["next_renewal_date"] == "2026-12-01"
    assert saved["reminded_for_date"] == "2026-12-01"


@pytest.fixture
def client():
    test_client = app_module.app.test_client()
    with test_client.session_transaction() as session:
        session["logged_in"] = True
    return test_client


def test_api_saves_a_reminder_when_creating(client):
    response = client.post("/api/subscriptions", json={**BASE, "remind_days_before": "7"})
    assert response.status_code == 201
    assert response.get_json()["remind_days_before"] == 7


def test_api_blank_reminder_means_none(client):
    response = client.post("/api/subscriptions", json={**BASE, "remind_days_before": ""})
    assert response.get_json()["remind_days_before"] is None


@pytest.mark.parametrize("value", ["-1", "366", "soon", "1.5"])
def test_api_rejects_bad_reminder_days(client, value):
    response = client.post("/api/subscriptions", json={**BASE, "remind_days_before": value})
    assert response.status_code == 400


def test_api_can_change_a_reminder_later(client):
    sub_id = client.post("/api/subscriptions", json=BASE).get_json()["id"]
    changed = client.post(f"/api/subscriptions/{sub_id}/reminder", json={"days": 30}).get_json()
    assert changed["remind_days_before"] == 30

    cleared = client.post(f"/api/subscriptions/{sub_id}/reminder", json={"days": None}).get_json()
    assert cleared["remind_days_before"] is None


def test_repeat_sends_again_after_the_interval():
    repeating = sub(remind_days_before=7, remind_repeat_days=2, reminded_for_date="2026-12-01", last_reminder_on="2026-11-24")
    assert reminders_due([repeating], now=utc(date(2026, 11, 25))) == []
    assert reminders_due([repeating], now=utc(date(2026, 11, 26))) != []


def test_repeat_stops_once_the_renewal_has_passed():
    repeating = sub(remind_days_before=7, remind_repeat_days=2, reminded_for_date="2026-12-01", last_reminder_on="2026-11-30")
    assert reminders_due([repeating], now=utc(date(2026, 12, 2))) == []


def test_no_repeat_when_no_interval_is_set():
    once = sub(remind_days_before=7, reminded_for_date="2026-12-01", last_reminder_on="2026-11-24")
    assert reminders_due([once], now=utc(date(2026, 11, 30))) == []


def test_repeat_still_waits_for_the_first_reminder():
    assert reminders_due([sub(remind_days_before=7, remind_repeat_days=2)], now=utc(date(2026, 11, 24))) != []


def test_run_reminders_repeats_until_renewal():
    add_subscription({**BASE, "remind_days_before": 7, "remind_repeat_days": 2})
    sent = []
    fake_send = lambda subject, body: sent.append(subject)

    assert run_reminders(now=utc(date(2026, 11, 24)), send=fake_send) == 1  # first email
    assert run_reminders(now=utc(date(2026, 11, 25)), send=fake_send) == 0  # too soon
    assert run_reminders(now=utc(date(2026, 11, 26)), send=fake_send) == 1  # repeat
    assert len(sent) == 2
    assert load_subscriptions()[0]["last_reminder_on"] == "2026-11-26"


def test_api_saves_a_repeat_interval(client):
    response = client.post("/api/subscriptions", json={**BASE, "remind_days_before": "7", "remind_repeat_days": "2"})
    assert response.status_code == 201
    assert response.get_json()["remind_repeat_days"] == 2


def test_api_repeat_needs_a_first_reminder(client):
    response = client.post("/api/subscriptions", json={**BASE, "remind_repeat_days": "2"})
    assert response.status_code == 400
    assert "Set a reminder before" in response.get_json()["error"]


@pytest.mark.parametrize("value", ["-1", "366", "soon"])
def test_api_rejects_bad_repeat_days(client, value):
    response = client.post("/api/subscriptions", json={**BASE, "remind_days_before": "7", "remind_repeat_days": value})
    assert response.status_code == 400


def test_api_blank_or_zero_repeat_means_none(client):
    for value in ("", "0"):
        response = client.post("/api/subscriptions", json={**BASE, "remind_days_before": "7", "remind_repeat_days": value})
        assert response.get_json()["remind_repeat_days"] is None


def test_api_changing_the_reminder_keeps_the_repeat(client):
    sub_id = client.post("/api/subscriptions", json={**BASE, "remind_days_before": "7", "remind_repeat_days": "2"}).get_json()["id"]
    changed = client.post(f"/api/subscriptions/{sub_id}/reminder", json={"days": 14}).get_json()
    assert changed["remind_days_before"] == 14
    assert changed["remind_repeat_days"] == 2


def test_api_sets_a_repeat_on_an_existing_reminder(client):
    sub_id = client.post("/api/subscriptions", json={**BASE, "remind_days_before": "7"}).get_json()["id"]
    changed = client.post(f"/api/subscriptions/{sub_id}/reminder", json={"days": 7, "repeat_days": 3}).get_json()
    assert changed["remind_repeat_days"] == 3


def test_api_clearing_the_reminder_clears_the_repeat(client):
    sub_id = client.post("/api/subscriptions", json={**BASE, "remind_days_before": "7", "remind_repeat_days": "2"}).get_json()["id"]
    cleared = client.post(f"/api/subscriptions/{sub_id}/reminder", json={"days": None}).get_json()
    assert cleared["remind_repeat_days"] is None


def test_send_hour_waits_for_that_hour_in_the_subscriptions_zone():
    lagos = sub(send_hour=9, time_zone=LAGOS)
    assert reminders_due([lagos], now=utc(date(2026, 11, 24), hour=7, minute=30)) == []  # 08:30 in Lagos
    assert reminders_due([lagos], now=utc(date(2026, 11, 24), hour=8)) != []  # 09:00 in Lagos


def test_send_hour_follows_the_time_zone():
    new_york = sub(send_hour=9, time_zone=NEW_YORK)
    assert reminders_due([new_york], now=utc(date(2026, 11, 24), hour=13)) == []  # 08:00 in New York
    assert reminders_due([new_york], now=utc(date(2026, 11, 24), hour=14)) != []  # 09:00 in New York


def test_subscriptions_saved_before_send_times_keep_13_utc():
    assert reminders_due([sub()], now=utc(date(2026, 11, 24), hour=12, minute=59)) == []
    assert reminders_due([sub()], now=utc(date(2026, 11, 24), hour=13)) != []


def test_run_reminders_waits_for_the_send_hour():
    add_subscription({**BASE, "remind_days_before": 7, "send_hour": 9, "time_zone": LAGOS})
    sent = []
    fake_send = lambda subject, body: sent.append(subject)

    assert run_reminders(now=utc(date(2026, 11, 24), hour=7, minute=30), send=fake_send) == 0  # 08:30 in Lagos
    assert run_reminders(now=utc(date(2026, 11, 24), hour=8), send=fake_send) == 1  # 09:00 in Lagos
    assert run_reminders(now=utc(date(2026, 11, 24), hour=9), send=fake_send) == 0  # already sent today
    assert len(sent) == 1


def test_renewal_day_is_judged_in_the_subscriptions_own_zone():
    # 22:00 on 23 Nov in New York is already 03:00 on 24 Nov in UTC, so the renewal dated 23 Nov has not passed there.
    add_subscription({**BASE, "next_renewal_date": "2026-11-23", "remind_days_before": 0, "send_hour": 21, "time_zone": NEW_YORK})
    sent = []

    now = datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc)
    assert run_reminders(now=now, send=lambda subject, body: sent.append(subject)) == 1
    assert load_subscriptions()[0]["next_renewal_date"] == "2026-11-23"


def test_api_defaults_send_hour_to_9_and_time_zone_to_utc(client):
    created = client.post("/api/subscriptions", json=BASE).get_json()
    assert created["send_hour"] == 9
    assert created["time_zone"] == "UTC"


def test_api_saves_send_hour_and_time_zone(client):
    created = client.post("/api/subscriptions", json={**BASE, "send_hour": "8", "time_zone": LAGOS}).get_json()
    assert created["send_hour"] == 8
    assert created["time_zone"] == LAGOS


@pytest.mark.parametrize("value", ["24", "-1", "noon", "9.5"])
def test_api_rejects_bad_send_hours(client, value):
    response = client.post("/api/subscriptions", json={**BASE, "send_hour": value})
    assert response.status_code == 400


@pytest.mark.parametrize("value", ["Mars/Olympus", "not a zone", "../etc/passwd"])
def test_api_rejects_unknown_time_zones(client, value):
    response = client.post("/api/subscriptions", json={**BASE, "time_zone": value})
    assert response.status_code == 400


def test_api_changing_the_send_time_keeps_the_reminder(client):
    created = client.post("/api/subscriptions", json={**BASE, "remind_days_before": "7", "remind_repeat_days": "2"}).get_json()
    changed = client.post(
        f"/api/subscriptions/{created['id']}/reminder",
        json={"days": 7, "send_hour": "8", "time_zone": LAGOS},
    ).get_json()
    assert changed["remind_days_before"] == 7
    assert changed["remind_repeat_days"] == 2
    assert changed["send_hour"] == 8
    assert changed["time_zone"] == LAGOS


def test_api_rejects_a_bad_time_zone_on_the_reminder_route(client):
    sub_id = client.post("/api/subscriptions", json=BASE).get_json()["id"]
    response = client.post(f"/api/subscriptions/{sub_id}/reminder", json={"days": 7, "time_zone": "Mars/Olympus"})
    assert response.status_code == 400
