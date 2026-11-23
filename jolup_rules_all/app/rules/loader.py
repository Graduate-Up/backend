"""졸업요건 규칙 파일 로더.

    from app.rules.loader import select_rule_set, resolve

    rs = select_rule_set("plsop", 2023, abeek=False)          # base 파일 1개
    req = resolve(rs, status="new", multi_major="minor_1_only", admission_year=2023)
    req.total_credits, req.major_credits                      # 140, 77

규칙 파일은 읽기 전용 데이터다. 판정(satisfied/unsatisfied/...)은 엔진에서 하고,
여기서는 "이 학생에게 적용되는 숫자·조건"까지만 계산한다.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel

RULES_DIR = Path(__file__).resolve().parent

Major = Literal["glsop", "plsop", "aicom", "simcom", "chumcom"]
Status = Literal["new", "general_transfer", "bachelor_transfer", "foreign", "disabled"]
MultiMajor = Literal["none", "double_or_linked_or_convergence", "minor_1_only", "minor_2plus", "teaching_course"]
Track = Literal["multi_major", "overseas_dual_degree", "bs_ms_linked"]


class RuleSetEntry(BaseModel):
    id: str
    file: str
    major: Major
    admission_from: int
    admission_to: int
    abeek: Optional[bool] = None
    tracks: Optional[list[Track]] = None


class NoRuleSet(LookupError):
    """학번·전공 조합에 맞는 규칙 파일이 없음 (예: 2021학번 인컴)."""


class EnglishRequirement(BaseModel):
    department_min_toeic: Optional[int] = None   # 학부 요건 (면제 안 됨)
    alternative: Optional[str] = None            # 예: 120시간 대체
    exempt: bool = False                         # 인컴 + 실용영어 면제 신분
    topik4_instead: bool = False                 # 외국인 22학번~ 실용영어 대체


class Requirements(BaseModel):
    rule_set_id: str
    total_credits: int
    major_credits: int                    # 학교 기준 전공학점
    engineering_major_credits: Optional[int] = None  # 플솝 ABEEK 공학전공
    liberal_arts_min: Optional[int]       # None = 학교 교양 미적용(ABEEK) 또는 전부 인정(학사편입)
    liberal_arts_areas_required: bool
    sdg_min: int
    required_course_codes: list[str]
    english: EnglishRequirement
    field_practice_credits: int
    qualification_exempt: list[str]       # 졸업자격인정제 면제 항목
    early_graduation_allowed: bool
    inferred: bool                        # 추정 값이 섞였는지
    needs_confirmation: list[str]


def _read(rel: str):
    with open(RULES_DIR / rel, encoding="utf-8") as f:
        return json.load(f)


@lru_cache
def load_index() -> list[RuleSetEntry]:
    return [RuleSetEntry(**e) for e in _read("index.json")["rule_sets"]]


@lru_cache
def load_rule_set(rule_set_id: str) -> dict:
    entry = next((e for e in load_index() if e.id == rule_set_id), None)
    if entry is None:
        raise NoRuleSet(rule_set_id)
    return _read(entry.file)


@lru_cache
def load_common(name: str) -> dict:
    return _read(f"common/{name}.json")


def select_rule_set(major: Major, admission_year: int, abeek: Optional[bool] = None) -> dict:
    if major == "plsop" and abeek is None:
        raise ValueError("플솝은 abeek(True/False)를 넘겨야 함")
    hits = [e for e in load_index()
            if e.major == major and e.admission_from <= admission_year <= e.admission_to
            and (e.abeek is None or e.abeek == abeek)]
    if len(hits) != 1:
        raise NoRuleSet(f"{major} {admission_year} abeek={abeek}: {len(hits)}개")
    return load_rule_set(hits[0].id)


def _has_inferred(o) -> bool:
    if isinstance(o, dict):
        return o.get("inferred") is True or any(_has_inferred(v) for v in o.values())
    if isinstance(o, list):
        return any(_has_inferred(v) for v in o)
    return False


def resolve(rs: dict, status: Status = "new", multi_major: MultiMajor = "none",
            track: Optional[Track] = None, admission_year: Optional[int] = None) -> Requirements:
    """admission_year: 구간이 여러 학번을 묶은 파일(예: plsop_2021-2022)에서 외국인 기준처럼
    학번마다 갈리는 값을 고를 때 필요. 생략하면 구간 첫 학번."""
    lo, hi = rs["admission_years"]
    year = admission_year if admission_year is not None else lo
    if not lo <= year <= hi:
        raise ValueError(f"{rs['id']}는 {lo}~{hi}학번용")
    credits = rs["credits"]
    statuses = load_common("statuses")["statuses"]
    st = statuses[status]
    overrides = rs.get("status_overrides", {}).get(status, {})

    if "tracks" in rs and track is None:
        raise ValueError("글솝은 track을 넘겨야 함")

    # 전공학점
    major = credits["major"]["by_multi_major"][multi_major]
    if "major_credits" in overrides:
        mc = overrides["major_credits"]
        major = mc["minor_1_only"] if multi_major == "minor_1_only" else mc["default"]

    # 교양
    la = credits["liberal_arts"]
    abeek = rs["selector"].get("abeek") is True
    if abeek or status == "bachelor_transfer":
        la_min, areas, sdg = None, False, 0
    else:
        la_min = la["min"]
        areas = bool(la.get("areas")) and st.get("liberal_arts", {}).get("areas_required", True)
        sdg = la.get("sdg_min", 0) if st.get("liberal_arts", {}).get("sdg_required", True) else 0

    # 졸업자격인정제 면제
    if status == "foreign":
        band = next(b for b in st["by_admission_year"]
                    if b["admission_years"][0] <= year <= b["admission_years"][1])
        exempt = band["qualification_exempt"]
        topik = "practical_english_substitute" in band
    else:
        exempt, topik = st["qualification_exempt"], False

    # 영어
    e = rs["english"]
    eng = EnglishRequirement(topik4_instead=topik)
    if e.get("department_requirement"):
        if "by_track" in e:
            eng.department_min_toeic = e["by_track"][track]["min"]
        else:
            eng.department_min_toeic = e["min"]["min"]
            eng.alternative = e.get("alternative")
    else:  # 인컴: 실용영어로 갈음
        eng.exempt = "practical_english" in exempt

    # 현장실습
    fp = rs["field_practice"]
    fp_credits = fp["by_track"][track] if "by_track" in fp else fp.get("min_credits", 0)

    req_codes = [c["code"] for c in rs["required_courses"]["all_of"]]
    early = status not in load_common("policies")["early_graduation"]["not_for"]

    return Requirements(
        rule_set_id=rs["id"], total_credits=credits["total"]["min"], major_credits=major,
        engineering_major_credits=credits.get("abeek", {}).get("engineering_major"),
        liberal_arts_min=la_min, liberal_arts_areas_required=areas, sdg_min=sdg,
        required_course_codes=req_codes, english=eng, field_practice_credits=fp_credits,
        qualification_exempt=exempt, early_graduation_allowed=early,
        inferred=_has_inferred(rs), needs_confirmation=rs.get("needs_confirmation", []),
    )
