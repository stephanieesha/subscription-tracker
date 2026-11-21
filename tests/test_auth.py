import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import app as app_module
from conftest import TEST_PASSWORD, TEST_USERNAME
from storage import load_subscriptions

VALID = {"name": "Netflix", "amount": 15.99, "billing_cycle": "monthly", "next_renewal_date": "2026-11-08"}


@pytest.fixture
def anonymous():
    return app_module.app.test_client()


def log_in(client, username=TEST_USERNAME, password=TEST_PASSWORD):
    return client.post("/login", data={"username": username, "password": password})


def test_dashboard_redirects_to_login_when_logged_out(anonymous):
    response = anonymous.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_api_returns_401_when_logged_out(anonymous):
    assert anonymous.get("/api/subscriptions").status_code == 401
    assert anonymous.get("/api/dashboard").status_code == 401


def test_api_writes_are_refused_when_logged_out(anonymous):
    response = anonymous.post("/api/subscriptions", json=VALID)
    assert response.status_code == 401
    assert load_subscriptions() == []


def test_login_page_and_robots_are_public(anonymous):
    assert anonymous.get("/login").status_code == 200
    assert "Disallow: /" in anonymous.get("/robots.txt").get_data(as_text=True)


def test_correct_credentials_log_in(anonymous):
    response = log_in(anonymous)
    assert response.status_code == 302
    assert anonymous.get("/").status_code == 200
    assert anonymous.get("/api/subscriptions").status_code == 200


def test_wrong_password_is_refused(anonymous):
    response = log_in(anonymous, password="wrong")
    assert response.status_code == 200
    assert "Wrong username or password" in response.get_data(as_text=True)
    assert anonymous.get("/").status_code == 302


def test_wrong_username_is_refused(anonymous):
    log_in(anonymous, username="someone-else")
    assert anonymous.get("/api/subscriptions").status_code == 401


def test_nobody_can_log_in_when_no_password_hash_is_configured(anonymous, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD_HASH")
    log_in(anonymous)
    assert anonymous.get("/api/subscriptions").status_code == 401


def test_logout_ends_the_session(anonymous):
    log_in(anonymous)
    anonymous.post("/logout")
    assert anonymous.get("/").status_code == 302
    assert anonymous.get("/api/subscriptions").status_code == 401
