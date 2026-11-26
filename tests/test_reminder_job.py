"""
The hourly reminder job: every subscription is emailed once, in its own send hour, and one email that
fails does not stop the others.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import reminders
from reminders import RemindersFailed, main, run_reminders
from storage import add_subscription, load_subscriptions

BASE = {"name": "Netflix", "amount": 15.99, "billing_cycle": "monthly", "next_renewal_date": "2026-12-01", "category": "Video"}
DUE_AT_9_UTC = {**BASE, "remind_days_before": 7, "send_hour": 9, "time_zone": "UTC"}
LAGOS = "Africa/Lagos"  # UTC+1 all year
NEW_YORK = "America/New_York"  # UTC-5 in late November, UTC-4 in summer


def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def run_every_hour(start: datetime, hours: int) -> list:
    """Runs the job once an hour, the way Render's schedule does, and returns the UTC time of every email sent."""
    sent_at = []
    for step in range(hours):
        now = start + timedelta(hours=step)
        run_reminders(now=now, send=lambda subject, body, now=now: sent_at.append(now))
    return sent_at


# ---------- one email, in the right hour ----------

@pytest.mark.parametrize("zone, send_hour, expected", [
    pytest.param(LAGOS, 9, utc(2026, 11, 24, 8), id="lagos-9am"),
    pytest.param(NEW_YORK, 9, utc(2026, 11, 24, 14), id="new-york-9am"),
    pytest.param("Asia/Kolkata", 9, utc(2026, 11, 24, 4), id="kolkata-half-hour-offset"),
    pytest.param("Pacific/Kiritimati", 0, utc(2026, 11, 23, 10), id="kiritimati-14-hours-ahead"),
    pytest.param("Pacific/Honolulu", 23, utc(2026, 11, 25, 9), id="honolulu-10-hours-behind"),
    pytest.param("UTC", 13, utc(2026, 11, 24, 13), id="utc-1pm"),
])
def test_four_days_of_hourly_runs_send_one_email_in_the_subscriptions_send_hour(zone, send_hour, expected):
    add_subscription({**BASE, "remind_days_before": 7, "send_hour": send_hour, "time_zone": zone})

    assert run_every_hour(utc(2026, 11, 22), hours=96) == [expected]


def test_the_hour_that_does_not_exist_when_the_clocks_go_forward_still_gets_its_email():
    # In New York 2:00 on 14 March 2027 jumps straight to 3:00.
    add_subscription({**BASE, "next_renewal_date": "2027-03-20", "remind_days_before": 6, "send_hour": 2, "time_zone": NEW_YORK})

    assert run_every_hour(utc(2027, 3, 13), hours=48) == [utc(2027, 3, 14, 7)]


def test_the_hour_that_happens_twice_when_the_clocks_go_back_sends_only_once():
    # In New York 1:00 to 2:00 on 1 November 2026 happens twice.
    add_subscription({**BASE, "next_renewal_date": "2026-11-05", "remind_days_before": 4, "send_hour": 1, "time_zone": NEW_YORK})

    assert run_every_hour(utc(2026, 10, 31), hours=60) == [utc(2026, 11, 1, 5)]


# ---------- one failed email ----------

def three_due_subscriptions():
    for name in ("Netflix", "Spotify", "iCloud"):
        add_subscription({**DUE_AT_9_UTC, "name": name})


def netflix_fails(sent: list):
    def send(subject, body):
        if "Netflix" in subject:
            raise OSError("connection reset")
        sent.append(subject)

    return send


def test_one_failed_email_does_not_stop_the_others():
    three_due_subscriptions()
    sent = []

    with pytest.raises(RemindersFailed) as failed:
        run_reminders(now=utc(2026, 11, 24, 9), send=netflix_fails(sent))

    assert failed.value.sent == 2
    assert failed.value.failures == [("Netflix", "OSError: connection reset")]
    assert sent == ["Reminder: Spotify renews on 2026-12-01", "Reminder: iCloud renews on 2026-12-01"]


def test_a_failed_email_is_not_marked_as_sent():
    three_due_subscriptions()

    with pytest.raises(RemindersFailed):
        run_reminders(now=utc(2026, 11, 24, 9), send=netflix_fails([]))

    reminded = {sub["name"]: sub["reminded_for_date"] for sub in load_subscriptions()}
    assert reminded == {"Netflix": None, "Spotify": "2026-12-01", "iCloud": "2026-12-01"}


def test_a_failed_email_is_tried_again_on_the_next_hourly_run():
    add_subscription(DUE_AT_9_UTC)
    attempts = []

    def fails_the_first_time(subject, body):
        attempts.append(subject)
        if len(attempts) == 1:
            raise OSError("connection reset")

    with pytest.raises(RemindersFailed):
        run_reminders(now=utc(2026, 11, 24, 9), send=fails_the_first_time)

    assert run_reminders(now=utc(2026, 11, 24, 10), send=fails_the_first_time) == 1
    assert run_reminders(now=utc(2026, 11, 24, 11), send=fails_the_first_time) == 0
    assert len(attempts) == 2


def test_every_failure_in_a_run_is_reported():
    three_due_subscriptions()

    def everything_fails(subject, body):
        raise OSError("connection reset")

    with pytest.raises(RemindersFailed) as failed:
        run_reminders(now=utc(2026, 11, 24, 9), send=everything_fails)

    assert failed.value.sent == 0
    assert [name for name, _ in failed.value.failures] == ["Netflix", "Spotify", "iCloud"]


# ---------- what the job prints and its exit status ----------

def test_the_job_exits_with_status_1_and_lists_what_failed(capsys):
    three_due_subscriptions()

    assert main(now=utc(2026, 11, 24, 9), send=netflix_fails([])) == 1

    output = capsys.readouterr().out
    assert "Sent 2 reminder(s), 1 failed:" in output
    assert "Netflix: OSError: connection reset" in output


def test_the_job_exits_with_status_0_when_everything_was_sent(capsys):
    add_subscription(DUE_AT_9_UTC)

    assert main(now=utc(2026, 11, 24, 9), send=lambda subject, body: None) == 0

    assert capsys.readouterr().out.strip() == "Sent 1 reminder(s)"


def test_the_job_exits_with_status_0_when_nothing_is_due(capsys):
    add_subscription(DUE_AT_9_UTC)

    assert main(now=utc(2026, 11, 24, 8), send=lambda subject, body: None) == 0

    assert capsys.readouterr().out.strip() == "Sent 0 reminder(s)"


# ---------- the Gmail side ----------

class FakeSMTP:
    """Stands in for Gmail: remembers what it was given instead of sending anything."""

    messages = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def login(self, address, password):
        self.logged_in_as = (address, password)

    def send_message(self, message):
        FakeSMTP.messages.append(message)


@pytest.fixture
def fake_gmail(monkeypatch):
    FakeSMTP.messages = []
    monkeypatch.setattr(reminders.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setenv("GMAIL_ADDRESS", "sender@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.delenv("REMINDER_TO", raising=False)
    return FakeSMTP


def test_the_email_goes_to_the_gmail_address_unless_reminder_to_is_set(fake_gmail, monkeypatch):
    reminders.send_email("Reminder: Netflix renews on 2026-12-01", "Netflix renews.")
    monkeypatch.setenv("REMINDER_TO", "me@example.com")
    reminders.send_email("Reminder: Netflix renews on 2026-12-01", "Netflix renews.")

    first, second = fake_gmail.messages
    assert first["To"] == "sender@example.com" and first["From"] == "sender@example.com"
    assert second["To"] == "me@example.com" and second["From"] == "sender@example.com"
    assert first["Subject"] == "Reminder: Netflix renews on 2026-12-01"


def test_the_job_sends_through_gmail_by_default(fake_gmail):
    add_subscription(DUE_AT_9_UTC)

    assert main(now=utc(2026, 11, 24, 9)) == 0

    (message,) = fake_gmail.messages
    assert message["Subject"] == "Reminder: Netflix renews on 2026-12-01"
    assert "$15.99" in message.get_content()


def test_a_subscription_whose_email_cannot_be_built_does_not_stop_the_others(fake_gmail, capsys):
    add_subscription({**DUE_AT_9_UTC, "name": "Net\nflix"})  # a line break is not allowed in an email subject
    add_subscription({**DUE_AT_9_UTC, "name": "Spotify"})

    assert main(now=utc(2026, 11, 24, 9)) == 1

    assert [message["Subject"] for message in fake_gmail.messages] == ["Reminder: Spotify renews on 2026-12-01"]
    assert "Net\nflix: ValueError" in capsys.readouterr().out


def test_missing_gmail_settings_are_reported_in_plain_words(monkeypatch):
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set"):
        reminders.send_email("subject", "body")


def test_the_job_says_so_when_the_gmail_settings_are_missing(monkeypatch, capsys):
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    add_subscription(DUE_AT_9_UTC)

    assert main(now=utc(2026, 11, 24, 9)) == 1

    assert "GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set" in capsys.readouterr().out
