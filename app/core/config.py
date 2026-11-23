import os
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]
RULE_DIRECTORY = PROJECT_DIRECTORY / "data" / "graduation_rules"


def account_db_path() -> Path:
    return Path(os.getenv("JOLUP_DB_PATH") or PROJECT_DIRECTORY / "data" / "local" / "accounts.sqlite3").resolve()
