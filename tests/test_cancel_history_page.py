"""
Cancelling a subscription on the page. A cancelled subscription is greyed out, sits below the active ones as
history, and says when it started and when it was cancelled instead of when it renews. The Cancel button shows a
problem above the list when the server turns the request down. Like the other page tests these run the page's own
script in Node.js against a stub page, and are skipped without Node.js.
"""

import json
import re

from test_reminder_form import INDEX_PAGE, needs_node, run_page, subscription

CANCEL = "await page('cancelSub(\"abc\")');"
LIST_ERROR = "return page(\"document.getElementById('listError').innerHTML\");"


def cancelled(sub_id="old", name="Hulu", **changes) -> dict:
    return subscription(sub_id, name, **{"status": "cancelled", "start_date": "2025-03-10", "cancelled_date": "2026-11-24", **changes})


def names_in_order(markup: str) -> list:
    return re.findall(r'<span class="sub-name">([^<]*)</span>', markup)


def meta_of(markup: str) -> str:
    return re.search(r'<span class="sub-meta">([^<]*)</span>', markup).group(1)


# ---------- the history ----------

@needs_node
def test_cancelled_subscriptions_come_after_the_active_ones_which_keep_their_order():
    page = run_page([cancelled("c1", "Old A"), subscription("a1", "B"), cancelled("c2", "Old C"), subscription("a2", "D")])

    assert names_in_order(page["subList"]) == ["B", "D", "Old A", "Old C"]


@needs_node
def test_the_history_shows_the_most_recently_cancelled_first():
    page = run_page([
        cancelled("c1", "June", cancelled_date="2026-06-30"),
        cancelled("c2", "November", cancelled_date="2026-11-24"),
        cancelled("c3", "September", cancelled_date="2026-09-01"),
    ])

    assert names_in_order(page["subList"]) == ["November", "September", "June"]


@needs_node
def test_cancelled_subscriptions_with_no_cancellation_date_come_last_in_the_order_they_were_added():
    page = run_page([
        cancelled("c1", "No date A", cancelled_date=None),
        cancelled("c2", "March", cancelled_date="2026-03-01"),
        cancelled("c3", "No date B", cancelled_date=None),
        cancelled("c4", "January", cancelled_date="2026-01-01"),
    ])

    assert names_in_order(page["subList"]) == ["March", "January", "No date A", "No date B"]


@needs_node
def test_cancelled_subscriptions_cancelled_on_the_same_day_keep_the_order_they_were_added():
    page = run_page([cancelled("c1", "First", cancelled_date="2026-05-05"), cancelled("c2", "Second", cancelled_date="2026-05-05")])

    assert names_in_order(page["subList"]) == ["First", "Second"]


@needs_node
def test_the_stored_list_is_not_reordered():
    page = run_page([cancelled("c1", "Old A"), subscription("a1", "B")], "return page('subscriptions.map((s) => s.name).join(\",\")');")

    assert page["result"] == "Old A,B"


@needs_node
def test_a_list_of_only_cancelled_subscriptions_is_shown():
    page = run_page([cancelled("c1", "Old A"), cancelled("c2", "Old C")])

    assert names_in_order(page["subList"]) == ["Old A", "Old C"]


@needs_node
def test_a_cancelled_row_is_greyed_out_and_has_no_buttons():
    page = run_page([cancelled()])

    assert page["subList"].count('class="sub-row cancelled"') == 1
    assert '<span class="soon-flag">cancelled</span>' in page["subList"]
    assert "<button" not in page["subList"]


@needs_node
def test_only_the_cancelled_rows_are_greyed_out():
    page = run_page([subscription("a1", "B"), cancelled(), subscription("a2", "D")])

    assert page["subList"].count('class="sub-row cancelled"') == 1
    assert page["subList"].count('class="sub-row "') == 2


@needs_node
def test_a_cancelled_row_says_when_it_started_and_was_cancelled():
    page = run_page([cancelled()])

    assert meta_of(page["subList"]) == " - $15.99/monthly - started 2025-03-10 - cancelled 2026-11-24"


@needs_node
def test_a_cancelled_row_does_not_show_a_next_renewal_or_reminders():
    page = run_page([cancelled(remind_days_before=7, remind_repeat_days=2, remind_until="2026-12-31")])

    for text in ("next:", "email", "stops after", "Africa/Lagos", "2026-12-01"):
        assert text not in page["subList"]


@needs_node
def test_what_a_cancelled_row_says_depends_on_which_dates_are_known():
    both = run_page([cancelled()])
    only_start = run_page([cancelled(cancelled_date=None)])
    only_cancelled = run_page([cancelled(start_date=None)])
    legacy = run_page([cancelled(start_date=None, cancelled_date=None)])
    missing = run_page([subscription(status="cancelled")])

    assert meta_of(only_start["subList"]) == " - $15.99/monthly - started 2025-03-10"
    assert meta_of(only_cancelled["subList"]) == " - $15.99/monthly - cancelled 2026-11-24"
    assert meta_of(legacy["subList"]) == " - $15.99/monthly"
    assert meta_of(missing["subList"]) == " - $15.99/monthly"
    assert "started" in both["subList"]


@needs_node
def test_the_dates_in_the_history_are_shown_as_text_not_as_markup():
    page = run_page([cancelled(start_date="<img src=x onerror=alert(1)>", cancelled_date="<b>soon</b>")])

    assert "&lt;img" in page["subList"]
    assert "&lt;b&gt;soon" in page["subList"]
    assert "<img" not in page["subList"]
    assert "<b>" not in page["subList"]


@needs_node
def test_an_active_row_still_shows_its_next_renewal_and_reminders_and_not_its_start_date():
    page = run_page([subscription(start_date="2025-03-10", remind_until="2026-12-31")])

    assert meta_of(page["subList"]) == " - $15.99/monthly - next: 2026-12-01 - email 7 day(s) before, then every 2 day(s), at 08:00 Africa/Lagos, stops after 2026-12-31"
    assert "started" not in page["subList"]


def test_a_greyed_out_row_is_faded():
    assert re.search(r"\.cancelled \{ opacity: 0\.[0-9]+; \}", INDEX_PAGE.read_text())


# ---------- the Cancel button ----------

@needs_node
def test_cancel_posts_to_the_cancel_route_with_no_body_and_shows_the_row_greyed_out():
    saved = cancelled("abc", "Netflix")

    page = run_page([subscription()], CANCEL, after_save=[saved])

    assert page["requests"] == [{"url": "/api/subscriptions/abc/cancel", "method": "POST", "body": None}]
    assert page["subList"].count('class="sub-row cancelled"') == 1
    assert "cancelled 2026-11-24" in page["subList"]
    assert "<button" not in page["subList"]


@needs_node
def test_cancel_reloads_the_totals():
    steps = "element('statRow').innerHTML = '';" + CANCEL + "return element('statRow').innerHTML;"

    page = run_page([subscription()], steps, after_save=[cancelled("abc", "Netflix")])

    assert "Monthly total" in page["result"]


@needs_node
def test_the_id_is_encoded_in_the_address():
    hostile = "a/b?c#d"

    page = run_page([subscription(hostile)], f"await page('cancelSub(' + JSON.stringify({json.dumps(hostile)}) + ')');")

    assert page["requests"][0]["url"] == "/api/subscriptions/a%2Fb%3Fc%23d/cancel"


@needs_node
def test_an_id_with_a_quote_in_it_cannot_break_out_of_the_cancel_button():
    hostile = 'x" onfocus="boom'

    page = run_page([subscription(hostile)])

    assert 'onfocus="boom' not in page["subList"]
    assert "cancelSub('x&quot; onfocus=&quot;boom')" in page["subList"]


# ---------- when it goes wrong ----------

@needs_node
def test_a_message_from_the_server_is_shown_above_the_list_and_the_row_stays_active():
    reply = {"error": "Subscription not found"}

    page = run_page([subscription()], CANCEL + LIST_ERROR, status=404, reply=reply, after_save=[cancelled("abc", "Netflix")])

    assert page["result"] == '<div class="error-box">Subscription not found</div>'
    assert "cancelSub('abc')" in page["subList"]
    assert 'class="sub-row cancelled"' not in page["subList"]


@needs_node
def test_a_failed_cancel_leaves_the_totals_alone():
    steps = "element('statRow').innerHTML = 'before';" + CANCEL + "return element('statRow').innerHTML;"

    page = run_page([subscription()], steps, status=500, reply={"error": "no"})

    assert page["result"] == "before"


@needs_node
def test_a_message_from_the_server_is_shown_as_text_not_as_markup():
    page = run_page([subscription()], CANCEL + LIST_ERROR, status=400, reply={"error": "<img src=x onerror=alert(1)>"})

    assert "&lt;img" in page["result"]
    assert "<img" not in page["result"]


@needs_node
def test_an_error_page_that_is_not_json_gets_a_plain_message():
    page = run_page([subscription()], CANCEL + LIST_ERROR, status=500, non_json=True)

    assert page["result"] == '<div class="error-box">Could not cancel the subscription.</div>'


@needs_node
def test_a_server_that_cannot_be_reached_gets_a_plain_message_and_nothing_is_reloaded():
    steps = "element('statRow').innerHTML = 'before';" + CANCEL + "return [element('statRow').innerHTML, element('listError').innerHTML];"

    page = run_page([subscription()], steps, offline=True)

    assert page["result"] == ["before", '<div class="error-box">Could not reach the server. Try again.</div>']
    assert "cancelSub('abc')" in page["subList"]


@needs_node
def test_an_old_message_is_cleared_when_the_next_try_starts():
    stale = "page(\"document.getElementById('listError').innerHTML = '<div>old</div>'\");"

    page = run_page([subscription()], stale + CANCEL + LIST_ERROR, after_save=[cancelled("abc", "Netflix")])

    assert page["result"] == ""


@needs_node
def test_no_browser_dialog_is_used():
    page = run_page([subscription()], CANCEL, status=400, reply={"error": "no"})

    assert page["dialogs"] == []
