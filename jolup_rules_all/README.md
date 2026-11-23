# 졸UP 졸업요건 규칙 파일 (전 전공 · 전 학번)

`app/rules/` 폴더를 백엔드 레포(Graduate-Up/backend)의 `app/` 아래에 그대로 넣으면 된다.

```
app/rules/
  index.json                 # 어떤 학번·전공이 어떤 파일을 쓰는지 (18개)
  base/<전공>_<학번구간>.json   # 전공 × 학번 구간 × (플솝만) ABEEK 별 졸업요건
  common/                    # 전공 공통: 졸업자격인정제, 신분(편입·외국인·장애), 현장실습 대체, 평점·조기졸업, 동일과목표, 출처
  catalog/*.csv              # 전공별 교육과정 과목표 (과목코드, 학점, 학년·학기, 교과구분, 설계학점)
  needs_confirmation.json    # 학부에 확인 못 해 추정으로 채운 값 14개와 적용한 기본값
  loader.py                  # select_rule_set() / resolve()
tests/test_rules.py          # 참조 무결성 + 분기표 시트 189개 경우 값 대조 (200개 테스트)
```

## 파일 나누는 기준

| 전공 | 파일 | 파일 안에서 갈리는 것 |
|---|---|---|
| 글솝 | 2017 / 2018-2021 / 2022 / 2023 / 2024-2025 | 트랙 ①②③ (`tracks`), 신분 |
| 플솝 ABEEK 이수 | 2017-2020 / 2021-2022 / 2023-2025 | 신분 |
| 플솝 ABEEK 미이수 | 2017 / 2018-2020 / 2021-2022 / 2023 / 2024-2025 | 다전공 형태(23~ 부전공 1개 77), 신분 |
| 인컴 | 2023 / 2024-2025 / 2026 | 다전공 형태(72·54·66), 신분 |
| 심화컴퓨팅·첨컴 | 2026 | 다전공 형태(72·54·66), 신분 |

학번이 달라도 요건이 완전히 같으면 한 파일로 묶었다. 외국인 기준처럼 묶인 구간 안에서도 학번마다 갈리는 값은
`resolve(..., admission_year=)`로 고른다.

## 사용법

```python
from app.rules.loader import select_rule_set, resolve

rs = select_rule_set("glsop", 2023)                      # 플솝은 abeek=True/False 필수
req = resolve(rs, status="new", multi_major="none", track="multi_major", admission_year=2023)
req.total_credits, req.major_credits, req.required_course_codes, req.english
```

- `status`: new / general_transfer / bachelor_transfer / foreign / disabled
- `multi_major`: none / double_or_linked_or_convergence / minor_1_only / minor_2plus / teaching_course
- `track`(글솝만): multi_major / overseas_dual_degree / bs_ms_linked
- 지원 안 하는 조합(예: 2021학번 인컴)은 `NoRuleSet` 예외

FastAPI에서는 이렇게 붙이면 된다 (예시, main.py는 건드리지 않음):

```python
@app.get("/requirements")
def requirements(major: str, year: int, status: str = "new", multi_major: str = "none",
                 track: str | None = None, abeek: bool | None = None):
    rs = select_rule_set(major, year, abeek=abeek)
    return resolve(rs, status=status, multi_major=multi_major, track=track, admission_year=year)
```

## 값 읽는 법

- 모든 값에 `source: ["S1", ...]`가 붙어 있고, 출처 제목·링크·날짜는 `common/sources.json`.
- `"inferred": true` = 학부 답변이나 공지로 확인 못 해 추정한 값. 어떤 질문인지와 적용한 기본값은 `needs_confirmation.json`.
- 판정 상태: satisfied / unsatisfied / needs_approval(현장실습 대체·교과구분 변경처럼 승인 필요) / needs_confirmation(추정 값에 걸림).

## 테스트

```bash
pip install pydantic pytest
python -m pytest -q tests
```

## 아직 비어 있는 것

- 글솝·플솝 2025 이전 입학생 교육과정표: 지금은 2025 기준표로 대신함 (학부 홈페이지 PDF 반영 필요)
- DB(`graduate-up-db`)의 `requirement_sets`는 이제 전공당 1행이 아니라 base 파일당 1행(18행). `majors.requirement_set_id` 대신 `index.json`으로 고르면 된다.
