from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.config import RULE_DIRECTORY
from app.schemas.rules import GraduationRule
from app.schemas.student import Major, StudentProfile
from app.services.graduation_engine.bundle import RuleBundle, get_bundle_loader, matching_entries
from app.services.graduation_engine.rule_loader import RuleDataError, RuleUnavailableError, load_rules
from app.services.graduation_engine.rule_selector import RuleSource, candidate_rules, select_rules, validate_major

router = APIRouter(tags=["requirements"])


def get_rule_provider() -> Callable[[], RuleSource]:
    return load_rules_from_default_directory


def load_rules_from_default_directory() -> RuleSource:
    if (RULE_DIRECTORY.parents[1] / "jolup_rules_all" / "app" / "rules" / "index.json").is_file():
        return RuleBundle()
    return load_rules(RULE_DIRECTORY, aicom_path=RULE_DIRECTORY.parents[1] / "aicom_2024.rules.json")


class TracksResponse(BaseModel):
    tracks: list[str]
    allows_no_track: bool
    requires_abeek: bool = False
    track_labels: dict[str, str] = {}
    track_variants: dict[str, dict[str, str]] = {}


@router.get("/requirements/tracks")
def get_tracks(
    admission_year: Annotated[int, Query(ge=1900, le=2200)],
    major: Major,
    provide_rules: Annotated[Callable[[], RuleSource], Depends(get_rule_provider)],
) -> TracksResponse:
    student = StudentProfile(admission_year=admission_year, major=major)
    validate_major(student)
    rules = provide_rules()
    if isinstance(rules, RuleBundle):
        try:
            bundled_loader = get_bundle_loader()
            entries = matching_entries(student)
            variants = {
                track: {key: item["text"] for key, item in value.get("variants", {}).items()}
                for entry in entries for track, value in bundled_loader.load_rule_set(entry.id).get("tracks", {}).items()
                if value.get("variants")
            }
        except (OSError, ValueError, KeyError) as exc:
            raise RuleDataError("Invalid graduation rules bundle") from exc
        if not entries:
            raise RuleUnavailableError("No graduation rules for this major and admission year")
        tracks = sorted({track for entry in entries for track in entry.tracks or []})
        return TracksResponse(
            tracks=tracks, allows_no_track=any(not entry.tracks for entry in entries),
            requires_abeek=any(entry.abeek is not None for entry in entries), track_variants=variants,
            track_labels={"multi_major": "다중전공", "overseas_dual_degree": "해외복수학위",
                          "bs_ms_linked": "학·석사연계"} if tracks else {},
        )
    tracks = {track for rule in candidate_rules(student, rules) for track in rule.allowed_tracks}
    return TracksResponse(tracks=sorted(track for track in tracks if track is not None), allows_no_track=None in tracks)


@router.get("/requirements", description="연·월 및 트랙 선택은 [졸업요건 조회 화면](/requirements)에서 사용할 수 있습니다.")
def get_requirements(
    student: Annotated[StudentProfile, Query()], provide_rules: Annotated[Callable[[], RuleSource], Depends(get_rule_provider)],
) -> GraduationRule:
    validate_major(student)
    return select_rules(student, provide_rules())
