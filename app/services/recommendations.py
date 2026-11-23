import json
import logging
import os
from collections import Counter
from functools import lru_cache
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.core.config import PROJECT_DIRECTORY
from app.schemas.course import Amount, Code, Course
from app.schemas.evaluation import Source, StudentRecords
from app.schemas.rules import GraduationRule
from app.schemas.student import StudentProfile
from app.services.course_metadata import ABEEK_PREREQUISITES, CAREER_TAGS, CareerTag
from app.services.graduation_engine.course_areas import LIBERAL_AREAS, curriculum_is_sourced, curriculum_table
from app.services.graduation_engine.evaluator import equivalent_codes, evaluate_course_requirements

logger = logging.getLogger(__name__)
DEFAULT_MODEL = "gpt-5.4-mini"


class ProgramCourse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Code
    name: str = Field(min_length=1)
    credits: Amount = Field(gt=0)
    curriculum_category: str | None = Field(default=None, description="Original course category in the program source.")
    career_tags: list[CareerTag] = Field(default_factory=list)


class Program(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: Literal["convergence", "double_major"]
    source: Source
    courses: list[ProgramCourse] = Field(min_length=1)


class RequirementGain(BaseModel):
    requirement_id: str
    name: str
    amount: Amount


class CourseRecommendation(BaseModel):
    course_code: Code
    course_name: str
    credits: Amount
    curriculum_category: str
    recommended_year: str | None = None
    recommended_semester: str | None = None
    gains: list[RequirementGain]
    career_tags: list[CareerTag]
    reason: str
    program_ids: list[str] = Field(default_factory=list)


class ProgramRecommendation(BaseModel):
    id: str
    name: str
    kind: Literal["convergence", "double_major"]
    overlapping_course_codes: list[Code]
    related_course_codes: list[Code]
    source: Source


class Recommendations(BaseModel):
    curriculum_source: Source
    inferred: bool
    missing_course_requirements: list[RequirementGain]
    recommended: list[CourseRecommendation]
    alternatives: list[CourseRecommendation]
    programs: list[ProgramRecommendation]
    program_data_available: bool
    ranking: Literal["rules", "openai"] = "rules"


@lru_cache(maxsize=1)
def programs_catalog() -> list[Program]:
    path = PROJECT_DIRECTORY / "data" / "recommendation_programs.json"
    try:
        return TypeAdapter(list[Program]).validate_json(path.read_bytes())
    except FileNotFoundError:
        return []


def openai_order(courses: list[CourseRecommendation], career: CareerTag, interests: Counter) -> list[str]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key or len(courses) < 2:
        return []
    candidates = [{"id": item.course_code, "name": item.course_name, "tags": item.career_tags} for item in courses]
    ids = [item.course_code for item in courses]
    try:
        response = httpx.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": os.getenv("JOLUP_OPENAI_MODEL", DEFAULT_MODEL),
                "store": False,
                "max_output_tokens": 350,
                "instructions": "Rank all candidates by career fit and continuity with the interest tags. "
                                "Return each supplied id exactly once. Use only the data supplied.",
                "input": json.dumps({"career": career, "interest_tags": interests, "candidates": candidates},
                                    ensure_ascii=False),
                "text": {"format": {"type": "json_schema", "name": "course_order", "strict": True, "schema": {
                    "type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string", "enum": ids}}},
                    "required": ["ids"], "additionalProperties": False,
                }}},
            },
            timeout=6,
        )
        response.raise_for_status()
        payload = response.json()
        if payload["status"] == "completed":
            for item in payload["output"]:
                if item["type"] != "message":
                    continue
                for content in item["content"]:
                    if content["type"] == "output_text":
                        order = json.loads(content["text"])["ids"]
                        if isinstance(order, list) and sorted(order) == sorted(ids):
                            return order
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        logger.warning("OpenAI recommendation ranking unavailable")
    return []


def recommend(
    student: StudentProfile, records: StudentRecords, rule: GraduationRule,
    career: CareerTag | None, use_ai: bool, target_semester: str | None,
) -> Recommendations:
    table = curriculum_table(student.major, student.admission_year)
    programs = programs_catalog()
    equivalents = equivalent_codes(rule)
    completed = {item.course_code for item in records.completed_courses
                 if item.status == "completed" and item.grade not in {"F", "NP"}}
    completed_canonical = {equivalents.get(code, code) for code in completed}
    excluded = completed_canonical | {equivalents.get(item.course_code, item.course_code)
                                      for item in records.completed_courses if item.status == "in_progress"}
    interests = Counter(tag for code in completed for tag in CAREER_TAGS.get(code, []))
    before, _ = evaluate_course_requirements(
        records.completed_courses, rule, student.admission_year, records.liberal_summary,
    )
    shortfalls = {item.id: item for item in before if item.shortfall > 0 and item.id != "gpa"}
    program_ids_by_course: dict[str, list[str]] = {}
    candidates = {code: (item.name, item.credits, item.category, item.grade_year or None, item.semester or None)
                  for code, item in table.courses.items()}
    program_tags = {}
    matched_programs = []
    for program in programs:
        for item in program.courses:
            candidates.setdefault(item.code, (item.name, item.credits, "일반선택", None, None))
            program_ids_by_course.setdefault(item.code, []).append(program.id)
            program_tags.setdefault(item.code, set()).update(item.career_tags)
        overlap = sorted({item.code for item in program.courses if item.code in completed})
        related = sorted({item.code for item in program.courses
                          if career in item.career_tags or any(interests[tag] for tag in item.career_tags)})
        if overlap or related:
            matched_programs.append(ProgramRecommendation(
                id=program.id, name=program.name, kind=program.kind, overlapping_course_codes=overlap,
                related_course_codes=related, source=program.source,
            ))
    matched_programs.sort(key=lambda item: (-len(item.overlapping_course_codes), -len(item.related_course_codes), item.id))

    ranked: list[tuple[tuple[int, float], CourseRecommendation]] = []
    for code, (name, credits, category, year, semester) in candidates.items():
        if equivalents.get(code, code) in excluded:
            continue
        if student.major == "plsop" and student.abeek and any(
            not any(equivalents.get(option, option) in completed_canonical for option in group)
            for group in ABEEK_PREREQUISITES.get(code, [])
        ):
            continue
        simulated_category = "전공필수" if any(
            req.required_transcript_category == "전공필수" and any(opt.course_code == code for opt in req.courses)
            for req in rule.course_requirements
        ) and category == "전공" else category
        simulated = Course(course_code=code, course_name=name, credits=credits, transcript_category=simulated_category,
                           semester=target_semester or "9999-1", status="completed", prior_institution=False)
        after, _ = evaluate_course_requirements(
            [*records.completed_courses, simulated], rule, student.admission_year, records.liberal_summary,
        )
        gains = [
            RequirementGain(requirement_id=item.id, name=item.name, amount=round(shortfalls[item.id].shortfall - item.shortfall, 4))
            for item in after if item.id in shortfalls and item.shortfall < shortfalls[item.id].shortfall
            and (target_semester or item.id not in LIBERAL_AREAS)
        ]
        tags = sorted(set(CAREER_TAGS.get(code, [])) | program_tags.get(code, set()))
        if not gains and not (not shortfalls and (career in tags or any(interests[tag] for tag in tags))):
            continue
        specific = [gain for gain in gains if gain.requirement_id != "total"]
        priority = (len(specific), sum(gain.amount for gain in specific or gains))
        ranked.append((priority, CourseRecommendation(
            course_code=code, course_name=name, credits=credits, curriculum_category=category,
            recommended_year=year, recommended_semester=semester, gains=gains, career_tags=tags,
            reason=("수강 시 충족에 도움이 되는 요건: " + ", ".join(gain.name for gain in specific or gains)
                    if gains else "선택한 진로 또는 기존 이수 분야와 연결되는 과목입니다."),
            program_ids=program_ids_by_course.get(code, []),
        )))
    ranked.sort(key=lambda pair: (-pair[0][0], -pair[0][1], career not in pair[1].career_tags,
                                  -sum(interests[tag] for tag in pair[1].career_tags), pair[1].course_code))
    items = [item for _, item in ranked]
    same_priority = [item for priority, item in ranked if priority == ranked[0][0]][:12] if ranked else []
    order = openai_order(same_priority, career, interests) if use_ai and career else []
    if order:
        positions = {code: index for index, code in enumerate(order)}
        items[:len(order)] = sorted(same_priority, key=lambda item: positions[item.course_code])
    return Recommendations(
        curriculum_source=table.source, inferred=not curriculum_is_sourced(student.major, student.admission_year),
        missing_course_requirements=[RequirementGain(requirement_id=item.id, name=item.name, amount=item.shortfall)
                                     for item in shortfalls.values()],
        recommended=items[:3], alternatives=items[3:], programs=matched_programs,
        program_data_available=bool(programs), ranking="openai" if order else "rules",
    )
