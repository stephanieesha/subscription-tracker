"""
Subscription Tracker web app.

Run with: python src/app.py
Then open: http://localhost:5030
"""

import math
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from auth import SESSION_KEY, check_credentials, is_logged_in, new_secret_key
from limits import client_ip, max_subscriptions, too_many_requests
from storage import (
    load_subscriptions, save_subscriptions, add_subscription, find_subscription, count_subscriptions,
)
from tracker import compute_totals, upcoming_renewals, renew_subscription, cancel_subscription, roll_forward_all

ROOT = Path(__file__).parent.parent
app = Flask(__name__, template_folder=str(ROOT / "templates"))
app.secret_key = new_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Render serves over HTTPS and sets RENDER=true; local http://localhost needs the cookie to work over http.
    SESSION_COOKIE_SECURE=os.environ.get("RENDER") == "true",
)

PUBLIC_PATHS = ("/login", "/robots.txt")
MAX_REMINDER_DAYS = 365
DEFAULT_SEND_HOUR = 9
DEFAULT_TIME_ZONE = "UTC"


def parse_reminder_days(raw):
    """None or blank means no reminder. Otherwise a whole number of days from 0 to MAX_REMINDER_DAYS."""
    if raw is None or raw == "":
        return None
    try:
        days = int(raw)
    except (ValueError, TypeError):
        raise ValueError("remind_days_before must be a whole number of days")
    if days < 0 or days > MAX_REMINDER_DAYS:
        raise ValueError(f"remind_days_before must be between 0 and {MAX_REMINDER_DAYS}")
    return days


def parse_repeat_days(raw):
    """None, blank or 0 means no repeat. Otherwise a whole number of days from 1 to MAX_REMINDER_DAYS."""
    if raw is None or raw == "":
        return None
    try:
        days = int(raw)
    except (ValueError, TypeError):
        raise ValueError("remind_repeat_days must be a whole number of days")
    if days < 0 or days > MAX_REMINDER_DAYS:
        raise ValueError(f"remind_repeat_days must be between 0 and {MAX_REMINDER_DAYS}")
    return days or None


def parse_send_hour(raw):
    """None or blank means 9am. Otherwise a whole hour from 0 (midnight) to 23."""
    if raw is None or str(raw).strip() == "":
        return DEFAULT_SEND_HOUR
    try:
        hour = int(raw)
    except (ValueError, TypeError):
        raise ValueError("send_hour must be a whole hour from 0 to 23")
    if hour < 0 or hour > 23:
        raise ValueError("send_hour must be a whole hour from 0 to 23")
    return hour


def parse_date_text(raw, field):
    """None or blank means no date. Otherwise a date written YYYY-MM-DD, returned in that form."""
    text = "" if raw is None else str(raw).strip()
    if text == "":
        return None
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            pass
    raise ValueError(f"{field} must be a date in YYYY-MM-DD format")


def parse_remind_until(raw):
    """None or blank means no end date. Otherwise the last day reminders may be sent, as YYYY-MM-DD."""
    return parse_date_text(raw, "remind_until")


def parse_start_date(raw):
    """None or blank means the subscription starts today. Otherwise the day it started, as YYYY-MM-DD."""
    return parse_date_text(raw, "start_date")


def today_in(time_zone):
    """Today's date in a time zone, which is the date the reminder job sees for a subscription set to that zone."""
    return datetime.now(ZoneInfo(time_zone or DEFAULT_TIME_ZONE)).date()


def end_date_problem(until, days, time_zone, saved=None):
    """What is wrong with an end date for reminders, or None. A date that is already saved may be in the past,
    so changing another setting never fails because of it."""
    if until is None:
        return None
    if days is None:
        return "Set a reminder before choosing an end date"
    if until != saved and until < today_in(time_zone).isoformat():
        return "remind_until must be today or a later date"
    return None


def parse_time_zone(raw):
    """None or blank means UTC. Otherwise an IANA time zone name such as Africa/Lagos or America/New_York."""
    name = "" if raw is None else str(raw).strip()
    if name == "":
        return DEFAULT_TIME_ZONE
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("time_zone must be a name like Africa/Lagos or America/New_York")
    return name


@app.before_request
def require_login():
    if request.path in PUBLIC_PATHS or is_logged_in(session):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "Log in to continue"}), 401
    return redirect(url_for("login"))


@app.before_request
def limit_changes_per_visitor():
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and too_many_requests(client_ip(request)):
        return jsonify({"error": "Too many requests - please slow down and try again in a minute"}), 429


@app.after_request
def keep_out_of_search_engines(response):
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.route("/robots.txt")
def robots():
    return "User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain"}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if check_credentials(request.form.get("username", ""), request.form.get("password", "")):
            session.clear()
            session[SESSION_KEY] = True
            return redirect(url_for("index"))
        error = "Wrong username or password"
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


def load_current_subscriptions() -> list:
    """Loads subscriptions with any renewal date that has already passed moved forward. Nothing is saved
    here: the routes that change a subscription save it, and the reminder job saves the moved dates."""
    subscriptions = load_subscriptions()
    roll_forward_all(subscriptions)
    return subscriptions


@app.route("/api/subscriptions", methods=["GET"])
def list_subscriptions():
    return jsonify(load_current_subscriptions())


@app.route("/api/subscriptions", methods=["POST"])
def create_subscription():
    data = request.get_json() or {}

    required = ["name", "amount", "billing_cycle", "next_renewal_date"]
    missing = [field for field in required if not data.get(field)]
    if missing:
        return jsonify({"error": f"Missing required field(s): {', '.join(missing)}"}), 400

    if data["billing_cycle"] not in ("weekly", "monthly", "yearly"):
        return jsonify({"error": "billing_cycle must be weekly, monthly, or yearly"}), 400

    try:
        amount = float(data["amount"])
    except (ValueError, TypeError):
        return jsonify({"error": "amount must be a number"}), 400
    if not math.isfinite(amount) or amount < 0 or amount > 1_000_000_000:
        return jsonify({"error": "amount must be between 0 and 1,000,000,000"}), 400

    try:
        date.fromisoformat(str(data["next_renewal_date"]))
    except ValueError:
        return jsonify({"error": "next_renewal_date must be a date in YYYY-MM-DD format"}), 400

    if len(str(data["name"])) > 100 or len(str(data.get("category", ""))) > 100:
        return jsonify({"error": "name and category must be 100 characters or fewer"}), 400

    try:
        data["remind_days_before"] = parse_reminder_days(data.get("remind_days_before"))
        data["remind_repeat_days"] = parse_repeat_days(data.get("remind_repeat_days"))
        data["send_hour"] = parse_send_hour(data.get("send_hour"))
        data["time_zone"] = parse_time_zone(data.get("time_zone"))
        data["remind_until"] = parse_remind_until(data.get("remind_until"))
        data["start_date"] = parse_start_date(data.get("start_date"))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    if data["remind_repeat_days"] and data["remind_days_before"] is None:
        return jsonify({"error": "Set a reminder before choosing a repeat"}), 400
    problem = end_date_problem(data["remind_until"], data["remind_days_before"], data["time_zone"])
    if problem:
        return jsonify({"error": problem}), 400

    today = today_in(data["time_zone"]).isoformat()  # the date where the subscription's owner is
    if data["start_date"] is None:
        data["start_date"] = today
    elif data["start_date"] > today:
        return jsonify({"error": "start_date must be today or an earlier date"}), 400

    if max_subscriptions() and count_subscriptions() >= max_subscriptions():
        return jsonify({"error": f"The shared list is full ({max_subscriptions()} subscriptions) - cancel or ask the owner to clear it"}), 400

    new_sub = add_subscription(data)
    return jsonify(new_sub), 201


@app.route("/api/subscriptions/<sub_id>/cancel", methods=["POST"])
def cancel(sub_id):
    subscriptions = load_current_subscriptions()
    sub = find_subscription(subscriptions, sub_id)
    if sub is None:
        return jsonify({"error": "Subscription not found"}), 404

    cancel_subscription(sub, today_in(sub.get("time_zone")))
    save_subscriptions(subscriptions)
    return jsonify(sub)


@app.route("/api/subscriptions/<sub_id>/renew", methods=["POST"])
def renew(sub_id):
    subscriptions = load_current_subscriptions()
    sub = find_subscription(subscriptions, sub_id)
    if sub is None:
        return jsonify({"error": "Subscription not found"}), 404

    renew_subscription(sub)
    save_subscriptions(subscriptions)
    return jsonify(sub)


@app.route("/api/subscriptions/<sub_id>/reminder", methods=["POST"])
def set_reminder(sub_id):
    subscriptions = load_current_subscriptions()
    sub = find_subscription(subscriptions, sub_id)
    if sub is None:
        return jsonify({"error": "Subscription not found"}), 404

    body = request.get_json() or {}
    try:
        days = parse_reminder_days(body.get("days"))
        # Keep the current repeat, end date, send hour and time zone unless the request sends them.
        repeat = parse_repeat_days(body["repeat_days"]) if "repeat_days" in body else sub.get("remind_repeat_days")
        until = parse_remind_until(body["until"]) if "until" in body else sub.get("remind_until")
        send_hour = parse_send_hour(body["send_hour"]) if "send_hour" in body else sub.get("send_hour")
        time_zone = parse_time_zone(body["time_zone"]) if "time_zone" in body else sub.get("time_zone")
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    if "until" in body:
        problem = end_date_problem(until, days, time_zone, sub.get("remind_until"))
        if problem:
            return jsonify({"error": problem}), 400

    days_changed = days != sub.get("remind_days_before")
    sub["remind_days_before"] = days
    sub["remind_repeat_days"] = repeat if days is not None else None  # a repeat needs a first reminder
    sub["remind_until"] = until if days is not None else None  # and so does an end date
    if days is None:
        sub["reminders_paused"] = False  # with no reminder there is nothing left to pause
    sub["send_hour"] = send_hour
    sub["time_zone"] = time_zone
    if days_changed:
        sub["reminded_for_date"] = None  # a changed reminder may need to fire again for this renewal
        sub["last_reminder_on"] = None
    save_subscriptions(subscriptions)
    return jsonify(sub)


def set_reminders_paused(sub_id, paused):
    subscriptions = load_current_subscriptions()
    sub = find_subscription(subscriptions, sub_id)
    if sub is None:
        return jsonify({"error": "Subscription not found"}), 404
    if paused and sub.get("remind_days_before") is None:
        return jsonify({"error": "Set a reminder before pausing it"}), 400

    sub["reminders_paused"] = paused
    save_subscriptions(subscriptions)
    return jsonify(sub)


@app.route("/api/subscriptions/<sub_id>/pause", methods=["POST"])
def pause_reminders(sub_id):
    return set_reminders_paused(sub_id, True)


@app.route("/api/subscriptions/<sub_id>/resume", methods=["POST"])
def resume_reminders(sub_id):
    return set_reminders_paused(sub_id, False)


@app.route("/api/dashboard")
def dashboard():
    subscriptions = load_current_subscriptions()
    return jsonify({
        "totals": compute_totals(subscriptions),
        "upcoming": upcoming_renewals(subscriptions, within_days=7),
    })


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "1") == "1", port=int(os.environ.get("PORT", "5030")))
