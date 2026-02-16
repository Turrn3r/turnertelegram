import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

DEFAULT_DB_PRIMARY = "/data/prices.db"
DEFAULT_DB_FALLBACK = "/tmp/prices.db"
_DB_ENV = os.getenv("DB_PATH", DEFAULT_DB_PRIMARY)

def _choose_db_path() -> str:
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

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orderbook_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT NOT NULL UNIQUE,
                ts_utc TEXT NOT NULL,
                symbol TEXT NOT NULL,
                mid REAL NOT NULL,
                spread_bps REAL NOT NULL,
                bid_depth_usd REAL NOT NULL,
                ask_depth_usd REAL NOT NULL,
                imbalance REAL NOT NULL,
                top_wall_side TEXT NOT NULL,
                top_wall_usd REAL NOT NULL,
                top_wall_price REAL NOT NULL,
                source TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ob_ts ON orderbook_signals(ts_utc)")

        # Event -> price linkage (for impact tracking)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link_id TEXT NOT NULL UNIQUE,
                news_guid TEXT NOT NULL,
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                candle_time_utc TEXT NOT NULL,
                base_close REAL NOT NULL,
                last_close REAL NOT NULL,
                return_pct REAL NOT NULL,
                ts_utc TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_event_links ON event_links(symbol, interval, candle_time_utc)")
        conn.commit()

def upsert_candle(symbol: str, interval: str, open_time_utc: str, o: float, h: float, l: float, c: float, v: Optional[float], source: str) -> None:
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
    limit = max(10, min(int(limit), 3000))
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
        rows = list(reversed(cur.fetchall()))

    return [{"t": r["open_time_utc"], "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"], "volume": r["volume"]} for r in rows]

def insert_news_item(guid: str, source: str, title: str, link: str | None, summary: str | None, published: str | None, tags: str | None, score: float, signal: str, ts: Optional[datetime] = None) -> bool:
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

def insert_flow_event(event_id: str, symbol: str, side: str, price: float, quantity: float, notional_usd: float, source: str, ts: Optional[datetime] = None) -> bool:
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

def insert_orderbook_signal(signal_id: str, symbol: str, mid: float, spread_bps: float, bid_depth_usd: float, ask_depth_usd: float, imbalance: float,
                           top_wall_side: str, top_wall_usd: float, top_wall_price: float, source: str, ts: Optional[datetime] = None) -> bool:
    ts = ts or datetime.now(timezone.utc)
    with db() as conn:
        try:
            conn.execute(
                """
                INSERT INTO orderbook_signals(
                    signal_id, ts_utc, symbol, mid, spread_bps, bid_depth_usd, ask_depth_usd,
                    imbalance, top_wall_side, top_wall_usd, top_wall_price, source
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (signal_id, ts.isoformat(), symbol, float(mid), float(spread_bps), float(bid_depth_usd), float(ask_depth_usd),
                 float(imbalance), top_wall_side, float(top_wall_usd), float(top_wall_price), source),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

def find_nearest_candle_close(symbol: str, interval: str) -> tuple[str, float] | None:
    """
    Get latest candle time+close for symbol/interval.
    """
    with db() as conn:
        cur = conn.execute(
            """
            SELECT open_time_utc, close
            FROM ohlc_points
            WHERE symbol=? AND interval=?
            ORDER BY open_time_utc DESC
            LIMIT 1
            """,
            (symbol, interval),
        )
        r = cur.fetchone()
    if not r:
        return None
    return (r["open_time_utc"], float(r["close"]))

def upsert_event_link(link_id: str, news_guid: str, symbol: str, interval: str, candle_time_utc: str, base_close: float, last_close: float, return_pct: float) -> bool:
    ts = datetime.now(timezone.utc)
    with db() as conn:
        try:
            conn.execute(
                """
                INSERT INTO event_links(link_id, news_guid, symbol, interval, candle_time_utc, base_close, last_close, return_pct, ts_utc)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(link_id)
                DO UPDATE SET last_close=excluded.last_close, return_pct=excluded.return_pct, ts_utc=excluded.ts_utc
                """,
                (link_id, news_guid, symbol, interval, candle_time_utc, float(base_close), float(last_close), float(return_pct), ts.isoformat()),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

def get_recent_event_impacts(symbol: str, interval: str, limit: int = 3) -> list[dict]:
    with db() as conn:
        cur = conn.execute(
            """
            SELECT news_guid, candle_time_utc, return_pct
            FROM event_links
            WHERE symbol=? AND interval=?
            ORDER BY ts_utc DESC
            LIMIT ?
            """,
            (symbol, interval, max(1, min(limit, 10))),
        )
        rows = cur.fetchall()
    return [{"news_guid": r["news_guid"], "candle_time_utc": r["candle_time_utc"], "return_pct": float(r["return_pct"])} for r in rows]
