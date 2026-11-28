"""
The Pause reminders and Resume reminders buttons on the page. Like the reminder form tests, these run the
page's own script in Node.js against a stub page and check what it showed and what it sent to the server.
They are skipped without Node.js.
"""

import json
import re

from test_reminder_form import INDEX_PAGE, needs_node, run_page, subscription

PAUSE = "await page('pauseReminders(\"abc\")');"
RESUME = "await page('resumeReminders(\"abc\")');"
LIST_ERROR = "return page(\"document.getElementById('listError').innerHTML\");"


def paused(**changes) -> dict:
    return subscription(reminders_paused=True, **changes)


# ---------- the buttons and the flag ----------

@needs_node
def test_a_subscription_with_a_reminder_offers_to_pause_it():
    page = run_page([subscription()])

    assert page["subList"].count("Pause reminders") == 1
    assert "pauseReminders('abc')" in page["subList"]
    assert "Resume reminders" not in page["subList"]
    assert "reminders paused" not in page["subList"]


@needs_node
def test_a_paused_subscription_says_so_and_offers_to_resume_it():
    page = run_page([paused()])

    assert '<span class="soon-flag">reminders paused</span>' in page["subList"]
    assert page["subList"].count("Resume reminders") == 1
    assert "resumeReminders('abc')" in page["subList"]
    assert "Pause reminders" not in page["subList"]


@needs_node
def test_a_subscription_from_before_pausing_is_shown_as_running():
    old = subscription()
    assert "reminders_paused" not in old

    page = run_page([old])

    assert "Pause reminders" in page["subList"]
    assert "reminders paused" not in page["subList"]


@needs_node
def test_a_subscription_with_no_reminder_has_nothing_to_pause():
    page = run_page([subscription(remind_days_before=None, remind_repeat_days=None)])

    assert "Pause reminders" not in page["subList"]
    assert "Resume reminders" not in page["subList"]


@needs_node
def test_a_cancelled_subscription_has_no_buttons_and_no_flag_even_if_it_was_paused():
    page = run_page([paused(status="cancelled")])

    assert "Pause reminders" not in page["subList"]
    assert "Resume reminders" not in page["subList"]
    assert "reminders paused" not in page["subList"]
    assert '<span class="soon-flag">cancelled</span>' in page["subList"]


@needs_node
def test_the_other_buttons_and_the_reminder_summary_are_still_there():
    page = run_page([paused()])

    for label in ("Reminder", "Renew", "Cancel"):
        assert f">{label}<" in page["subList"]
    assert "email 7 day(s) before, then every 2 day(s), at 08:00 Africa/Lagos" in page["subList"]


@needs_node
def test_each_subscription_gets_its_own_button():
    page = run_page([subscription(), subscription("def", "Spotify", reminders_paused=True)])

    assert "pauseReminders('abc')" in page["subList"]
    assert "resumeReminders('def')" in page["subList"]
    assert "pauseReminders('def')" not in page["subList"]


# ---------- pressing them ----------

@needs_node
def test_pause_posts_to_the_pause_route_and_shows_the_list_the_server_sends_back():
    page = run_page([subscription()], PAUSE, after_save=[paused()])

    assert page["requests"] == [{"url": "/api/subscriptions/abc/pause", "method": "POST", "body": None}]
    assert "Resume reminders" in page["subList"]
    assert "reminders paused" in page["subList"]


@needs_node
def test_resume_posts_to_the_resume_route_and_shows_the_list_the_server_sends_back():
    page = run_page([paused()], RESUME, after_save=[subscription()])

    assert page["requests"] == [{"url": "/api/subscriptions/abc/resume", "method": "POST", "body": None}]
    assert "Pause reminders" in page["subList"]
    assert "reminders paused" not in page["subList"]


@needs_node
def test_the_id_is_encoded_in_the_address():
    hostile = "a/b?c#d"

    page = run_page([subscription(hostile)], f"await page('pauseReminders(' + JSON.stringify({json.dumps(hostile)}) + ')');")

    assert page["requests"][0]["url"] == "/api/subscriptions/a%2Fb%3Fc%23d/pause"


@needs_node
def test_an_id_with_a_quote_in_it_cannot_break_out_of_the_buttons():
    hostile = 'x" onfocus="boom'

    page = run_page([subscription(hostile), subscription(hostile + "2", reminders_paused=True)])

    assert 'onfocus="boom' not in page["subList"]
    assert page["subList"].count("x&quot; onfocus=&quot;boom") >= 2


# ---------- when it goes wrong ----------

@needs_node
def test_a_message_from_the_server_is_shown_above_the_list_and_the_list_is_not_reloaded():
    reply = {"error": "Set a reminder before pausing it"}

    page = run_page([subscription()], PAUSE + LIST_ERROR, status=400, reply=reply, after_save=[paused()])

    assert page["result"] == '<div class="error-box">Set a reminder before pausing it</div>'
    assert "Pause reminders" in page["subList"]
    assert "reminders paused" not in page["subList"]


@needs_node
def test_a_message_from_the_server_is_shown_as_text_not_as_markup():
    page = run_page([subscription()], PAUSE + LIST_ERROR, status=400, reply={"error": "<img src=x onerror=alert(1)>"})

    assert "&lt;img" in page["result"]
    assert "<img" not in page["result"]


@needs_node
def test_an_error_page_that_is_not_json_gets_a_plain_message():
    page = run_page([subscription()], PAUSE + LIST_ERROR, status=500, non_json=True)

    assert page["result"] == '<div class="error-box">Could not change the reminders.</div>'


@needs_node
def test_a_server_that_cannot_be_reached_gets_a_plain_message_and_the_list_stays():
    page = run_page([subscription()], PAUSE + LIST_ERROR, offline=True)

    assert page["result"] == '<div class="error-box">Could not reach the server. Try again.</div>'
    assert "Pause reminders" in page["subList"]


@needs_node
def test_an_old_message_is_cleared_when_the_next_try_starts():
    stale = "page(\"document.getElementById('listError').innerHTML = '<div>old</div>'\");"

    page = run_page([subscription()], stale + PAUSE + LIST_ERROR, after_save=[paused()])

    assert page["result"] == ""


@needs_node
def test_no_browser_dialog_is_used():
    page = run_page([subscription()], PAUSE, status=400, reply={"error": "no"})

    assert page["dialogs"] == []


def test_the_page_has_a_place_for_the_message_above_the_list():
    html = INDEX_PAGE.read_text()

    assert html.count('id="listError"') == 1
    assert html.index('id="listError"') < html.index('id="subList"')


def test_the_rows_wrap_on_a_narrow_screen_so_four_buttons_stay_on_the_page():
    html = INDEX_PAGE.read_text()
    narrow = re.search(r"@media \(max-width: 520px\) \{(.*?)\n    \}", html, re.S)

    assert narrow is not None
    assert re.search(r"\.sub-row \{[^}]*flex-wrap: wrap", narrow.group(1))
    assert re.search(r"\.actions \{[^}]*flex-wrap: wrap", narrow.group(1))


def test_a_tag_is_never_split_over_two_lines():
    assert re.search(r"\.soon-flag \{[^}]*white-space: nowrap", INDEX_PAGE.read_text())
