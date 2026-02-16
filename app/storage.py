diff --git a/app/storage.py b/app/storage.py
index 7b76691bda6040e6ae9bdd6a51ce60244741fa1e..391623f5c836c572684d5946ba9f071ac4c96f37 100644
--- a/app/storage.py
+++ b/app/storage.py
@@ -31,50 +31,67 @@ def init_db():
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
+
+        conn.execute(
+            """
+            CREATE TABLE IF NOT EXISTS flow_events (
+                id INTEGER PRIMARY KEY AUTOINCREMENT,
+                event_id TEXT NOT NULL UNIQUE,
+                ts_utc TEXT NOT NULL,
+                symbol TEXT NOT NULL,
+                side TEXT NOT NULL,
+                price REAL NOT NULL,
+                quantity REAL NOT NULL,
+                notional_usd REAL NOT NULL,
+                source TEXT NOT NULL
+            )
+            """
+        )
+        conn.execute("CREATE INDEX IF NOT EXISTS idx_flow_ts ON flow_events(ts_utc)")
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
@@ -163,25 +180,60 @@ def insert_news_item(
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
+
+
+def insert_flow_event(
+    event_id: str,
+    symbol: str,
+    side: str,
+    price: float,
+    quantity: float,
+    notional_usd: float,
+    source: str,
+    ts=None,
+) -> bool:
+    ts = ts or datetime.now(timezone.utc)
+    with db() as conn:
+        try:
+            conn.execute(
+                """
+                INSERT INTO flow_events(event_id, ts_utc, symbol, side, price, quantity, notional_usd, source)
+                VALUES(?,?,?,?,?,?,?,?)
+                """,
+                (
+                    event_id,
+                    ts.isoformat(),
+                    symbol,
+                    side,
+                    float(price),
+                    float(quantity),
+                    float(notional_usd),
+                    source,
+                ),
+            )
+            conn.commit()
+            return True
+        except sqlite3.IntegrityError:
+            return False
