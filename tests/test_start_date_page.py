"""
The start date box in the add form. Like the other page tests these run the page's own script in Node.js
against a stub page, and are skipped without Node.js.
"""

import json
import re

from test_reminder_end_date_page import add_form
from test_reminder_form import INDEX_PAGE, needs_node, run_page

ADD = "await page('addSubscription()');"


def start_date(value: str) -> str:
    return f"element('startDate').value = {json.dumps(value)};"


@needs_node
def test_adding_a_subscription_sends_the_start_date():
    page = run_page([], add_form() + start_date("2025-03-10") + ADD)

    body = page["requests"][0]["body"]
    assert page["requests"][0]["url"] == "/api/subscriptions"
    assert body["start_date"] == "2025-03-10"
    assert (body["name"], body["amount"], body["category"]) == ("Netflix", "15.99", "Video")


@needs_node
def test_adding_a_subscription_without_a_start_date_sends_it_blank():
    page = run_page([], add_form() + ADD)

    assert page["requests"][0]["body"]["start_date"] == ""


@needs_node
def test_the_start_date_box_is_cleared_after_a_subscription_is_added():
    page = run_page([], add_form() + start_date("2025-03-10") + ADD + "return element('startDate').value;")

    assert page["result"] == ""


@needs_node
def test_the_start_date_box_keeps_its_date_when_the_server_turns_the_subscription_down():
    steps = add_form() + start_date("2099-01-01") + ADD + "return [element('startDate').value, element('formError').innerHTML];"

    page = run_page([], steps, status=400, reply={"error": "start_date must be today or an earlier date"})

    assert page["result"][0] == "2099-01-01"
    assert page["result"][1] == '<div class="error-box">start_date must be today or an earlier date</div>'


def test_the_add_form_has_a_labelled_date_box_for_the_start_date():
    html = INDEX_PAGE.read_text()

    assert html.count('<label for="startDate">Started on (blank = today)</label>') == 1
    assert html.count('<input type="date" id="startDate" />') == 1
    assert html.index('id="startDate"') < html.index('id="remindDays"') < html.index('onclick="addSubscription()"')


def test_the_labelled_boxes_have_a_name_that_is_not_only_about_reminders():
    html = INDEX_PAGE.read_text()

    assert "reminder-field" not in html
    assert re.search(r"\.form-field label \{[^}]*font-weight: 700", html)
    assert re.search(r"\.form-field \{[^}]*flex: 1", html)
    assert re.search(r"\.form-field input, \.form-field select \{[^}]*width: 100%", html)
