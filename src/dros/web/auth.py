from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from dros.settings import DrosSettings

COOKIE_NAME = "dros_session"
LONG_SESSION_SECONDS = 90 * 24 * 60 * 60
TEMP_SESSION_SECONDS = 24 * 60 * 60
PBKDF2_ITERATIONS = 310_000


@dataclass(frozen=True)
class AuthSession:
    token: str
    username: str
    persistent: bool
    expires_at: int

    @property
    def max_age(self) -> int | None:
        if not self.persistent:
            return None
        return LONG_SESSION_SECONDS


def resolve_auth_db_path(settings: DrosSettings) -> Path:
    if settings.web.auth_db is not None:
        return settings.web.auth_db
    return settings.paths.run / "web-auth.sqlite3"


class WebAuthStore:
    def __init__(self, db_path: Path | str | None) -> None:
        if db_path is None:
            raise ValueError("web auth database path is required")
        self.db_path = Path(db_path)

    def create_user(self, username: str, password: str) -> None:
        username = _normalize_username(username)
        password_hash = hash_password(password)
        now = _now()
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO users (username, password_hash, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (username, password_hash, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"web user already exists: {username}") from exc

    def set_password(self, username: str, password: str) -> None:
        username = _normalize_username(username)
        password_hash = hash_password(password)
        now = _now()
        with self._connect() as conn:
            result = conn.execute(
                """
                UPDATE users
                SET password_hash = ?, updated_at = ?
                WHERE username = ?
                """,
                (password_hash, now, username),
            )
            if result.rowcount == 0:
                raise ValueError(f"web user not found: {username}")
            conn.execute("DELETE FROM sessions WHERE username = ?", (username,))

    def verify_password(self, username: str, password: str) -> bool:
        username = _normalize_username(username)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT password_hash FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        if row is None:
            return False
        return verify_password_hash(password, row["password_hash"])

    def create_session(self, username: str, *, persistent: bool) -> AuthSession:
        username = _normalize_username(username)
        now = _now()
        expires_at = now + (LONG_SESSION_SECONDS if persistent else TEMP_SESSION_SECONDS)
        token = secrets.token_urlsafe(32)
        token_hash = hash_session_token(token)
        with self._connect() as conn:
            if not self._user_exists(conn, username):
                raise ValueError(f"web user not found: {username}")
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            conn.execute(
                """
                INSERT INTO sessions (token_hash, username, created_at, expires_at, persistent)
                VALUES (?, ?, ?, ?, ?)
                """,
                (token_hash, username, now, expires_at, int(persistent)),
            )
        return AuthSession(
            token=token,
            username=username,
            persistent=persistent,
            expires_at=expires_at,
        )

    def username_for_session(self, token: str | None) -> str | None:
        if not token:
            return None
        token_hash = hash_session_token(token)
        now = _now()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT username, expires_at
                FROM sessions
                WHERE token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if int(row["expires_at"]) <= now:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                return None
            return str(row["username"])

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE token_hash = ?",
                (hash_session_token(token),),
            )

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema(conn)
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              username TEXT PRIMARY KEY,
              password_hash TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY,
              username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
              created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL,
              persistent INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_username ON sessions(username);
            CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
            """
        )

    def _user_exists(self, conn: sqlite3.Connection, username: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        return row is not None


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password cannot be empty")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        _b64encode(salt),
        _b64encode(digest),
    )


def verify_password_hash(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected = password_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    try:
        iteration_count = int(iterations)
        salt_bytes = _b64decode(salt)
        expected_bytes = _b64decode(expected)
    except (TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_bytes,
        iteration_count,
    )
    return hmac.compare_digest(actual, expected_bytes)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_username(username: str) -> str:
    normalized = username.strip()
    if not normalized:
        raise ValueError("username cannot be empty")
    return normalized


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))


def _now() -> int:
    return int(time.time())
