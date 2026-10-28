"""База пользователей бота (SQLite, файл /data/bot.db)."""
import secrets
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id       INTEGER PRIMARY KEY,
    username    TEXT,
    full_name   TEXT,
    status      TEXT NOT NULL,            -- pending | active | blocked | rejected
    sub_token   TEXT UNIQUE,
    created_at  INTEGER NOT NULL,
    approved_at INTEGER
);
CREATE TABLE IF NOT EXISTS clients (      -- один пользователь = по клиенту на каждое подключение панели
    tg_id INTEGER NOT NULL,
    node  TEXT NOT NULL,
    uuid  TEXT NOT NULL,
    email TEXT NOT NULL,
    PRIMARY KEY (tg_id, node)
);
CREATE TABLE IF NOT EXISTS invites (
    code       TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    used_by    INTEGER,
    used_at    INTEGER
);
"""


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # --- пользователи ---
    def get_user(self, tg_id: int):
        return self.conn.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()

    def get_by_token(self, token: str):
        return self.conn.execute("SELECT * FROM users WHERE sub_token=?", (token,)).fetchone()

    def upsert_user(self, tg_id: int, username: str | None, full_name: str, status: str):
        self.conn.execute(
            """INSERT INTO users (tg_id, username, full_name, status, created_at) VALUES (?,?,?,?,?)
               ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name,
               status=excluded.status""",
            (tg_id, username, full_name, status, int(time.time())),
        )

    def touch_user(self, tg_id: int, username: str | None, full_name: str):
        self.conn.execute("UPDATE users SET username=?, full_name=? WHERE tg_id=?", (username, full_name, tg_id))

    def set_status(self, tg_id: int, status: str):
        self.conn.execute("UPDATE users SET status=? WHERE tg_id=?", (status, tg_id))

    def activate(self, tg_id: int, token: str):
        self.conn.execute(
            "UPDATE users SET status='active', sub_token=?, approved_at=? WHERE tg_id=?",
            (token, int(time.time()), tg_id),
        )

    def delete_user(self, tg_id: int):
        self.conn.execute("DELETE FROM clients WHERE tg_id=?", (tg_id,))
        self.conn.execute("DELETE FROM users WHERE tg_id=?", (tg_id,))

    def list_users(self, statuses=("active", "blocked")):
        marks = ",".join("?" * len(statuses))
        return self.conn.execute(
            f"SELECT * FROM users WHERE status IN ({marks}) ORDER BY approved_at, created_at", statuses
        ).fetchall()

    def count(self, status: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM users WHERE status=?", (status,)).fetchone()[0]

    def seats_taken(self) -> int:
        """Места занимают и активные, и заблокированные (у них остаются ключи в панели)."""
        return self.conn.execute("SELECT COUNT(*) FROM users WHERE status IN ('active','blocked')").fetchone()[0]

    # --- клиенты панели ---
    def set_client(self, tg_id: int, node: str, uuid: str, email: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO clients (tg_id, node, uuid, email) VALUES (?,?,?,?)", (tg_id, node, uuid, email)
        )

    def clients(self, tg_id: int):
        return self.conn.execute("SELECT * FROM clients WHERE tg_id=?", (tg_id,)).fetchall()

    def drop_clients(self, tg_id: int):
        self.conn.execute("DELETE FROM clients WHERE tg_id=?", (tg_id,))

    # --- приглашения ---
    def new_invite(self) -> str:
        code = "inv" + secrets.token_urlsafe(9).replace("-", "").replace("_", "")
        self.conn.execute("INSERT INTO invites (code, created_at) VALUES (?,?)", (code, int(time.time())))
        return code

    def take_invite(self, code: str, tg_id: int) -> bool:
        cur = self.conn.execute(
            "UPDATE invites SET used_by=?, used_at=? WHERE code=? AND used_by IS NULL", (tg_id, int(time.time()), code)
        )
        return cur.rowcount == 1
