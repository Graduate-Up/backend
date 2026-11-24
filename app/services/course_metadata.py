import re
from functools import lru_cache
from typing import Literal
from unicodedata import normalize

from app.schemas.evaluation import Source
from app.services.graduation_engine.course_areas import MAJOR_CATEGORIES, curricula

# 컴퓨터학부 공지 28590. 개설 학부와 학번별 주전공 인정 여부는 별도다.
CSE_CAPSTONE_CODES = {"ITEC0401", "ITEC0402", "GLSO0218", "GLSO0221"}
COURSE_CODE_PATTERN = re.compile(r"([A-Z]{4})[0-9]{4}")


def course_name_key(name: str) -> str:
    return "".join(normalize("NFKC", name).casefold().split())


@lru_cache(maxsize=1)
def curriculum_offering_index() -> tuple[set[str], set[tuple[str, str]]]:
    codes, names_by_prefix = set(), set()
    for major, tables in curricula().items():
        for table in tables.values():
            for course in table.courses.values():
                if course.category not in MAJOR_CATEGORIES[major]:
                    continue
                code_match = COURSE_CODE_PATTERN.fullmatch(course.code)
                if code_match:
                    codes.add(course.code)
                    names_by_prefix.add((code_match[1], course_name_key(course.name)))
    return codes, names_by_prefix


def computer_science_offering(
    course_code: str, recorded: bool | None = None, *, prior_institution: bool | None = None,
    course_name: str | None = None,
) -> bool | None:
    if prior_institution is not True and course_code in CSE_CAPSTONE_CODES:
        return True
    if recorded is not None or prior_institution is True:
        return recorded
    code_match = COURSE_CODE_PATTERN.fullmatch(course_code)
    if code_match is None:
        return None
    try:
        codes, names_by_prefix = curriculum_offering_index()
    except FileNotFoundError:
        return None
    if course_code in codes or (course_name and (code_match[1], course_name_key(course_name)) in names_by_prefix):
        return True
    return None


CareerTag = Literal["ai_data", "backend", "security", "graduate_study"]
CAREER_TAGS: dict[str, list[CareerTag]] = {
    "COMP0218": ["ai_data"],
    "COMP0217": ["backend"],
    "COMP0224": ["backend"],
    "COMP0312": ["backend", "security"],
    "COMP0322": ["backend", "ai_data"],
    "COME0427": ["backend", "ai_data"],
    "COMP0324": ["ai_data"],
    "COMP0333": ["ai_data"],
    "COMP0341": ["security"],
    "COMP0345": ["security"],
    "COMP0414": ["backend", "security"],
    "COMP0422": ["backend"],
    "COME0368": ["security"],
    "GLSO0227": ["backend"],
    "COMP0454": ["ai_data", "graduate_study"],
    "COMP0460": ["graduate_study"],
    "COMP0461": ["graduate_study"],
    "ITEC0417": ["ai_data", "graduate_study"],
    "ITEC0419": ["ai_data"],
}

ABEEK_PREREQUISITE_SOURCE = Source(
    title="컴퓨터학부 필수 선후수이수체계 안내",
    url="https://cse.knu.ac.kr/bbs/board.php?bo_table=sub6_1_a&wr_id=28490",
)
# Each inner list is a set of alternatives. All groups have to be satisfied.
ABEEK_PREREQUISITES = {
    "COME0331": [["COMP0204", "COMP0206"]],
    "COMP0216": [["COMP0204", "COMP0206"]],
    "ITEC0423": [["COME0331"]],
    "COMP0319": [["COME0331"]],
    "COMP0320": [["COMP0319"]],
    "COMP0217": [["COMP0205"]],
    "ELEC0462": [["COMP0205"]],
    "ITEC0401": [["COMP0205"], ["COMP0217"]],
    "ITEC0402": [["ITEC0401"]],
}

SIMCOM_RECOMMENDATION_SOURCE = Source(
    title="심화컴퓨팅전공 권장 이수 체계",
    url="https://cse.knu.ac.kr/sub3_2_a.php",
)
SIMCOM_RECOMMENDED_ORDER = {
    "COME0331": [["COMP0206"]],
    "COMP0319": [["COME0331"]],
    "COMP0224": [["COMP0336"]],
    "COMP0422": [["COMP0224"]],
    "ITEC0401": [["COMP0422"]],
    "COMP0312": [["ELEC0462"]],
    "GLSO0227": [["COMP0312"]],
    "COMP0414": [["COMP0323"]],
    "COMP0341": [["COMP0414"]],
    "COMP0315": [["COME0301"]],
    "COMP0321": [["COMP0315"]],
}
