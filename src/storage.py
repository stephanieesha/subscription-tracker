"""
Storage for subscriptions.

Locally (no DATABASE_URL) subscriptions live in data/subscriptions.json, as before. In production
DATABASE_URL points at PostgreSQL and the same four functions read and write a table instead.
Kept separate from tracker.py's pure logic so storage concerns (file I/O, database access, ID
assignment) don't mix with the math being tested.
"""

import json
import os
import uuid
from pathlib import Path

DATA_PATH = Path(__file__).parent.parent / "data" / "subscriptions.json"

COLUMNS = ("id", "name", "amount", "billing_cycle", "next_renewal_date", "category", "status")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    billing_cycle TEXT NOT NULL,
    next_renewal_date TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Other',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def using_database() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


def _connect():
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, prepare_threshold=None)
    conn.execute(CREATE_TABLE)
    return conn


def load_subscriptions() -> list:
    if using_database():
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(COLUMNS)} FROM subscriptions ORDER BY created_at, id"
            ).fetchall()
        return [dict(row) for row in rows]

    if not DATA_PATH.exists():
        return []
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def save_subscriptions(subscriptions: list) -> None:
    if using_database():
        with _connect() as conn:
            for sub in subscriptions:
                conn.execute(
                    """
                    INSERT INTO subscriptions (id, name, amount, billing_cycle, next_renewal_date, category, status)
                    VALUES (%(id)s, %(name)s, %(amount)s, %(billing_cycle)s, %(next_renewal_date)s, %(category)s, %(status)s)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name, amount = EXCLUDED.amount, billing_cycle = EXCLUDED.billing_cycle,
                        next_renewal_date = EXCLUDED.next_renewal_date, category = EXCLUDED.category,
                        status = EXCLUDED.status
                    """,
                    {column: sub[column] for column in COLUMNS},
                )
        return

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(subscriptions, indent=2), encoding="utf-8")


def add_subscription(data: dict) -> dict:
    new_sub = {
        "id": str(uuid.uuid4()),
        "name": data["name"],
        "amount": float(data["amount"]),
        "billing_cycle": data["billing_cycle"],
        "next_renewal_date": data["next_renewal_date"],
        "category": data.get("category", "Other"),
        "status": "active",
    }
    if using_database():
        save_subscriptions([new_sub])  # inserts just this row, without touching the others
        return new_sub

    subscriptions = load_subscriptions()
    subscriptions.append(new_sub)
    save_subscriptions(subscriptions)
    return new_sub


def count_subscriptions() -> int:
    return len(load_subscriptions())


def find_subscription(subscriptions: list, sub_id: str) -> dict:
    for sub in subscriptions:
        if sub["id"] == sub_id:
            return sub
    return None
