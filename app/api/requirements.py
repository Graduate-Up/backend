from collections.abc import Callable
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.config import RULE_DIRECTORY
from app.schemas.evaluation import EarlyGraduationPolicy, Source
from app.schemas.rules import GraduationRule
from app.schemas.student import Major, StudentProfile
from app.services.graduation_engine.bundle import RuleBundle, early_graduation_policy, get_bundle_loader, matching_entries
from app.services.graduation_engine.rule_loader import RuleDataError, RuleUnavailableError, load_rules
from app.services.graduation_engine.rule_selector import RuleSource, candidate_rules, select_rules, validate_major

router = APIRouter(tags=["requirements"])


class QualificationDeadline(BaseModel):
    graduation: str = Field(pattern=r"^[0-9]{4}-(02|08)$")
    date: date
    note: str | None = None
    sources: list[Source]


class DepartmentDeadline(BaseModel):
    label: str
    sources: list[Source]


class GraduationEvent(BaseModel):
    kind: Literal["portfolio_submission", "graduation_interview", "classification_change",
                  "general_elective_conversion", "graduation_review_result"]
    graduation: str
    major: Major | None = None
    admission_from: int | None = None
    admission_to: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: Literal["confirmed", "tentative", "standing_rule", "unverified"]
    note: str | None = None
    sources: list[Source]


class ClassificationChange(BaseModel):
    department_code: str = Field(description="학부 온라인 신청 안내에 기재된 컴퓨터학부 코드")
    major_department_codes: dict[Major, str]
    major_sources: dict[Major, list[Source]]
    form_url: str
    form_name: str
    sources: list[Source]


class DeferralTuition(BaseModel):
    min_credits: int
    max_credits: int | None = None
    tuition_fraction: str


class GraduationDeferral(BaseModel):
    max_semesters: int
    min_completed_semesters: int
    facility_fee_percent: int
    tuition_by_credits: list[DeferralTuition]
    note: str
    sources: list[Source]


class GraduationSchedule(BaseModel):
    qualification_deadlines: list[QualificationDeadline]
    department_deadline: DepartmentDeadline
    early_graduation: EarlyGraduationPolicy
    events: list[GraduationEvent]
    classification_change: ClassificationChange
    deferral: GraduationDeferral


@router.get("/graduation/schedule")
def graduation_schedule(
    graduation: Annotated[str | None, Query(pattern=r"^[0-9]{4}-(02|08)$")] = None,
    major: Major | None = None,
    admission_year: Annotated[int | None, Query(ge=1900, le=2200)] = None,
) -> GraduationSchedule:
    try:
        loader = get_bundle_loader()
        deadline = loader.load_common("graduation_qualification")["deadline"]
        early = loader.load_common("policies")["early_graduation"]
        schedule = loader.load_common("graduation_schedule")
        sources = {
            item["id"]: Source(title=item["title"], url=item.get("url"), reference=item.get("date"))
            for item in loader.load_common("sources")["sources"]
        }
        sources.update({key: Source(**item) for key, item in schedule["sources"].items()})

        def cited(item):
            return {**item, "sources": [sources[key] for key in item["source"]]}

        classification = schedule["classification_change"]
        return GraduationSchedule(
            qualification_deadlines=[QualificationDeadline(**cited(item)) for item in deadline["qualification_form"]
                                     if graduation is None or item["graduation"] == graduation],
            department_deadline=DepartmentDeadline(**cited(deadline["department_requirements"])),
            early_graduation=early_graduation_policy(early, cited(early)["sources"]),
            events=[GraduationEvent(**cited(item)) for item in schedule["events"]
                    if (graduation is None or item["graduation"] == graduation)
                    and (major is None or item.get("major") in {None, major})
                    and (admission_year is None or item.get("admission_from", 1900) <= admission_year
                         <= item.get("admission_to", 2200))],
            classification_change=ClassificationChange(
                **cited(classification),
                major_sources={key: [sources[ref] for ref in refs] for key, refs in classification["major_source"].items()},
            ),
            deferral=GraduationDeferral(**cited(schedule["deferral"])),
        )
    except (ModuleNotFoundError, FileNotFoundError) as exc:
        raise RuleUnavailableError("졸업 일정에 필요한 공통 규칙 파일이 없습니다.") from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuleDataError("졸업 일정 공통 규칙을 읽을 수 없습니다.") from exc


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
