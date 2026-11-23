"""전공·학번별 교육과정표를 data/curricula/curricula.csv 로 만든다.

원본: data/curricula/raw/curriculum_courses.csv (팀 정리, 학번별 적용 교육과정 판독본)
- 같은 내용의 JSON(raw/courses.json)과 행 단위로 일치하는지 확인한다.
- 학번마다 표를 하나씩 둔다. '적용교육과정'은 그 학번에 적용되는 학년도 표다
  (예: 플솝 20학번 → 2021 표 '2020~2021', 인컴 24학번 → 2023 표).
- 비고에 '원문 없음'이 있으면 그 학번 원문이 없어 다른 학년도 표로 추정한 것이다(inferred).
- 글솝 2023·2025 표는 판독본에 교양 영역·창업 표기가 없어, 학부 교육과정 PDF에 인쇄된 표기를 덧붙인다.

실행: python scripts/build_curricula.py
"""

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "curricula" / "raw"
SOURCE_CSV = RAW / "curriculum_courses.csv"
SOURCE_JSON = RAW / "courses.json"
OUT = ROOT / "data" / "curricula" / "curricula.csv"

MAJORS = {"심화컴퓨터공학(ABEEK)": "plsop", "글로벌SW융합": "glsop", "인공지능컴퓨팅": "aicom",
          "심화컴퓨팅": "simcom", "첨단컴퓨팅연구": "chumcom"}
JSON_KEYS = {"plsop": "simhwa", "glsop": "global", "aicom": "ai", "simcom": "hpc", "chumcom": "adv"}

# 학부 홈페이지 교육과정 파일 (적용 교육과정 학년도 기준)
FILES = {
    ("plsop", 2021): "https://cse.knu.ac.kr/file/simhwa_2021.pdf",
    ("plsop", 2022): "https://cse.knu.ac.kr/file/simhwa_2022.jpg",
    ("plsop", 2023): "https://cse.knu.ac.kr/file/simhwa_2023.pdf",
    ("plsop", 2024): "https://cse.knu.ac.kr/file/simhwa_2024.jpg",
    ("plsop", 2025): "https://cse.knu.ac.kr/file/simhwa_2025.pdf",
    ("glsop", 2021): "https://cse.knu.ac.kr/file/global_2021.pdf",
    ("glsop", 2022): "https://cse.knu.ac.kr/file/global_2022.pdf",
    ("glsop", 2023): "https://cse.knu.ac.kr/file/global_2023.pdf",
    ("glsop", 2024): "https://cse.knu.ac.kr/file/global_2024.jpg",
    ("glsop", 2025): "https://cse.knu.ac.kr/file/global_2025.pdf",
}

# 판독본에 표기가 없는 글솝 2023·2025 교육과정 PDF의 영역·창업 표기
GLSOP_TAGS_2023 = {
    "CLTR0205": "첨성인기초(글쓰기),인문교양", "CLTR0819": "첨성인기초(수리)",
    "CLTR0003": "첨성인핵심(예술과 표현),인문교양", "CLTR0211": "첨성인핵심(자연과학)",
    "CLTR0043": "첨성인핵심(인문사회),인문교양", "CLTR0264": "첨성인기초(SW)",
    "CLTR0089": "첨성인핵심(인문사회),창업", "FUTR0201": "창업,KNU미래설계", "FUTR0214": "창업,KNU미래설계",
    "FUTR0208": "KNU미래설계", "FUTR0226": "창업,KNU미래설계", "FUTR0231": "창업,KNU미래설계",
    "GLSO0223": "창업", "COMP0428": "창업", "ITEC0403": "창업", "GLSO0229": "창업",
}
GLSOP_TAGS_2025 = {**GLSOP_TAGS_2023, "CLTR0205": "첨성인기초(글쓰기)", "CLTR0003": "첨성인핵심(예술과 표현),SDG",
                   "CLTR0043": "첨성인핵심(인문사회),SDG"}
EXTRA_TAGS = {("glsop", 2023): GLSOP_TAGS_2023, ("glsop", 2025): GLSOP_TAGS_2025}
# 판독본에 학점이 비어 있는 과목: 같은 과목의 다른 표 학점(3-3-0)
MISSING_CREDITS = {"COMP0454": "3"}

FIELDS = ["major", "admission_year", "curriculum_year", "code", "name", "credits", "grade_year", "semester",
          "category", "required", "design", "tags", "inferred", "source"]


def build_rows():
    rows = []
    with SOURCE_CSV.open(encoding="utf-8-sig") as file:
        for item in csv.DictReader(file):
            major = MAJORS[item["전공"]]
            admission, applied = 2000 + int(item["학번"]), int(item["적용교육과정"])
            code = item["학수번호"]
            tags = item["교양태그"].replace(";", ",") or EXTRA_TAGS.get((major, applied), {}).get(code, "")
            source = item["출처"] + (f" ({item['비고']})" if item["비고"] else "")
            if (major, applied) in FILES:
                source += " " + FILES[major, applied]
            rows.append(dict(
                major=major, admission_year=admission, curriculum_year=applied, code=code, name=item["과목명"],
                credits=item["학점"] or MISSING_CREDITS[code], grade_year=item["학년"], semester=item["학기"],
                category=item["구분"], required=str(item["필수"] == "Y").lower(),
                design=str(item["설계"] == "Y").lower(), tags=tags,
                inferred=str("원문 없음" in item["비고"]).lower(), source=source,
            ))
    return rows


def check_against_json(rows):
    data = json.loads(SOURCE_JSON.read_text(encoding="utf-8"))
    from_csv = {(r["major"], r["admission_year"], r["code"], r["category"], r["required"], r["design"],
                 int(r["grade_year"]), int(r["semester"])) for r in rows}
    from_json = set()
    for key, items in data["curricula"].items():
        prefix, year = key.rsplit("_", 1)
        major = next(m for m, p in JSON_KEYS.items() if p == prefix)
        from_json |= {(major, int(year), x["code"], x["category"], str(x["required"]).lower(),
                       str(x["design"]).lower(), x["year"], x["semester"]) for x in items}
    assert from_csv == from_json, (sorted(from_csv - from_json)[:5], sorted(from_json - from_csv)[:5])


def main():
    rows = build_rows()
    check_against_json(rows)
    seen = set()
    for row in rows:
        key = (row["major"], row["admission_year"], row["code"])
        assert key not in seen, key
        seen.add(key)
        assert row["category"] and float(row["credits"]) > 0, key
    rows.sort(key=lambda r: (r["major"], r["admission_year"], int(r["grade_year"]), int(r["semester"]), r["code"]))
    with OUT.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} rows -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
