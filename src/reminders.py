"""
Renewal reminder emails.

Each subscription can have remind_days_before set (for example 1, 7 or 30). A reminder is due on
every day from that many days before the renewal date up to and including the renewal date, but
only once per renewal: reminded_for_date records which renewal date was already emailed, and a new
renewal date (after Renew, or once a passed renewal rolls forward) re-arms the reminder.

Run once a day with:  python src/reminders.py
(Render runs this as a scheduled job; see render.yaml.)

Sending uses Gmail over SMTP. Needs GMAIL_ADDRESS and GMAIL_APP_PASSWORD, and optionally
REMINDER_TO (defaults to GMAIL_ADDRESS).
"""

import os
import smtplib
import sys
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from storage import load_subscriptions, save_subscriptions
from tracker import roll_forward_all


def reminders_due(subscriptions: list, today: date = None) -> list:
    today = today or date.today()
    due = []
    for sub in subscriptions:
        days = sub.get("remind_days_before")
        if sub["status"] != "active" or days is None:
            continue
        renewal = date.fromisoformat(sub["next_renewal_date"])
        in_window = renewal - timedelta(days=days) <= today <= renewal
        if in_window and sub.get("reminded_for_date") != sub["next_renewal_date"]:
            due.append(sub)
    return due


def send_email(subject: str, body: str) -> None:
    address = os.environ["GMAIL_ADDRESS"]
    password = os.environ["GMAIL_APP_PASSWORD"]

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = address
    message["To"] = os.environ.get("REMINDER_TO", address)
    message.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(address, password)
        smtp.send_message(message)


def run_reminders(today: date = None, send=send_email) -> int:
    subscriptions = load_subscriptions()
    if roll_forward_all(subscriptions, today):
        save_subscriptions(subscriptions)  # store renewal dates that have moved forward
    sent = 0
    for sub in reminders_due(subscriptions, today):
        send(
            f"Reminder: {sub['name']} renews on {sub['next_renewal_date']}",
            f"{sub['name']} ({sub['billing_cycle']}, ${sub['amount']:.2f}) renews on {sub['next_renewal_date']}.",
        )
        sub["reminded_for_date"] = sub["next_renewal_date"]
        # Save after each email, so a failure later in the run can't cause duplicate emails.
        save_subscriptions(subscriptions)
        sent += 1
    return sent


if __name__ == "__main__":
    print(f"Sent {run_reminders()} reminder(s)")
