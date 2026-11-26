"""
Renewal reminder emails.

Each subscription can have remind_days_before set (for example 1, 7 or 30). A reminder is due on
every day from that many days before the renewal date up to and including the renewal date, but
only once per renewal: reminded_for_date records which renewal date was already emailed, and a new
renewal date (after Renew, or once a passed renewal rolls forward) re-arms the reminder.

If remind_repeat_days is also set (for example 2), the first email still goes out at that point, then
another goes out every that many days until the renewal date. last_reminder_on records the day of the
most recent email for this renewal.

Each subscription also has send_hour (0 to 23) and time_zone (an IANA name such as Africa/Lagos). Its
emails go out on the first run at or after that hour on the subscription's own local date. Subscriptions
saved before these fields existed keep the old send time, 13:00 UTC.

Run every hour with:  python src/reminders.py
(Render runs this as a scheduled job; see render.yaml.)

If an email can't be sent, the run still handles the other subscriptions, prints which ones failed and
ends with exit status 1, so Render shows the run as failed. A failed subscription is not marked as
reminded, so the next hourly run tries it again.

Sending uses Gmail over SMTP. Needs GMAIL_ADDRESS and GMAIL_APP_PASSWORD, and optionally
REMINDER_TO (defaults to GMAIL_ADDRESS).
"""

import os
import smtplib
import sys
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

from storage import load_subscriptions, save_subscriptions
from tracker import roll_forward

LEGACY_SEND_HOUR = 13
LEGACY_TIME_ZONE = "UTC"


class RemindersFailed(Exception):
    """Some reminders could not be sent. The other subscriptions in the run were still handled."""

    def __init__(self, sent: int, failures: list):
        self.sent = sent
        self.failures = failures  # (subscription name, what went wrong) pairs
        super().__init__(f"{len(failures)} reminder(s) could not be sent")


def send_settings(sub: dict) -> tuple:
    """The hour and time zone a subscription's emails go out in. Older subscriptions use 13:00 UTC."""
    hour = sub.get("send_hour")
    return (LEGACY_SEND_HOUR if hour is None else hour, sub.get("time_zone") or LEGACY_TIME_ZONE)


def local_now(sub: dict, now: datetime) -> datetime:
    """The moment `now` as seen in the subscription's time zone."""
    _, zone = send_settings(sub)
    return now.astimezone(ZoneInfo(zone))


def reminders_due(subscriptions: list, now: datetime = None) -> list:
    now = now or datetime.now(timezone.utc)
    due = []
    for sub in subscriptions:
        days = sub.get("remind_days_before")
        if sub["status"] != "active" or days is None:
            continue
        local = local_now(sub, now)
        today = local.date()
        renewal = date.fromisoformat(sub["next_renewal_date"])
        in_window = renewal - timedelta(days=days) <= today <= renewal
        if not in_window:
            continue
        send_hour, _ = send_settings(sub)
        if local.hour < send_hour:
            continue  # today's email goes out once the send hour arrives
        if sub.get("reminded_for_date") != sub["next_renewal_date"]:
            due.append(sub)  # the first email for this renewal
        elif repeat_due(sub, today):
            due.append(sub)
    return due


def repeat_due(sub: dict, today: date) -> bool:
    """True when a repeating reminder is due again: the repeat interval has passed since the last email."""
    repeat = sub.get("remind_repeat_days")
    last = sub.get("last_reminder_on")
    if not repeat or not last:
        return False
    return (today - date.fromisoformat(last)).days >= repeat


def send_email(subject: str, body: str) -> None:
    address = os.environ.get("GMAIL_ADDRESS")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not address or not password:
        raise RuntimeError("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set on the reminders job")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = address
    message["To"] = os.environ.get("REMINDER_TO", address)
    message.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(address, password)
        smtp.send_message(message)


def run_reminders(now: datetime = None, send=None) -> int:
    """Sends every reminder that is due and returns how many went out. If some could not be sent, the
    others are still handled and RemindersFailed is raised at the end."""
    now = now or datetime.now(timezone.utc)
    if send is None:
        send = send_email
    subscriptions = load_subscriptions()
    # Each renewal date is judged by the date in its own subscription's time zone.
    changes = [roll_forward(sub, local_now(sub, now).date()) for sub in subscriptions]
    if any(changes):
        save_subscriptions(subscriptions)  # store renewal dates that have moved forward
    sent = 0
    failures = []
    for sub in reminders_due(subscriptions, now):
        try:
            send(
                f"Reminder: {sub['name']} renews on {sub['next_renewal_date']}",
                f"{sub['name']} ({sub['billing_cycle']}, ${sub['amount']:.2f}) renews on {sub['next_renewal_date']}.",
            )
        except Exception as error:
            failures.append((sub["name"], f"{type(error).__name__}: {error}"))
            continue  # not marked as reminded, so the next hourly run tries again
        sub["reminded_for_date"] = sub["next_renewal_date"]
        sub["last_reminder_on"] = local_now(sub, now).date().isoformat()
        # Save after each email, so a failure later in the run can't cause duplicate emails.
        save_subscriptions(subscriptions)
        sent += 1
    if failures:
        raise RemindersFailed(sent, failures)
    return sent


def main(now: datetime = None, send=None) -> int:
    """The hourly job. Returns its exit status: 0 when every due reminder went out, 1 when any failed."""
    try:
        sent = run_reminders(now=now, send=send)
    except RemindersFailed as error:
        print(f"Sent {error.sent} reminder(s), {len(error.failures)} failed:")
        for name, reason in error.failures:
            print(f"  {name}: {reason}")
        return 1
    print(f"Sent {sent} reminder(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
