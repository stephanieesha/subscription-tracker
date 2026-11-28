"""
The Reminder button opens a form under the subscription, instead of a string of browser prompts. These tests
run the page's own script in Node.js against a stub page, press the buttons by calling the functions they
call, and check what the page showed and what it sent to the server. They are skipped without Node.js.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INDEX_PAGE = Path(__file__).parent.parent / "templates" / "index.html"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="runs the page script with Node.js")

# The page's <script> with a stub document, fetch and dialogs. It runs the steps a test gives it, then
# prints what the page showed and every request it made, as JSON.
NODE_RUNNER = r"""
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync(__PAGE__, 'utf8');
const settings = __SETTINGS__;
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

const focused = [];
const elements = {};
function element(id) {
  if (!elements[id]) elements[id] = { value: '', innerHTML: '', focus() { focused.push(id); }, classList: { toggle() {} } };
  return elements[id];
}
const requests = [];
const contentTypes = [];
const dialogs = [];
const sandbox = {
  document: { getElementById: element },
  fetch: async (url, options = {}) => {
    const method = options.method || 'GET';
    if (method === 'GET' && url === '/api/subscriptions') {
      const saved = requests.length > 0 && settings.afterSave;
      return { ok: true, json: async () => (saved ? settings.afterSave : settings.subscriptions) };
    }
    if (method === 'GET' && url === '/api/dashboard') {
      return { ok: true, json: async () => ({ totals: { monthly_total: 0, yearly_total: 0, active_count: 0 }, upcoming: [] }) };
    }
    requests.push({ url, method, body: options.body ? JSON.parse(options.body) : null });
    contentTypes.push((options.headers || {})['Content-Type']);
    if (settings.offline) throw new TypeError('Failed to fetch');
    if (settings.nonJson) return { ok: false, status: 500, json: async () => { throw new SyntaxError('Unexpected token <'); } };
    return { ok: settings.status < 400, status: settings.status, json: async () => settings.reply };
  },
  prompt: (message) => { dialogs.push(['prompt', message]); return null; },
  alert: (message) => { dialogs.push(['alert', message]); },
};
vm.createContext(sandbox);
if (settings.before) vm.runInContext(settings.before, sandbox);
vm.runInContext(script, sandbox);
const page = (code) => vm.runInContext(code, sandbox);

setTimeout(async () => {
  let error = null;
  let result = null;
  try {
    result = await new Function('page', 'element', 'return (async () => {' + settings.steps + '\n})();')(page, element);
  } catch (e) {
    error = String(e && e.message ? e.message : e);
  }
  setTimeout(() => {
    const errors = {};
    for (const id of Object.keys(elements)) {
      if (id.startsWith('reminderError-')) errors[id] = elements[id].innerHTML;
    }
    console.log(JSON.stringify({
      error, result, subList: element('subList').innerHTML, errors, requests, contentTypes, dialogs, focused,
      zoneOptions: element('timeZoneOptions').innerHTML,
    }));
  }, 20);
}, 20);
"""


def run_page(subscriptions, steps="", status=200, reply=None, non_json=False, offline=False, before="",
             after_save=None) -> dict:
    """
    Loads the page with these subscriptions and runs the steps. `before` runs before the page's own script,
    and `after_save` is the list the server returns once something has been posted to it.
    """
    settings = {
        "subscriptions": subscriptions, "steps": steps, "status": status, "reply": reply or {},
        "nonJson": non_json, "offline": offline, "before": before, "afterSave": after_save,
    }
    code = NODE_RUNNER.replace("__PAGE__", json.dumps(str(INDEX_PAGE))).replace("__SETTINGS__", json.dumps(settings))
    result = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    page = json.loads(result.stdout)
    assert page["error"] is None, page["error"]
    return page


def subscription(sub_id="abc", name="Netflix", **changes) -> dict:
    return {
        "id": sub_id, "name": name, "amount": 15.99, "billing_cycle": "monthly", "next_renewal_date": "2026-12-01",
        "category": "Video", "status": "active", "remind_days_before": 7, "remind_repeat_days": 2,
        "send_hour": 8, "time_zone": "Africa/Lagos", **changes,
    }


OPEN = "page('openReminderForm(\"abc\")');"
SAVE = "await page('saveReminder(\"abc\")');"


def fill(days="", repeat="", hour="9", zone="UTC") -> str:
    return (
        f"element('reminderDays-abc').value = {json.dumps(days)};"
        f"element('reminderRepeat-abc').value = {json.dumps(repeat)};"
        f"element('reminderHour-abc').value = {json.dumps(hour)};"
        f"element('reminderZone-abc').value = {json.dumps(zone)};"
    )


def input_value(markup: str, element_id: str) -> str:
    tag = re.search(rf'<input[^>]*\bid="{re.escape(element_id)}"[^>]*>', markup)
    assert tag, f"no input with id {element_id}"
    return re.search(r'\bvalue="([^"]*)"', tag.group(0)).group(1)


def selected_hour(markup: str, element_id: str) -> str:
    select = re.search(rf'<select[^>]*\bid="{re.escape(element_id)}"[^>]*>(.*?)</select>', markup, re.S)
    assert select, f"no select with id {element_id}"
    return re.search(r'<option value="(\d+)" selected>', select.group(1)).group(1)


# ---------- no more browser prompts ----------

def test_the_page_script_no_longer_uses_browser_prompts_or_alerts():
    script = re.search(r"<script>([\s\S]*?)</script>", INDEX_PAGE.read_text()).group(1)

    assert "prompt(" not in script
    assert "alert(" not in script


@needs_node
def test_the_reminder_button_opens_a_form_and_nothing_pops_up():
    before = run_page([subscription()])
    assert "openReminderForm('abc')" in before["subList"]
    assert "reminderDays-abc" not in before["subList"]

    page = run_page([subscription()], OPEN)

    assert "reminderDays-abc" in page["subList"]
    assert page["focused"] == ["reminderDays-abc"]
    assert page["dialogs"] == []
    for name in ("reminderError", "reminderDays", "reminderRepeat", "reminderHour", "reminderZone"):
        assert f'id="{name}-abc"' in page["subList"]
    # The subscription's own Cancel button is the only one, so closing the form can't be mistaken for it.
    assert page["subList"].count(">Cancel<") == 1
    assert page["subList"].count(">Close<") == 1


@needs_node
def test_only_one_form_is_open_at_a_time():
    subs = [subscription(), subscription("def", "Spotify")]

    page = run_page(subs, OPEN + "page('openReminderForm(\"def\")');")

    assert "reminderDays-def" in page["subList"]
    assert "reminderDays-abc" not in page["subList"]


@needs_node
def test_close_closes_the_form_without_sending_anything():
    page = run_page([subscription()], OPEN + "page('closeReminderForm()');")

    assert "reminderDays-abc" not in page["subList"]
    assert page["requests"] == []


@needs_node
def test_cancelled_subscriptions_have_no_reminder_button():
    page = run_page([subscription(status="cancelled")])

    assert "openReminderForm" not in page["subList"]


@needs_node
def test_an_empty_list_still_says_so():
    page = run_page([])

    assert "No subscriptions yet." in page["subList"]


@needs_node
def test_an_id_with_a_quote_in_it_cannot_break_out_of_the_markup():
    hostile = 'x" onfocus="boom'

    page = run_page([subscription(hostile)], f"page('openReminderForm(' + JSON.stringify({json.dumps(hostile)}) + ')');")

    assert 'onfocus="boom' not in page["subList"]
    assert "x&quot; onfocus=&quot;boom" in page["subList"]


@needs_node
def test_the_summary_line_still_shows_the_reminder_settings():
    page = run_page([subscription(), subscription("def", "iCloud", send_hour=None, time_zone=None)])

    assert "email 7 day(s) before, then every 2 day(s), at 08:00 Africa/Lagos" in page["subList"]
    assert "email 7 day(s) before, then every 2 day(s), at 13:00 UTC" in page["subList"]


# ---------- what the form starts with ----------

@needs_node
def test_the_form_starts_with_the_current_settings():
    page = run_page([subscription()], OPEN)

    assert input_value(page["subList"], "reminderDays-abc") == "7"
    assert input_value(page["subList"], "reminderRepeat-abc") == "2"
    assert selected_hour(page["subList"], "reminderHour-abc") == "8"
    assert input_value(page["subList"], "reminderZone-abc") == "Africa/Lagos"
    assert "Turn off reminder" in page["subList"]


@needs_node
def test_a_subscription_from_before_send_times_starts_at_13_utc():
    page = run_page([subscription(send_hour=None, time_zone=None)], OPEN)

    assert selected_hour(page["subList"], "reminderHour-abc") == "13"
    assert input_value(page["subList"], "reminderZone-abc") == "UTC"


@needs_node
def test_a_subscription_with_no_reminder_starts_blank_and_has_nothing_to_turn_off():
    page = run_page([subscription(remind_days_before=None, remind_repeat_days=None)], OPEN)

    assert input_value(page["subList"], "reminderDays-abc") == ""
    assert input_value(page["subList"], "reminderRepeat-abc") == ""
    assert "Turn off reminder" not in page["subList"]


@needs_node
def test_zero_days_is_shown_as_zero_not_blank():
    page = run_page([subscription(remind_days_before=0, remind_repeat_days=None)], OPEN)

    assert input_value(page["subList"], "reminderDays-abc") == "0"


@needs_node
def test_the_time_zone_boxes_offer_a_list_of_zones():
    page = run_page([subscription()], OPEN)

    assert '<option value="UTC">' in page["zoneOptions"]
    assert '<option value="Africa/Lagos">' in page["zoneOptions"]
    assert '<option value="America/New_York">' in page["zoneOptions"]
    assert 'id="reminderZone-abc" value="Africa/Lagos" list="timeZoneOptions"' in page["subList"]


def test_the_add_form_time_zone_box_uses_the_same_list():
    html = INDEX_PAGE.read_text()
    add_form_zone = re.search(r'<input[^>]*\bid="timeZone"[^>]*>', html).group(0)

    assert 'list="timeZoneOptions"' in add_form_zone
    assert '<datalist id="timeZoneOptions"></datalist>' in html


@needs_node
def test_a_browser_that_cannot_list_zones_still_offers_utc():
    page = run_page([subscription()], before="Intl.supportedValuesOf = undefined;")

    assert page["zoneOptions"] == '<option value="UTC"></option>'


@needs_node
def test_utc_is_listed_once_even_if_the_browser_lists_it_too():
    page = run_page([subscription()], before="Intl.supportedValuesOf = () => ['UTC', 'Africa/Lagos'];")

    assert page["zoneOptions"] == '<option value="UTC"></option><option value="Africa/Lagos"></option>'


# ---------- saving ----------

@needs_node
def test_saving_sends_the_settings_and_closes_the_form():
    steps = OPEN + fill(days="14", repeat="3", hour="8", zone="Africa/Lagos") + SAVE

    page = run_page([subscription()], steps)

    assert page["requests"] == [{
        "url": "/api/subscriptions/abc/reminder", "method": "POST",
        "body": {"days": 14, "repeat_days": 3, "send_hour": 8, "time_zone": "Africa/Lagos"},
    }]
    assert page["contentTypes"] == ["application/json"]
    assert "reminderDays-abc" not in page["subList"]


@needs_node
def test_the_list_is_reloaded_from_the_server_after_saving():
    saved = subscription(remind_days_before=14, remind_repeat_days=3)
    steps = OPEN + fill(days="14", repeat="3", hour="8", zone="Africa/Lagos") + SAVE

    page = run_page([subscription()], steps, after_save=[saved])

    assert "email 14 day(s) before, then every 3 day(s), at 08:00 Africa/Lagos" in page["subList"]
    assert "email 7 day(s) before" not in page["subList"]


@needs_node
def test_blank_days_and_repeat_are_sent_as_no_reminder():
    page = run_page([subscription()], OPEN + fill(days="", repeat="", hour="9", zone="UTC") + SAVE)

    assert page["requests"][0]["body"] == {"days": None, "repeat_days": None, "send_hour": 9, "time_zone": "UTC"}


@needs_node
def test_days_that_are_only_spaces_count_as_blank():
    page = run_page([subscription()], OPEN + fill(days="   ", repeat="  ", hour="9", zone="UTC") + SAVE)

    assert page["requests"][0]["body"] == {"days": None, "repeat_days": None, "send_hour": 9, "time_zone": "UTC"}


@needs_node
def test_zero_days_is_sent_as_zero_not_as_no_reminder():
    page = run_page([subscription()], OPEN + fill(days="0", repeat="", hour="9", zone="UTC") + SAVE)

    assert page["requests"][0]["body"]["days"] == 0


@needs_node
def test_spaces_around_the_time_zone_are_dropped():
    page = run_page([subscription()], OPEN + fill(days="7", hour="9", zone="  Africa/Lagos ") + SAVE)

    assert page["requests"][0]["body"]["time_zone"] == "Africa/Lagos"


@needs_node
@pytest.mark.parametrize("days, repeat, hour, zone, message", [
    pytest.param("400", "", "9", "UTC", "whole number from 0 to 365", id="days-too-big"),
    pytest.param("-1", "", "9", "UTC", "whole number from 0 to 365", id="days-negative"),
    pytest.param("1.5", "", "9", "UTC", "whole number from 0 to 365", id="days-with-a-fraction"),
    pytest.param("soon", "", "9", "UTC", "whole number from 0 to 365", id="days-not-a-number"),
    pytest.param("7", "0", "9", "UTC", "from 1 to 365", id="repeat-zero"),
    pytest.param("7", "366", "9", "UTC", "from 1 to 365", id="repeat-too-big"),
    pytest.param("7", "1.5", "9", "UTC", "from 1 to 365", id="repeat-with-a-fraction"),
    pytest.param("", "2", "9", "UTC", "Set the days before renewal first", id="repeat-without-days"),
    pytest.param("7", "", "24", "UTC", "Choose an hour", id="hour-too-big"),
    pytest.param("7", "", "-1", "UTC", "Choose an hour", id="hour-negative"),
    pytest.param("7", "", "9.5", "UTC", "Choose an hour", id="hour-with-a-fraction"),
    pytest.param("7", "", "", "UTC", "Choose an hour", id="no-hour"),
    pytest.param("7", "", "9", "  ", "Enter a time zone", id="no-time-zone"),
])
def test_a_bad_value_is_explained_in_the_form_and_not_sent(days, repeat, hour, zone, message):
    page = run_page([subscription()], OPEN + fill(days, repeat, hour, zone) + SAVE)

    assert message in page["errors"]["reminderError-abc"]
    assert page["requests"] == []
    assert "reminderDays-abc" in page["subList"]
    assert page["dialogs"] == []


@needs_node
def test_a_message_from_the_server_is_shown_in_the_form_and_the_form_stays_open():
    reply = {"error": "time_zone must be a name like Africa/Lagos or America/New_York"}

    page = run_page([subscription()], OPEN + fill(days="7", zone="Mars/Olympus") + SAVE, status=400, reply=reply)

    assert reply["error"] in page["errors"]["reminderError-abc"]
    assert "reminderDays-abc" in page["subList"]


@needs_node
def test_a_message_from_the_server_is_shown_as_text_not_as_markup():
    page = run_page([subscription()], OPEN + fill(days="7") + SAVE, status=400, reply={"error": "<img src=x onerror=alert(1)>"})

    assert "&lt;img" in page["errors"]["reminderError-abc"]
    assert "<img" not in page["errors"]["reminderError-abc"]


@needs_node
def test_an_error_page_that_is_not_json_gets_a_plain_message():
    page = run_page([subscription()], OPEN + fill(days="7") + SAVE, status=500, non_json=True)

    assert "Could not save the reminder." in page["errors"]["reminderError-abc"]


@needs_node
def test_a_server_that_cannot_be_reached_gets_a_plain_message():
    page = run_page([subscription()], OPEN + fill(days="7") + SAVE, offline=True)

    assert "Could not reach the server." in page["errors"]["reminderError-abc"]
    assert "reminderDays-abc" in page["subList"]


# ---------- turning the reminder off ----------

@needs_node
def test_turn_off_reminder_clears_the_reminder_and_closes_the_form():
    page = run_page([subscription()], OPEN + "await page('turnOffReminder(\"abc\")');")

    assert page["requests"] == [{
        "url": "/api/subscriptions/abc/reminder", "method": "POST", "body": {"days": None, "repeat_days": None},
    }]
    assert "reminderDays-abc" not in page["subList"]
