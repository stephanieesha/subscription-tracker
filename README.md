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

## Running the tests

```bash
pytest tests/
```
22 tests, focused heavily on the date/money edge cases: month-end
rollovers, leap years, year boundaries, and correct exclusion of
cancelled subscriptions from totals.

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
