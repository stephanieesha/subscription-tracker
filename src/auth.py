"""
Single-owner login for the subscription tracker.

Configure these environment variables (the README shows how to create the hash):

  APP_USERNAME       the login name
  APP_PASSWORD_HASH  a hash from werkzeug's generate_password_hash, never the password itself
  SECRET_KEY         signs the session cookie; set it once and keep it stable across restarts

If APP_PASSWORD_HASH or APP_USERNAME is missing, nobody can log in (the app fails closed).
"""

import hmac
import os
import secrets

from werkzeug.security import check_password_hash

SESSION_KEY = "logged_in"
MAX_USERNAME_LENGTH = 100
MAX_PASSWORD_LENGTH = 200


def check_credentials(username: str, password: str) -> bool:
    expected_user = os.environ.get("APP_USERNAME", "")
    expected_hash = os.environ.get("APP_PASSWORD_HASH", "")
    if not expected_user or not expected_hash:
        return False
    if len(username) > MAX_USERNAME_LENGTH or len(password) > MAX_PASSWORD_LENGTH:
        return False

    user_ok = hmac.compare_digest(username.encode("utf-8"), expected_user.encode("utf-8"))
    # Check the password even when the username is wrong, so response time doesn't reveal which part failed.
    password_ok = check_password_hash(expected_hash, password)
    return user_ok and password_ok


def is_logged_in(session) -> bool:
    return session.get(SESSION_KEY) is True


def new_secret_key() -> str:
    # Without SECRET_KEY, a random key is used: logins last only until the server restarts.
    return os.environ.get("SECRET_KEY") or secrets.token_hex(32)
