from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from app.schemas.rules import GraduationRule, RuleFile

from .aicom_import import import_aicom_rule


class RuleDataError(Exception):
    pass


class RuleUnavailableError(RuleDataError):
    pass


def load_rules(
    directory: Path, dataset: Literal["official", "test"] = "official", *, aicom_path: Path | None = None,
) -> list[GraduationRule]:
    try:
        paths = sorted(path for path in directory.iterdir() if path.suffix == ".json")
    except FileNotFoundError:
        paths = []
    except OSError as exc:
        raise RuleDataError("Cannot read graduation rule directory") from exc

    rules = []
    if dataset == "official" and aicom_path is not None:
        try:
            rules.append(import_aicom_rule(aicom_path))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError) as exc:
            raise RuleDataError(f"Invalid graduation rule file: {aicom_path.name}") from exc
    for path in paths:
        try:
            rule_file = RuleFile.model_validate_json(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValidationError) as exc:
            raise RuleDataError(f"Invalid graduation rule file: {path.name}") from exc
        if any(rule.dataset != dataset for rule in rule_file.rules):
            raise RuleDataError(f"Unexpected dataset in graduation rule file: {path.name}")
        rules.extend(rule_file.rules)
    if not rules:
        raise RuleUnavailableError("No graduation rule JSON files are available")
    if len({rule.id for rule in rules}) != len(rules):
        raise RuleDataError("Duplicate graduation rule IDs")
    return rules
