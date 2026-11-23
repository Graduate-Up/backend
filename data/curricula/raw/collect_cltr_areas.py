"""경북대 수업시간표·강의계획서 조회(공개 페이지)에서 교양 영역별 과목코드를 모아 JSON으로 저장한다.

대상 페이지: https://knuin.knu.ac.kr/public/stddm/lectPlnInqr.knu
- 로그인 없이 조회되는 공개 API만 사용한다.
- 교양영역 코드 트리(첨성인기초/핵심/일반/소양 → 세부 영역)를 먼저 받은 뒤,
  연도 × 학기 × 세부 영역마다 개설 강좌를 조회해 과목코드를 모은다.
- 학교 서버에 부담을 주지 않도록 요청 사이에 쉬고, 필요한 범위만 돈다.

실행:
    pip install requests
    python scripts/collect_cltr_areas.py --from-year 2019 --to-year 2026
결과: data/cltr_areas.json
"""

import argparse
import json
import time
from pathlib import Path

import requests

BASE = "https://knuin.knu.ac.kr"
CODE_URL = f"{BASE}/public/cmmnn/cmmbs/code/selectListCode"
LECT_URL = f"{BASE}/public/web/stddm/lsspr/syllabus/lectPlnInqr/selectListLectPlnInqr"
HEADERS = {"Content-Type": "application/json; charset=UTF-8", "Accept": "application/json"}

SEMESTERS = {"CMBS001400001": "1학기", "CMBS001400002": "2학기"}
GENERAL_AREA = ("STCU001000007", ["첨성인일반"])  # 하위 영역이 없어 상위 코드로 조회

session = requests.Session()
session.headers.update(HEADERS)


def list_codes(key, upper=None, attrs=None):
    body = {"search": {"reqId": "collector", "key": key, "upperKey": upper, "listCode": False,
                       "attrs": attrs or [], "useYn": None, "searchParam": None}}
    r = session.post(CODE_URL, json=body, timeout=30)
    r.raise_for_status()
    return r.json()["data"]["option"]["codes"] or []


def area_leaves():
    """조회에 쓸 가장 아래 단계 교양영역 코드와 경로를 돌려준다."""
    leaves = []
    for l2 in list_codes("STCU0010", attrs=[{"key": "sbjetRelmCdTmtbl", "value": "Y"}]):
        for l3 in list_codes("STCU0011", l2["codeId"]):
            l4s = list_codes("STCU0012", l3["codeId"])
            if l4s:
                for l4 in l4s:
                    leaves.append({"id": l4["codeId"], "path": [l2["codeNm"], l3["codeNm"], l4["codeNm"]],
                                   "fromYear": l4.get("sbjetRelmStrtYear"), "toYear": l4.get("sbjetRelmEndYear")})
            else:
                leaves.append({"id": l3["codeId"], "path": [l2["codeNm"], l3["codeNm"]],
                               "fromYear": l3.get("sbjetRelmStrtYear"), "toYear": l3.get("sbjetRelmEndYear")})
    leaves.append({"id": GENERAL_AREA[0], "path": GENERAL_AREA[1], "fromYear": None, "toYear": None})
    return leaves


def search_lectures(year, sem_code, relm_code):
    search = {k: "" for k in (
        "sbjetCd sbjetNm crgePrfssNm sbjetSctcd estblDprtnCd rmtCrseYn rprsnLctreLnggeSctcd flplnCrseYn "
        "pstinNtnnvRmtCrseYn dgGbDstrcRmtCrseYn sugrdEvltnYn prctsExrmnYn bldngSn bldngCd bldngNm "
        "lssnsLcttmUntcd sbjetSctcd2 contents knuFtrDesigYn cltreHmntsCltreYn sdgCltreYn rltmCrseYn "
        "riseRmtCrseYn coRcgnnSbjetYn").split()}
    search.update({"estblYear": str(year), "estblSmstrSctcd": sem_code, "sbjetRelmCd": relm_code,
                   "gubun": "01", "isApi": "Y", "lctreLnggeSctcd": "ko"})
    r = session.post(LECT_URL, json={"search": search}, timeout=60)
    r.raise_for_status()
    return r.json().get("data") or []


def in_range(leaf, year):
    start, end = leaf["fromYear"], leaf["toYear"]
    return (not start or year >= int(start)) and (not end or year <= int(end))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--from-year", type=int, default=2019)
    p.add_argument("--to-year", type=int, default=2026)
    p.add_argument("--delay", type=float, default=0.3, help="요청 사이 대기(초)")
    p.add_argument("--out", default="data/cltr_areas.json")
    args = p.parse_args()

    leaves = area_leaves()
    result = {}
    for year in range(args.from_year, args.to_year + 1):
        for sem_code, sem_name in SEMESTERS.items():
            for leaf in leaves:
                if not in_range(leaf, year):
                    continue
                for row in search_lectures(year, sem_code, leaf["id"]):
                    key = f'{row["sbjetCd"]}|{">".join(leaf["path"])}'
                    item = result.setdefault(key, {
                        "code": row["sbjetCd"], "name": row["sbjetNm"], "credit": row.get("crdit"),
                        "area": leaf["path"], "areaCode": leaf["id"], "offered": []})
                    tag = f"{year}-{sem_name}"
                    if tag not in item["offered"]:
                        item["offered"].append(tag)
                    item["name"] = row["sbjetNm"]  # 최신 이름으로
                time.sleep(args.delay)
            print(f"{year} {sem_name} done, {len(result)} entries")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"source": "knuin 수업시간표·강의계획서 조회 (공개)", "areas": leaves,
               "courses": sorted(result.values(), key=lambda x: (x["area"], x["code"]))}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {len(result)} entries → {out}")


if __name__ == "__main__":
    main()
