# Subscription Tracker

Tracks recurring subscriptions and flags what's renewing soon, so nothing
renews as a surprise and nothing gets forgotten.

## Why this is harder than it looks

Billing cycles don't add up cleanly across weekly/monthly/yearly, and
date math around month boundaries is a classic source of subtle bugs
(what's "one month after January 31st"?). This project takes that
seriously - `add_months()` correctly clamps to the real last day of the
target month (Jan 31 + 1 month = Feb 28, or Feb 29 in a leap year),
rather than the common shortcut of just adding 30 days.

## Setup

```bash
pip install -r requirements.txt
python src/app.py
```
Then open `http://localhost:5030`.

## Login

The tracker has a single owner login. Every page and `/api` route requires it,
and nobody can log in until both `APP_USERNAME` and `APP_PASSWORD_HASH` are set.

Create the password hash once (the password itself is never stored):

```bash
python -c "from werkzeug.security import generate_password_hash as h; print(h(input('Password: ')))"
```

Set these environment variables:

- `APP_USERNAME`: the login name
- `APP_PASSWORD_HASH`: the hash printed above
- `SECRET_KEY`: any long random string, kept stable (changing it logs everyone out)

Locally, export them before running `python src/app.py`. On Render they are set
in the dashboard (`render.yaml` marks them `sync: false`, so they're not in the repo).

## Renewal reminder emails

Each subscription can have a reminder set to a number of days before renewal
(0 to 365). Leave it blank for none. The reminder is emailed once per renewal,
and Renew starts a new cycle for the next reminder.

Reminders are sent by `src/reminders.py`, which Render runs every hour as a
scheduled job (see `render.yaml`). Each subscription has its own send hour and time zone (set on
the add form or with the Reminder button; new ones default to 9am in your browser's time zone),
and its email goes out in that hour. Set these on the reminders job:

- `DATABASE_URL`: the same Neon connection string as the web service. Without it
  the job can't see your subscriptions, since the web service's local file isn't shared.
- `GMAIL_ADDRESS`: the Gmail address that sends the emails
- `GMAIL_APP_PASSWORD`: a Gmail app password. Create one at
  myaccount.google.com/apppasswords (this needs 2-Step Verification turned on).
  Don't use your normal Gmail password.
- `REMINDER_TO` (optional): where reminders go. Defaults to `GMAIL_ADDRESS`.

To test locally without sending mail, call `run_reminders(send=print)` from `src/reminders.py`
or run the tests.

## Running the tests

```bash
pytest tests/
```
Tests cover the date/money edge cases (month-end rollovers, leap years, year
boundaries, excluding cancelled subscriptions from totals), the API, and login.

## What it does

- Add a subscription (name, amount, billing cycle, next renewal date, category)
- Dashboard: total monthly/yearly spend across active subscriptions,
  normalized across billing cycles
- Flags anything renewing within 7 days
- Renew (advances the date by one cycle) or cancel (keeps the record,
  marks it inactive) without losing history

## Roadmap

- A currency field, if tracking subscriptions in more than one currency matters
- Email/notification reminder for upcoming renewals, rather than only
  showing them when the dashboard is open
