"""
The end date for reminders on the page: the date box in the Reminder form and in the add form, what is sent to
the server, what the list says, and what happens when the server turns a date down. Like the other page tests
these run the page's own script in Node.js against a stub page, and are skipped without Node.js.
"""

import json
import re

import pytest

from test_reminder_form import INDEX_PAGE, OPEN, SAVE, fill, input_value, needs_node, run_page, subscription

ADD = "await page('addSubscription()');"


def end_date(value: str) -> str:
    return f"element('reminderUntil-abc').value = {json.dumps(value)};"


def add_form(until: str = "") -> str:
    fields = {
        "name": "Netflix", "amount": "15.99", "billingCycle": "monthly", "nextRenewal": "2026-12-01", "category": "Video",
        "remindDays": "7", "remindRepeat": "2", "sendHour": "9", "timeZone": "Africa/Lagos", "remindUntil": until,
    }
    return "".join(f"element({json.dumps(name)}).value = {json.dumps(value)};" for name, value in fields.items())


# ---------- the Reminder form ----------

@needs_node
def test_the_form_has_a_date_box_for_the_end_date_filled_with_the_saved_one():
    page = run_page([subscription(remind_until="2026-12-31")], OPEN)

    assert '<label for="reminderUntil-abc">Stop reminders after (optional)</label>' in page["subList"]
    assert '<input type="date" id="reminderUntil-abc"' in page["subList"]
    assert input_value(page["subList"], "reminderUntil-abc") == "2026-12-31"


@needs_node
@pytest.mark.parametrize("none", [{}, {"remind_until": None}, {"remind_until": ""}], ids=["missing", "null", "empty"])
def test_the_date_box_starts_blank_when_there_is_no_end_date(none):
    page = run_page([subscription(**none)], OPEN)

    assert input_value(page["subList"], "reminderUntil-abc") == ""


@needs_node
def test_a_value_with_a_quote_in_it_cannot_break_out_of_the_date_box():
    hostile = '2026-12-31" onfocus="boom'

    page = run_page([subscription(remind_until=hostile)], OPEN)

    assert 'onfocus="boom' not in page["subList"]
    assert "2026-12-31&quot; onfocus=&quot;boom" in page["subList"]


@needs_node
def test_saving_sends_the_end_date_with_the_other_settings():
    steps = OPEN + fill(days="14", repeat="3", hour="8", zone="Africa/Lagos") + end_date("2026-12-31") + SAVE

    page = run_page([subscription()], steps)

    assert page["requests"] == [{
        "url": "/api/subscriptions/abc/reminder", "method": "POST",
        "body": {"days": 14, "repeat_days": 3, "send_hour": 8, "time_zone": "Africa/Lagos", "until": "2026-12-31"},
    }]
    assert "reminderDays-abc" not in page["subList"]


@needs_node
@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_date_is_sent_as_no_end_date(blank):
    page = run_page([subscription(remind_until="2026-12-31")], OPEN + fill(days="7") + end_date(blank) + SAVE)

    assert page["requests"][0]["body"]["until"] is None


@needs_node
def test_spaces_around_the_date_are_dropped():
    page = run_page([subscription()], OPEN + fill(days="7") + end_date("  2026-12-31 ") + SAVE)

    assert page["requests"][0]["body"]["until"] == "2026-12-31"


@needs_node
@pytest.mark.parametrize("bad", ["12/31/2026", "tomorrow", "2026-1-5", "20261231", "2026-12-31T10:00"])
def test_a_date_that_is_not_yyyy_mm_dd_is_explained_in_the_form_and_not_sent(bad):
    page = run_page([subscription()], OPEN + fill(days="7") + end_date(bad) + SAVE)

    assert "YYYY-MM-DD" in page["errors"]["reminderError-abc"]
    assert page["requests"] == []
    assert "reminderDays-abc" in page["subList"]
    assert page["dialogs"] == []


@needs_node
def test_an_end_date_without_days_is_explained_in_the_form_and_not_sent():
    page = run_page([subscription()], OPEN + fill(days="", repeat="") + end_date("2026-12-31") + SAVE)

    assert "Set the days before renewal first, or clear the end date." in page["errors"]["reminderError-abc"]
    assert page["requests"] == []


@needs_node
def test_a_message_about_the_date_from_the_server_is_shown_in_the_form_and_the_date_stays_in_the_box():
    reply = {"error": "remind_until must be today or a later date"}
    steps = OPEN + fill(days="7") + end_date("2020-01-01") + SAVE + "return element('reminderUntil-abc').value;"

    page = run_page([subscription()], steps, status=400, reply=reply)

    assert reply["error"] in page["errors"]["reminderError-abc"]
    assert page["result"] == "2020-01-01"
    assert "reminderDays-abc" in page["subList"]


@needs_node
def test_turning_the_reminder_off_sends_no_end_date_so_the_server_removes_it():
    page = run_page([subscription(remind_until="2026-12-31")], OPEN + "await page('turnOffReminder(\"abc\")');")

    assert page["requests"][0]["body"] == {"days": None, "repeat_days": None}


# ---------- the list ----------

@needs_node
def test_the_list_says_when_the_reminders_stop():
    page = run_page([subscription(remind_until="2026-12-31"), subscription("def", "iCloud")])

    assert "at 08:00 Africa/Lagos, stops after 2026-12-31" in page["subList"]
    assert page["subList"].count("stops after") == 1


@needs_node
def test_the_list_says_nothing_about_an_end_date_when_there_is_no_reminder():
    page = run_page([subscription(remind_days_before=None, remind_repeat_days=None, remind_until="2026-12-31")])

    assert "stops after" not in page["subList"]


@needs_node
def test_the_list_shows_the_end_date_as_text_not_as_markup():
    page = run_page([subscription(remind_until="<img src=x onerror=alert(1)>")])

    assert "&lt;img" in page["subList"]
    assert "<img" not in page["subList"]


# ---------- the add form ----------

@needs_node
def test_adding_a_subscription_sends_the_end_date():
    page = run_page([], add_form("2026-12-31") + ADD)

    body = page["requests"][0]["body"]
    assert page["requests"][0]["url"] == "/api/subscriptions"
    assert body["remind_until"] == "2026-12-31"
    assert (body["remind_days_before"], body["remind_repeat_days"], body["send_hour"], body["time_zone"]) == ("7", "2", "9", "Africa/Lagos")


@needs_node
def test_adding_a_subscription_without_an_end_date_sends_it_blank():
    page = run_page([], add_form() + ADD)

    assert page["requests"][0]["body"]["remind_until"] == ""


@needs_node
def test_the_date_box_is_cleared_after_a_subscription_is_added():
    page = run_page([], add_form("2026-12-31") + ADD + "return element('remindUntil').value;")

    assert page["result"] == ""


@needs_node
def test_the_date_box_keeps_its_date_when_the_server_turns_the_subscription_down():
    steps = add_form("2020-01-01") + ADD + "return [element('remindUntil').value, element('formError').innerHTML];"

    page = run_page([], steps, status=400, reply={"error": "remind_until must be today or a later date"})

    assert page["result"][0] == "2020-01-01"
    assert page["result"][1] == '<div class="error-box">remind_until must be today or a later date</div>'


def test_the_add_form_has_a_labelled_date_box_for_the_end_date():
    html = INDEX_PAGE.read_text()

    assert html.count('<label for="remindUntil">Stop reminders after (optional)</label>') == 1
    assert html.count('<input type="date" id="remindUntil" />') == 1
    assert html.index('id="remindUntil"') < html.index('onclick="addSubscription()"')


def test_labels_are_styled_wherever_a_reminder_field_is_used():
    html = INDEX_PAGE.read_text()

    assert re.search(r"\.form-field label \{[^}]*font-weight: 700", html)
    assert ".reminder-form label" not in html
