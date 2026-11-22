"""
Subscription Tracker web app.

Run with: python src/app.py
Then open: http://localhost:5030
"""

import math
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from auth import SESSION_KEY, check_credentials, is_logged_in, new_secret_key
from limits import client_ip, max_subscriptions, too_many_requests
from storage import (
    load_subscriptions, save_subscriptions, add_subscription, find_subscription, count_subscriptions,
)
from tracker import compute_totals, upcoming_renewals, renew_subscription, cancel_subscription

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


@app.route("/api/subscriptions", methods=["GET"])
def list_subscriptions():
    return jsonify(load_subscriptions())


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
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    if max_subscriptions() and count_subscriptions() >= max_subscriptions():
        return jsonify({"error": f"The shared list is full ({max_subscriptions()} subscriptions) - cancel or ask the owner to clear it"}), 400

    new_sub = add_subscription(data)
    return jsonify(new_sub), 201


@app.route("/api/subscriptions/<sub_id>/cancel", methods=["POST"])
def cancel(sub_id):
    subscriptions = load_subscriptions()
    sub = find_subscription(subscriptions, sub_id)
    if sub is None:
        return jsonify({"error": "Subscription not found"}), 404

    cancel_subscription(sub)
    save_subscriptions(subscriptions)
    return jsonify(sub)


@app.route("/api/subscriptions/<sub_id>/renew", methods=["POST"])
def renew(sub_id):
    subscriptions = load_subscriptions()
    sub = find_subscription(subscriptions, sub_id)
    if sub is None:
        return jsonify({"error": "Subscription not found"}), 404

    renew_subscription(sub)
    save_subscriptions(subscriptions)
    return jsonify(sub)


@app.route("/api/subscriptions/<sub_id>/reminder", methods=["POST"])
def set_reminder(sub_id):
    subscriptions = load_subscriptions()
    sub = find_subscription(subscriptions, sub_id)
    if sub is None:
        return jsonify({"error": "Subscription not found"}), 404

    try:
        days = parse_reminder_days((request.get_json() or {}).get("days"))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    sub["remind_days_before"] = days
    sub["reminded_for_date"] = None  # a changed reminder may need to fire again for this renewal
    save_subscriptions(subscriptions)
    return jsonify(sub)


@app.route("/api/dashboard")
def dashboard():
    subscriptions = load_subscriptions()
    return jsonify({
        "totals": compute_totals(subscriptions),
        "upcoming": upcoming_renewals(subscriptions, within_days=7),
    })


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "1") == "1", port=int(os.environ.get("PORT", "5030")))
