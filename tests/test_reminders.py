import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import app as app_module
from reminders import reminders_due, run_reminders
from storage import add_subscription, load_subscriptions

TODAY = date(2026, 11, 21)
BASE = {"name": "Netflix", "amount": 15.99, "billing_cycle": "monthly", "next_renewal_date": "2026-12-01", "category": "Video"}


def sub(**changes):
    return {**BASE, "id": "x", "status": "active", "remind_days_before": 7, "reminded_for_date": None, **changes}


def test_reminder_is_due_inside_the_window():
    assert reminders_due([sub()], today=date(2026, 11, 24)) != []  # 7 days before


def test_reminder_is_not_due_before_the_window():
    assert reminders_due([sub()], today=date(2026, 11, 23)) == []


def test_reminder_is_due_on_renewal_day_if_missed_earlier():
    assert reminders_due([sub()], today=date(2026, 12, 1)) != []


def test_reminder_is_not_due_after_renewal_day():
    assert reminders_due([sub()], today=date(2026, 12, 2)) == []


def test_no_reminder_when_none_is_set():
    assert reminders_due([sub(remind_days_before=None)], today=TODAY) == []


def test_cancelled_subscriptions_are_skipped():
    assert reminders_due([sub(status="cancelled")], today=date(2026, 11, 26)) == []


def test_already_reminded_for_this_renewal_is_skipped():
    assert reminders_due([sub(reminded_for_date="2026-12-01")], today=date(2026, 11, 26)) == []


def test_reminder_rearms_after_renewal_moves_the_date():
    renewed = sub(reminded_for_date="2026-12-01", next_renewal_date="2027-01-01")
    assert reminders_due([renewed], today=date(2026, 12, 27)) != []


def test_zero_days_means_reminder_on_renewal_day_only():
    assert reminders_due([sub(remind_days_before=0)], today=date(2026, 11, 30)) == []
    assert reminders_due([sub(remind_days_before=0)], today=date(2026, 12, 1)) != []


def test_run_reminders_emails_once_and_records_it():
    add_subscription({**BASE, "remind_days_before": 7})
    sent = []
    fake_send = lambda subject, body: sent.append(subject)

    assert run_reminders(today=date(2026, 11, 24), send=fake_send) == 1
    assert run_reminders(today=date(2026, 11, 25), send=fake_send) == 0
    assert len(sent) == 1
    assert load_subscriptions()[0]["reminded_for_date"] == "2026-12-01"


def test_run_reminders_moves_a_passed_renewal_forward_before_reminding():
    add_subscription({**BASE, "next_renewal_date": "2026-11-01", "remind_days_before": 30})
    sent = []

    assert run_reminders(today=date(2026, 11, 22), send=lambda subject, body: sent.append(subject)) == 1
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
    assert reminders_due([repeating], today=date(2026, 11, 25)) == []
    assert reminders_due([repeating], today=date(2026, 11, 26)) != []


def test_repeat_stops_once_the_renewal_has_passed():
    repeating = sub(remind_days_before=7, remind_repeat_days=2, reminded_for_date="2026-12-01", last_reminder_on="2026-11-30")
    assert reminders_due([repeating], today=date(2026, 12, 2)) == []


def test_no_repeat_when_no_interval_is_set():
    once = sub(remind_days_before=7, reminded_for_date="2026-12-01", last_reminder_on="2026-11-24")
    assert reminders_due([once], today=date(2026, 11, 30)) == []


def test_repeat_still_waits_for_the_first_reminder():
    assert reminders_due([sub(remind_days_before=7, remind_repeat_days=2)], today=date(2026, 11, 24)) != []


def test_run_reminders_repeats_until_renewal():
    add_subscription({**BASE, "remind_days_before": 7, "remind_repeat_days": 2})
    sent = []
    fake_send = lambda subject, body: sent.append(subject)

    assert run_reminders(today=date(2026, 11, 24), send=fake_send) == 1  # first email
    assert run_reminders(today=date(2026, 11, 25), send=fake_send) == 0  # too soon
    assert run_reminders(today=date(2026, 11, 26), send=fake_send) == 1  # repeat
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
