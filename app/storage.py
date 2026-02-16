import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

DEFAULT_DB_PRIMARY = "/data/prices.db"
DEFAULT_DB_FALLBACK = "/tmp/prices.db"

_DB_ENV = os.getenv("DB_PATH", DEFAULT_DB_PRIMARY)


def _choose_db_path() -> str:
    # Prefer the env path; if it’s under /data and not writable, fall back to /tmp.
    path = _DB_ENV
    try:
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, exist_ok=True)
        testfile = os.path.join(parent, ".write_test")
        with open(testfile, "w") as f:
            f.write("ok")
        os.remove(testfile)
        return path
    except Exception:
        parent = os.path.dirname(DEFAULT_DB_FALLBACK) or "."
        os.makedirs(parent, exist_ok=True)
        return DEFAULT_DB_FALLBACK


DB_PATH = _choose_db_path()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ohlc_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                open_time_utc TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL,
                source TEXT NOT NULL,
                UNIQUE(symbol, interval, open_time_utc)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlc ON ohlc_points(symbol, interval, open_time_utc)")

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


def upsert_candle(
    symbol: str,
    interval: str,
    open_time_utc: str,
    o: float,
    h: float,
    l: float,
    c: float,
    v: Optional[float],
    source: str,
) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO ohlc_points(symbol, interval, open_time_utc, open, high, low, close, volume, source)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol, interval, open_time_utc)
            DO UPDATE SET open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
                          volume=excluded.volume, source=excluded.source
            """,
            (symbol, interval, open_time_utc, float(o), float(h), float(l), float(c), None if v is None else float(v), source),
        )
        conn.commit()


def get_last_candles(symbol: str, interval: str, limit: int = 300) -> list[dict]:
    limit = max(10, min(int(limit), 2000))
    with db() as conn:
        cur = conn.execute(
            """
            SELECT open_time_utc, open, high, low, close, volume
            FROM ohlc_points
            WHERE symbol=? AND interval=?
            ORDER BY open_time_utc DESC
            LIMIT ?
            """,
            (symbol, interval, limit),
        )
        rows = cur.fetchall()

    rows = list(reversed(rows))
    return [
        {
            "t": r["open_time_utc"],
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
            "volume": r["volume"],
        }
        for r in rows
    ]


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
