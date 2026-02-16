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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol_ts ON price_points(symbol, ts_utc)")

        # OHLC candles (used for XRP candlesticks)
        conn.execute("""
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
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlc ON ohlc_points(symbol, interval, open_time_utc)")

        conn.execute("""
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
        """)
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
    return [{"ts": r[0], "price": float(r[1])} for r in rows]


def last_point(symbol: str):
    with db() as conn:
        cur = conn.execute(
            "SELECT ts_utc, price FROM price_points WHERE symbol=? ORDER BY ts_utc DESC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"ts": row[0], "price": float(row[1])}


def previous_point(symbol: str):
    with db() as conn:
        cur = conn.execute(
            "SELECT ts_utc, price FROM price_points WHERE symbol=? ORDER BY ts_utc DESC LIMIT 1 OFFSET 1",
            (symbol,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"ts": row[0], "price": float(row[1])}


def upsert_ohlc(
    symbol: str,
    interval: str,
    open_time_utc: str,
    o: float,
    h: float,
    l: float,
    c: float,
    v: float | None,
    source: str,
) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO ohlc_points(symbol, interval, open_time_utc, open, high, low, close, volume, source)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (symbol, interval, open_time_utc, float(o), float(h), float(l), float(c), float(v) if v is not None else None, source),
        )
        conn.commit()


def last_n_ohlc(symbol: str, interval: str, limit: int = 200):
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
    rows.reverse()
    return [
        {
            "ts": r[0],
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]) if r[5] is not None else None,
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
    ts=None,
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
    ts=None,
) -> bool:
    ts = ts or datetime.now(timezone.utc)
    with db() as conn:
        try:
            conn.execute(
                """
                INSERT INTO flow_events(event_id, ts_utc, symbol, side, price, quantity, notional_usd, source)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    event_id,
                    ts.isoformat(),
                    symbol,
                    side,
                    float(price),
                    float(quantity),
                    float(notional_usd),
                    source,
                ),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
