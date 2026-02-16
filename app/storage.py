import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "/tmp/prices.db")


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True) if os.path.dirname(DB_PATH) else None

    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                ts_utc TEXT NOT NULL,
                price REAL NOT NULL,
                source TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_price_symbol_ts ON price_points(symbol, ts_utc)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guid TEXT NOT NULL UNIQUE,
                ts_utc TEXT NOT NULL,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                link TEXT,
                summary TEXT,
                published TEXT,
                tags TEXT,
                score REAL NOT NULL,
                signal TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_news_ts ON news_items(ts_utc)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS flow_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                ts_utc TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                quantity REAL NOT NULL,
                notional_usd REAL NOT NULL,
                source TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_flow_ts ON flow_events(ts_utc)")

        conn.commit()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def insert_point(symbol: str, price: float, source: str, ts: Optional[datetime] = None) -> None:
    ts = ts or datetime.now(timezone.utc)
    with db() as conn:
        conn.execute(
            "INSERT INTO price_points(symbol, ts_utc, price, source) VALUES(?,?,?,?)",
            (symbol, ts.isoformat(), float(price), source),
        )
        conn.commit()


def last_n_points(symbol: str, limit: int = 300) -> list[dict]:
    limit = max(1, min(int(limit), 5000))
    with db() as conn:
        cur = conn.execute(
            """
            SELECT ts_utc, price
            FROM price_points
            WHERE symbol=?
            ORDER BY ts_utc DESC
            LIMIT ?
            """,
            (symbol, limit),
        )
        rows = cur.fetchall()

    # return ascending for charting
    points = [{"ts": r["ts_utc"], "price": r["price"]} for r in reversed(rows)]
    return points


def last_point(symbol: str) -> Optional[dict]:
    with db() as conn:
        cur = conn.execute(
            """
            SELECT ts_utc, price, source
            FROM price_points
            WHERE symbol=?
            ORDER BY ts_utc DESC
            LIMIT 1
            """,
            (symbol,),
        )
        r = cur.fetchone()
    if not r:
        return None
    return {"ts": r["ts_utc"], "price": r["price"], "source": r["source"]}


def previous_point(symbol: str) -> Optional[dict]:
    with db() as conn:
        cur = conn.execute(
            """
            SELECT ts_utc, price, source
            FROM price_points
            WHERE symbol=?
            ORDER BY ts_utc DESC
            LIMIT 1 OFFSET 1
            """,
            (symbol,),
        )
        r = cur.fetchone()
    if not r:
        return None
    return {"ts": r["ts_utc"], "price": r["price"], "source": r["source"]}


def insert_news_item(
    guid: str,
    source: str,
    title: str,
    link: str | None,
    summary: str | None,
    published: str | None,
    tags: str | None,
    score: float,
    signal: str,
    ts: Optional[datetime] = None,
) -> bool:
    ts = ts or datetime.now(timezone.utc)
    with db() as conn:
        try:
            conn.execute(
                """
                INSERT INTO news_items(guid, ts_utc, source, title, link, summary, published, tags, score, signal)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (guid, ts.isoformat(), source, title, link, summary, published, tags, float(score), signal),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def insert_flow_event(
    event_id: str,
    symbol: str,
    side: str,
    price: float,
    quantity: float,
    notional_usd: float,
    source: str,
    ts: Optional[datetime] = None,
) -> bool:
    ts = ts or datetime.now(timezone.utc)
    with db() as conn:
        try:
            conn.execute(
                """
                INSERT INTO flow_events(event_id, ts_utc, symbol, side, price, quantity, notional_usd, source)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (event_id, ts.isoformat(), symbol, side, float(price), float(quantity), float(notional_usd), source),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
