import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from functools import lru_cache
from pathlib import Path

from app.schemas.account import AccountResponse, Credentials, MeResponse, SessionResponse, StudentSettings
from app.schemas.evaluation import StudentRecords
from app.schemas.course import Course

SESSION_SECONDS = 8 * 60 * 60
PASSWORD_ROUNDS = 600_000


class DuplicateUsername(ValueError):
    pass


def _password_digest(password: str, salt: bytes, rounds: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)


def _token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


class AccountStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    password_rounds INTEGER NOT NULL,
                    profile_json TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash BLOB PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS student_records (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    records_json TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:
                yield conn

    def register(self, credentials: Credentials) -> AccountResponse:
        salt = secrets.token_bytes(16)
        password_hash = _password_digest(credentials.password.get_secret_value(), salt, PASSWORD_ROUNDS)
        with self.connect() as conn:
            row = conn.execute(
                """INSERT INTO users (username, password_salt, password_hash, password_rounds)
                VALUES (?, ?, ?, ?) ON CONFLICT(username) DO NOTHING RETURNING id, username""",
                (credentials.username, salt, password_hash, PASSWORD_ROUNDS),
            ).fetchone()
            if row is None:
                raise DuplicateUsername("이미 사용 중인 사용자 이름입니다.")
            return AccountResponse(**dict(row))

    def login(self, credentials: Credentials) -> SessionResponse | None:
        with self.connect() as conn:
            user = conn.execute("SELECT * FROM users WHERE username = ?", (credentials.username,)).fetchone()
        salt = user["password_salt"] if user else bytes(16)
        rounds = user["password_rounds"] if user else PASSWORD_ROUNDS
        digest = _password_digest(credentials.password.get_secret_value(), salt, rounds)
        if user is None or not hmac.compare_digest(digest, user["password_hash"]):
            return None

        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (_token_digest(token), user["id"], now + SESSION_SECONDS),
            )
        return SessionResponse(access_token=token, expires_in=SESSION_SECONDS)

    def get_me(self, token: str) -> MeResponse | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT users.id, users.username, users.profile_json FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?""",
                (_token_digest(token), int(time.time())),
            ).fetchone()
        if row is None:
            return None
        profile = StudentSettings.model_validate_json(row["profile_json"]) if row["profile_json"] is not None else None
        return MeResponse(account=AccountResponse(id=row["id"], username=row["username"]), profile=profile)

    def save_profile(self, user_id: int, profile: StudentSettings) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET profile_json = ? WHERE id = ?", (profile.model_dump_json(), user_id))

    def logout(self, token: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_digest(token),))

    def get_records(self, user_id: int) -> StudentRecords:
        with self.connect() as conn:
            row = conn.execute("SELECT records_json FROM student_records WHERE user_id = ?", (user_id,)).fetchone()
        return StudentRecords.model_validate_json(row["records_json"]) if row is not None else StudentRecords()

    def save_records(self, user_id: int, records: StudentRecords) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO student_records (user_id, records_json) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET records_json = excluded.records_json""",
                (user_id, records.model_dump_json()),
            )

    def append_courses(self, user_id: int, courses: list[Course]) -> int:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT records_json FROM student_records WHERE user_id = ?", (user_id,)).fetchone()
            records = StudentRecords.model_validate_json(row["records_json"]) if row is not None else StudentRecords()
            existing = {course.course_code for course in records.completed_courses}
            additions = [course for course in courses if course.course_code not in existing]
            if not additions:
                return 0
            updated = StudentRecords(
                completed_courses=[*records.completed_courses, *additions],
                nonacademic_records=records.nonacademic_records,
            )
            conn.execute(
                """INSERT INTO student_records (user_id, records_json) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET records_json = excluded.records_json""",
                (user_id, updated.model_dump_json()),
            )
            return len(additions)


@lru_cache(maxsize=1)
def get_account_store(path: Path) -> AccountStore:
    return AccountStore(path)
