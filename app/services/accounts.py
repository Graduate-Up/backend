import hashlib
import hmac
import secrets
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing, contextmanager
from functools import lru_cache
from pathlib import Path

from app.schemas.account import (
    AccountResponse, Credentials, MeResponse, PasswordReset, Registration, SessionResponse, StudentSettings,
)
from app.schemas.evaluation import LiberalSummary, NonacademicRecord, StudentRecords
from app.schemas.course import Course

SESSION_SECONDS = 8 * 60 * 60
PASSWORD_ROUNDS = 600_000
EMAIL_CODE_TTL = 600
EMAIL_CODE_COOLDOWN = 60
EMAIL_CODE_ATTEMPTS = 5


class DuplicateUsername(ValueError):
    pass


class InvalidEmailCode(ValueError):
    pass


class InvalidLegacyAccount(ValueError):
    pass


class EmailCodeRateLimit(ValueError):
    pass


def _email_code_digest(secret: bytes, email: str, nonce: str, code: str) -> bytes:
    return hmac.digest(secret, f"{email}:{nonce}:{code}".encode(), "sha256")


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
                CREATE TABLE IF NOT EXISTS verified_accounts (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS registration_codes (
                    email TEXT PRIMARY KEY,
                    nonce TEXT NOT NULL,
                    digest BLOB NOT NULL,
                    issuer TEXT NOT NULL,
                    sent_at INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS registration_codes_issuer ON registration_codes(issuer, sent_at);
                CREATE TABLE IF NOT EXISTS student_records (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    records_json TEXT NOT NULL
                );
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(registration_codes)")}
            if "purpose" not in columns:
                # 가입용 인증번호와 비밀번호 재설정용 인증번호를 구분한다 (기존 DB는 가입용으로 본다)
                conn.execute("ALTER TABLE registration_codes ADD COLUMN purpose TEXT NOT NULL DEFAULT 'register'")

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:
                yield conn

    def issue_email_code(self, email: str, issuer: str, secret: bytes, purpose: str = "register") -> str | None:
        """인증번호를 만들어 저장하고, 보낼 인증번호를 돌려준다.

        비밀번호 재설정(purpose="reset")은 가입되지 않은 이메일이면 None을 돌려준다. 이때도 같은 행을 저장해
        재요청 제한이 똑같이 걸리게 하므로, 응답만으로 가입 여부를 알 수 없다.
        """
        code = f"{secrets.randbelow(1_000_000):06d}"
        nonce = secrets.token_hex(16)
        now = int(time.time())
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM registration_codes WHERE sent_at <= ?", (now - 3600,))
            previous = conn.execute("SELECT sent_at FROM registration_codes WHERE email = ?", (email,)).fetchone()
            recent = conn.execute("SELECT COUNT(*) FROM registration_codes WHERE issuer = ?", (issuer,)).fetchone()[0]
            if (previous and now - previous["sent_at"] < EMAIL_CODE_COOLDOWN) or (previous is None and recent >= 10):
                raise EmailCodeRateLimit()
            conn.execute(
                """INSERT INTO registration_codes (email, nonce, digest, issuer, sent_at, purpose)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(email) DO UPDATE SET nonce = excluded.nonce,
                digest = excluded.digest, issuer = excluded.issuer, sent_at = excluded.sent_at, attempts = 0,
                purpose = excluded.purpose""",
                (email, nonce, _email_code_digest(secret, email, nonce, code), issuer, now, purpose),
            )
            if purpose == "reset" and self._verified_user(conn, email) is None:
                return None
        return code

    @staticmethod
    def _verified_user(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT users.* FROM users JOIN verified_accounts ON users.id = verified_accounts.user_id WHERE username = ?",
            (email,),
        ).fetchone()

    @staticmethod
    def _check_email_code(conn: sqlite3.Connection, email: str, code: str, purpose: str, secret: bytes) -> None:
        """인증번호가 맞는지 확인한다. 틀리면 시도 횟수를 올리고 InvalidEmailCode."""
        pending = conn.execute("SELECT * FROM registration_codes WHERE email = ?", (email,)).fetchone()
        if (pending is None or pending["purpose"] != purpose or time.time() >= pending["sent_at"] + EMAIL_CODE_TTL
                or pending["attempts"] >= EMAIL_CODE_ATTEMPTS):
            raise InvalidEmailCode()
        if not hmac.compare_digest(_email_code_digest(secret, email, pending["nonce"], code), pending["digest"]):
            conn.execute("UPDATE registration_codes SET attempts = attempts + 1 WHERE email = ?", (email,))
            conn.commit()
            raise InvalidEmailCode()

    @staticmethod
    def _new_session(conn: sqlite3.Connection, user_id: int) -> SessionResponse:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (_token_digest(token), user_id, now + SESSION_SECONDS),
        )
        return SessionResponse(access_token=token, expires_in=SESSION_SECONDS)

    def reset_password(self, reset: PasswordReset, secret: bytes) -> SessionResponse:
        """인증번호가 맞으면 새 비밀번호로 바꾸고, 기존 세션을 모두 끊은 뒤 새 세션을 돌려준다."""
        salt = secrets.token_bytes(16)
        password_hash = _password_digest(reset.password.get_secret_value(), salt, PASSWORD_ROUNDS)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._check_email_code(conn, reset.email, reset.code.get_secret_value(), "reset", secret)
            user = self._verified_user(conn, reset.email)
            if user is None:
                raise InvalidEmailCode()
            conn.execute(
                "UPDATE users SET password_salt = ?, password_hash = ?, password_rounds = ? WHERE id = ?",
                (salt, password_hash, PASSWORD_ROUNDS, user["id"]),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
            conn.execute("DELETE FROM registration_codes WHERE email = ?", (reset.email,))
            return self._new_session(conn, user["id"])

    def register(self, credentials: Registration, secret: bytes) -> AccountResponse:
        salt = secrets.token_bytes(16)
        password = credentials.password.get_secret_value()
        password_hash = _password_digest(password, salt, PASSWORD_ROUNDS)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._check_email_code(conn, credentials.email, credentials.code.get_secret_value(), "register", secret)
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (credentials.email,)).fetchone():
                raise DuplicateUsername("이미 가입된 학교 이메일입니다.")
            if credentials.legacy_username is not None:
                legacy = conn.execute(
                    """SELECT * FROM users WHERE username = ? AND id NOT IN (SELECT user_id FROM verified_accounts)""",
                    (credentials.legacy_username,),
                ).fetchone()
                if legacy is None or not hmac.compare_digest(
                    _password_digest(password, legacy["password_salt"], legacy["password_rounds"]), legacy["password_hash"],
                ):
                    raise InvalidLegacyAccount()
                user_id = legacy["id"]
                conn.execute("UPDATE users SET username = ? WHERE id = ?", (credentials.email, user_id))
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            else:
                user_id = conn.execute(
                    "INSERT INTO users (username, password_salt, password_hash, password_rounds) VALUES (?, ?, ?, ?)",
                    (credentials.email, salt, password_hash, PASSWORD_ROUNDS),
                ).lastrowid
            conn.execute("INSERT INTO verified_accounts (user_id) VALUES (?)", (user_id,))
            conn.execute("DELETE FROM registration_codes WHERE email = ?", (credentials.email,))
            return AccountResponse(id=user_id, username=credentials.email)

    def login(self, credentials: Credentials) -> SessionResponse | None:
        with self.connect() as conn:
            user = self._verified_user(conn, credentials.email)
        salt = user["password_salt"] if user else bytes(16)
        rounds = user["password_rounds"] if user else PASSWORD_ROUNDS
        digest = _password_digest(credentials.password.get_secret_value(), salt, rounds)
        if user is None or not hmac.compare_digest(digest, user["password_hash"]):
            return None
        with self.connect() as conn:
            return self._new_session(conn, user["id"])

    def get_me(self, token: str) -> MeResponse | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT users.id, users.username, users.profile_json FROM sessions
                JOIN users ON users.id = sessions.user_id
                JOIN verified_accounts ON users.id = verified_accounts.user_id
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

    def save_records(self, user_id: int, records: StudentRecords) -> StudentRecords:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT records_json FROM student_records WHERE user_id = ?", (user_id,)).fetchone()
            if row is not None:
                saved = StudentRecords.model_validate_json(row["records_json"])
                if "liberal_summary" not in records.model_fields_set:
                    records.liberal_summary = saved.liberal_summary
                # Existing clients can resubmit the amount without the original test result.
                english_tests = {(rec.requirement_id, rec.amount, rec.status): rec.english_test
                                 for rec in saved.nonacademic_records if rec.english_test is not None}
                for record in records.nonacademic_records:
                    key = (record.requirement_id, record.amount, record.status)
                    if "english_test" not in record.model_fields_set and key in english_tests:
                        record.english_test = english_tests[key]
            conn.execute(
                """INSERT INTO student_records (user_id, records_json) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET records_json = excluded.records_json""",
                (user_id, records.model_dump_json()),
            )
        return records

    def append_courses(
        self, user_id: int, courses: list[Course], liberal_summary: LiberalSummary | None = None,
    ) -> tuple[int, int]:
        """Add courses whose code is new and fill in missing grades of already saved courses.

        Returns (added, grades_filled).
        """
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT records_json FROM student_records WHERE user_id = ?", (user_id,)).fetchone()
            records = StudentRecords.model_validate_json(row["records_json"]) if row is not None else StudentRecords()
            incoming = {(course.course_code, course.semester): course for course in courses if course.grade}
            filled = 0
            saved = []
            for course in records.completed_courses:
                match = incoming.get((course.course_code, course.semester))
                if course.grade is None and match is not None:
                    course = course.model_copy(update={"grade": match.grade})
                    filled += 1
                saved.append(course)
            existing = {course.course_code for course in saved}
            additions = [course for course in courses if course.course_code not in existing]
            if not additions and not filled and (liberal_summary is None or liberal_summary == records.liberal_summary):
                return 0, 0
            updated = StudentRecords(
                completed_courses=[*saved, *additions],
                nonacademic_records=records.nonacademic_records,
                liberal_summary=liberal_summary if liberal_summary is not None else records.liberal_summary,
            )
            conn.execute(
                """INSERT INTO student_records (user_id, records_json) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET records_json = excluded.records_json""",
                (user_id, updated.model_dump_json()),
            )
            return len(additions), filled


    def update_nonacademic(
        self, user_id: int, change: Callable[[list[NonacademicRecord]], list[NonacademicRecord]],
    ) -> StudentRecords:
        """Replace only the nonacademic records atomically, keeping the course list untouched."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT records_json FROM student_records WHERE user_id = ?", (user_id,)).fetchone()
            records = StudentRecords.model_validate_json(row["records_json"]) if row is not None else StudentRecords()
            updated = StudentRecords(
                completed_courses=records.completed_courses,
                nonacademic_records=change(records.nonacademic_records),
                liberal_summary=records.liberal_summary,
            )
            conn.execute(
                """INSERT INTO student_records (user_id, records_json) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET records_json = excluded.records_json""",
                (user_id, updated.model_dump_json()),
            )
            return updated

@lru_cache(maxsize=1)
def get_account_store(path: Path) -> AccountStore:
    return AccountStore(path)
