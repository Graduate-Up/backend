"""규칙 파일 검증: 참조 무결성 + 분기표 시트 189개 경우와 값 일치."""
import csv
import json
import re
from pathlib import Path

import pytest

from app.rules.loader import NoRuleSet, RULES_DIR, load_index, load_rule_set, resolve, select_rule_set

FIXTURE = Path(__file__).parent / "fixtures" / "sheet_cases.csv"
MAJOR = {"글솝": "glsop", "플솝": "plsop", "인컴": "aicom", "심화컴퓨팅": "simcom", "첨컴": "chumcom"}
ALL_MM = ["none", "double_or_linked_or_convergence", "minor_1_only", "minor_2plus", "teaching_course"]


def _json(rel):
    return json.loads((RULES_DIR / rel).read_text(encoding="utf-8"))


def _codes(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "source" and isinstance(v, list):
                yield from v
            else:
                yield from _codes(v)
    elif isinstance(o, list):
        for v in o:
            yield from _codes(v)


# ------------------------------------------------------------ 참조 무결성
def test_sources_and_confirmations_exist():
    src = {s["id"] for s in _json("common/sources.json")["sources"]}
    nc = {n["id"] for n in _json("needs_confirmation.json")["items"]}
    for e in load_index():
        rs = load_rule_set(e.id)
        assert set(rs["sources"]) <= src, e.id
        assert set(_codes(rs)) <= src, e.id
        assert set(rs["needs_confirmation"]) <= nc, e.id


def test_required_courses_in_catalog():
    catalogs = _json("index.json")["catalogs"]
    for e in load_index():
        rs = load_rule_set(e.id)
        with open(RULES_DIR / catalogs[e.major], encoding="utf-8") as f:
            codes = {r["code"] for r in csv.DictReader(f)}
        missing = [c["code"] for c in rs["required_courses"]["all_of"] if c["code"] not in codes]
        assert not missing, (e.id, missing)


@pytest.mark.parametrize("major,years,abeek", [
    ("glsop", range(2017, 2026), [None]), ("plsop", range(2017, 2026), [True, False]),
    ("aicom", range(2023, 2027), [None]), ("simcom", [2026], [None]), ("chumcom", [2026], [None])])
def test_each_supported_combo_selects_exactly_one(major, years, abeek):
    for y in years:
        for a in abeek:
            assert select_rule_set(major, y, abeek=a)["admission_years"][0] <= y


@pytest.mark.parametrize("major,year", [("aicom", 2021), ("simcom", 2025), ("glsop", 2026), ("plsop", 2016)])
def test_unsupported_combo_raises(major, year):
    with pytest.raises(NoRuleSet):
        select_rule_set(major, year, abeek=False if major == "plsop" else None)


# ------------------------------------------------------------ 시트 189개 경우
def _years(s):
    a, *b = [int(x) for x in s.split("~")]
    return range(a, (b[0] if b else a) + 1)


def _statuses(label):
    if label.startswith("외국인·장애"):
        return ["foreign", "disabled"]
    if label.startswith("외국인"):
        return ["foreign"]
    return [{"신입학": "new", "일반편입": "general_transfer", "학사편입": "bachelor_transfer", "장애학생": "disabled"}[label]]


def _multi(major, branch):
    b = branch.split("·", 1)[-1].strip() if major == "plsop" and "미이수" in branch else branch
    if major in ("glsop",) or "ABEEK 이수" in branch or b == "전 형태":
        return ALL_MM
    table = {"단일전공": ["none"], "다전공·교직": ["double_or_linked_or_convergence", "minor_2plus", "teaching_course"],
             "부전공 1개만": ["minor_1_only"],
             "단일전공·다전공·교직": ["none", "double_or_linked_or_convergence", "minor_2plus", "teaching_course"],
             "단일전공·다전공": ["none", "double_or_linked_or_convergence", "minor_2plus", "teaching_course"]}
    if major == "plsop":
        b = b.replace("ABEEK 미이수 · ", "")
    return table[b]


def _track(branch):
    return {"①": "multi_major", "②": "overseas_dual_degree", "③": "bs_ms_linked"}.get(branch[:1])


def _first_int(s):
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


ROWS = list(csv.DictReader(FIXTURE.open(encoding="utf-8")))


@pytest.mark.parametrize("row", ROWS, ids=[r["ID"] for r in ROWS])
def test_sheet_case(row):
    major = MAJOR[row["전공"]]
    abeek = None if major != "plsop" else ("ABEEK 이수" in row["분기"])
    track = _track(row["분기"])
    for y in _years(row["학번"]):
        rs = select_rule_set(major, y, abeek=abeek)
        for st in _statuses(row["신분"]):
            for mm in _multi(major, row["분기"]):
                r = resolve(rs, status=st, multi_major=mm, track=track, admission_year=y)
                assert r.total_credits == int(row["총학점"]), (y, st, mm)
                if abeek:
                    assert r.engineering_major_credits == int(re.search(r"공학전공 (\d+)", row["전공학점"]).group(1))
                    assert r.major_credits == 65
                else:
                    assert r.major_credits == _first_int(row["전공학점"]), (y, st, mm)
                eng = row["영어"]
                if major == "aicom":
                    assert r.english.exempt == (eng == "면제"), (st, eng)
                    assert r.english.topik4_instead == eng.startswith("TOPIK")
                else:
                    assert r.english.department_min_toeic == _first_int(eng), (y, st, eng)
                    assert r.english.topik4_instead == ("TOPIK" in eng)
                fp = row["현장실습·연구"]
                if fp[:1].isdigit():
                    assert r.field_practice_credits == int(fp[0])
                elif fp == "없음":
                    assert r.field_practice_credits == 0
                if "면제" in row["졸업자격인정제"]:
                    assert "practical_english" in r.qualification_exempt or st == "foreign"
                assert r.early_graduation_allowed == ("조기졸업 불가" not in row["그 밖"])
