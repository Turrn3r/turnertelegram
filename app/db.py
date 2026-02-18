import os
import sqlite3
import time
import secrets

DB_PATH = os.getenv("DB_PATH", "/data/app.db")

def connect():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.execute("""
        CREATE TABLE IF NOT EXISTS wallet_links (
            user_key TEXT PRIMARY KEY,
            wallet_address TEXT NOT NULL,
            linked_at INTEGER NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS nonces (
            user_key TEXT PRIMARY KEY,
            nonce TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)
    con.commit()
    return con

CON = connect()

def new_nonce(user_key: str) -> str:
    nonce = secrets.token_hex(16)
    CON.execute(
        "INSERT OR REPLACE INTO nonces (user_key, nonce, created_at) VALUES (?,?,?)",
        (user_key, nonce, int(time.time()))
    )
    CON.commit()
    return nonce

def get_nonce(user_key: str):
    row = CON.execute(
        "SELECT nonce, created_at FROM nonces WHERE user_key=?",
        (user_key,)
    ).fetchone()
    return row  # (nonce, created_at) or None

def clear_nonce(user_key: str):
    CON.execute("DELETE FROM nonces WHERE user_key=?", (user_key,))
    CON.commit()

def save_link(user_key: str, wallet_address: str):
    CON.execute(
        "INSERT OR REPLACE INTO wallet_links (user_key, wallet_address, linked_at) VALUES (?,?,?)",
        (user_key, wallet_address, int(time.time()))
    )
    CON.commit()

def get_link(user_key: str):
    row = CON.execute(
        "SELECT wallet_address, linked_at FROM wallet_links WHERE user_key=?",
        (user_key,)
    ).fetchone()
    return row  # (address, linked_at) or None
