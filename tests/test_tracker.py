import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tracker import (
    add_months, add_cycle, add_cycles, normalize_to_monthly, normalize_to_yearly,
    compute_totals, upcoming_renewals, renew_subscription, cancel_subscription,
    roll_forward, roll_forward_all,
)


# ---------- add_months: the part most likely to have subtle bugs ----------

def test_add_months_simple_case():
    assert add_months(date(2026, 3, 15), 1) == date(2026, 4, 15)


def test_add_months_handles_month_end_rollover():
    # Jan 31 + 1 month can't be "Feb 31" - must clamp to Feb's actual last day
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_add_months_handles_leap_year_february():
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)  # 2028 is a leap year


def test_add_months_handles_year_rollover():
    assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)


def test_add_months_twelve_months_equals_one_year():
    assert add_months(date(2026, 6, 10), 12) == date(2027, 6, 10)


# ---------- add_cycle ----------

def test_add_cycle_weekly():
    assert add_cycle(date(2026, 3, 1), "weekly") == date(2026, 3, 8)


def test_add_cycle_monthly_respects_month_end_rollover():
    assert add_cycle(date(2026, 1, 31), "monthly") == date(2026, 2, 28)


def test_add_cycle_yearly():
    assert add_cycle(date(2026, 5, 20), "yearly") == date(2027, 5, 20)


def test_add_cycle_rejects_unknown_cycle():
    try:
        add_cycle(date(2026, 1, 1), "daily")
        assert False, "should have raised"
    except ValueError:
        pass


# ---------- normalization ----------

def test_normalize_monthly_to_monthly_is_unchanged():
    assert normalize_to_monthly(10, "monthly") == 10


def test_normalize_yearly_to_monthly_divides_by_twelve():
    assert normalize_to_monthly(120, "yearly") == 10


def test_normalize_weekly_to_monthly():
    # 5/week * 52 weeks/year / 12 months/year
    assert round(normalize_to_monthly(5, "weekly"), 2) == round(5 * 52 / 12, 2)


def test_normalize_monthly_to_yearly_multiplies_by_twelve():
    assert normalize_to_yearly(10, "monthly") == 120


# ---------- totals ----------

SAMPLE_SUBS = [
    {"amount": 10, "billing_cycle": "monthly", "status": "active"},
    {"amount": 120, "billing_cycle": "yearly", "status": "active"},
    {"amount": 999, "billing_cycle": "monthly", "status": "cancelled"},  # must be excluded
]


def test_compute_totals_excludes_cancelled_subscriptions():
    totals = compute_totals(SAMPLE_SUBS)
    assert totals["active_count"] == 2


def test_compute_totals_monthly_sum_is_correct():
    # 10/month + (120/year = 10/month) = 20/month
    totals = compute_totals(SAMPLE_SUBS)
    assert totals["monthly_total"] == 20.0


def test_compute_totals_yearly_sum_is_correct():
    # 10/month * 12 + 120/year = 240
    totals = compute_totals(SAMPLE_SUBS)
    assert totals["yearly_total"] == 240.0


def test_compute_totals_handles_empty_list():
    totals = compute_totals([])
    assert totals["monthly_total"] == 0
    assert totals["active_count"] == 0


# ---------- upcoming renewals ----------

RENEWAL_SUBS = [
    {"name": "A", "next_renewal_date": "2026-03-05", "status": "active"},
    {"name": "B", "next_renewal_date": "2026-03-20", "status": "active"},  # outside window
    {"name": "C", "next_renewal_date": "2026-03-01", "status": "cancelled"},  # excluded
    {"name": "D", "next_renewal_date": "2026-03-02", "status": "active"},
]


def test_upcoming_renewals_filters_to_window():
    today = date(2026, 3, 1)
    result = upcoming_renewals(RENEWAL_SUBS, within_days=7, today=today)
    names = [r["name"] for r in result]
    assert "B" not in names  # March 20 is outside a 7-day window from March 1


def test_upcoming_renewals_excludes_cancelled():
    today = date(2026, 3, 1)
    result = upcoming_renewals(RENEWAL_SUBS, within_days=7, today=today)
    names = [r["name"] for r in result]
    assert "C" not in names


def test_upcoming_renewals_sorted_soonest_first():
    today = date(2026, 3, 1)
    result = upcoming_renewals(RENEWAL_SUBS, within_days=7, today=today)
    dates = [r["next_renewal_date"] for r in result]
    assert dates == sorted(dates)


# ---------- renew / cancel ----------

def test_renew_subscription_advances_date_by_cycle():
    sub = {"next_renewal_date": "2026-01-31", "billing_cycle": "monthly"}
    renew_subscription(sub)
    assert sub["next_renewal_date"] == "2026-02-28"


def test_cancel_subscription_sets_status_and_date():
    sub = {"status": "active"}
    cancel_subscription(sub)
    assert sub["status"] == "cancelled"
    assert "cancelled_date" in sub


# ---------- rolling passed renewals forward ----------

def test_add_cycles_steps_from_the_original_date():
    assert add_cycles(date(2026, 1, 31), "monthly", 2) == date(2026, 3, 31)


def test_roll_forward_moves_a_passed_renewal_into_the_future():
    sub = {"next_renewal_date": "2026-11-08", "billing_cycle": "monthly", "status": "active"}
    assert roll_forward(sub, date(2026, 11, 22)) is True
    assert sub["next_renewal_date"] == "2026-12-08"


def test_roll_forward_skips_several_missed_weekly_cycles_at_once():
    sub = {"next_renewal_date": "2026-01-01", "billing_cycle": "weekly", "status": "active"}
    roll_forward(sub, date(2026, 2, 1))
    assert sub["next_renewal_date"] == "2026-02-05"


def test_roll_forward_keeps_the_billing_day_across_a_short_month():
    sub = {"next_renewal_date": "2026-01-31", "billing_cycle": "monthly", "status": "active"}
    roll_forward(sub, date(2026, 3, 10))
    assert sub["next_renewal_date"] == "2026-03-31"


def test_roll_forward_leaves_a_renewal_due_today_alone():
    sub = {"next_renewal_date": "2026-11-22", "billing_cycle": "monthly", "status": "active"}
    assert roll_forward(sub, date(2026, 11, 22)) is False
    assert sub["next_renewal_date"] == "2026-11-22"


def test_roll_forward_ignores_cancelled_subscriptions():
    sub = {"next_renewal_date": "2026-01-01", "billing_cycle": "monthly", "status": "cancelled"}
    assert roll_forward(sub, date(2026, 11, 22)) is False
    assert sub["next_renewal_date"] == "2026-01-01"


def test_roll_forward_all_reports_whether_anything_changed():
    subs = [
        {"next_renewal_date": "2026-11-08", "billing_cycle": "monthly", "status": "active"},
        {"next_renewal_date": "2026-12-20", "billing_cycle": "yearly", "status": "active"},
    ]
    assert roll_forward_all(subs, date(2026, 11, 22)) is True
    assert [s["next_renewal_date"] for s in subs] == ["2026-12-08", "2026-12-20"]
    assert roll_forward_all(subs, date(2026, 11, 22)) is False
