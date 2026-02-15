import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.getenv("DB_PATH", "/tmp/prices.db")


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                ts_utc TEXT NOT NULL,
                price REAL NOT NULL,
                source TEXT NOT NULL
            )
        """)
        conn.commit()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def insert_point(symbol: str, price: float, source: str, ts=None):
    ts = ts or datetime.now(timezone.utc)
    with db() as conn:
        conn.execute(
            "INSERT INTO price_points(symbol, ts_utc, price, source) VALUES(?,?,?,?)",
            (symbol, ts.isoformat(), float(price), source),
        )
        conn.commit()


def last_n_points(symbol: str, limit: int = 300):
    with db() as conn:
        cur = conn.execute(
            "SELECT ts_utc, price FROM price_points WHERE symbol=? ORDER BY ts_utc DESC LIMIT ?",
            (symbol, limit),
        )
        rows = cur.fetchall()
    rows.reverse()
    return [{"ts": r[0], "price": r[1]} for r in rows]


def last_point(symbol: str):
    with db() as conn:
        cur = conn.execute(
            "SELECT price FROM price_points WHERE symbol=? ORDER BY ts_utc DESC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
    return row[0] if row else None
