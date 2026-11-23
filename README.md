# 졸UP 백엔드

경북대학교 컴퓨터학부 학생이 등록한 전체 이수 내역을 Python 규칙 엔진으로 평가하는 FastAPI 서비스입니다. 필수과목은 과목코드로 자동 판정합니다.

## 폴더 구조

| 경로 | 역할 |
| --- | --- |
| `app/main.py`, `app/api/` | FastAPI 앱, 라우터, API 오류 응답 |
| `app/schemas/` | 학생, 과목, 비교과, 규칙 및 응답 모델 |
| `app/services/graduation_engine/` | 규칙 로딩·선택, 학점 및 필수과목 계산, 전체 평가 |
| `app/core/config.py`, `data/graduation_rules/` | 공식 규칙 경로와 데이터 위치 |
| `tests/`, `tests/fixtures/` | pytest, 테스트 전용 앱, 가상 JSON 규칙 |

## Windows PowerShell에서 실행

Python 3.12를 설치하고 저장소 루트에서 실행합니다. 기존 가상환경이 정상이라면 첫 줄은 생략합니다. 기존 가상환경이 삭제된 Python을 참조하면 다른 폴더명으로 새 가상환경을 만든 후 아래 경로를 그 이름으로 바꿉니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pytest -q
python -m uvicorn app.main:app --reload
```

Swagger UI는 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)입니다. PowerShell 실행 정책으로 활성화가 차단되면 `python` 대신 `.\.venv\Scripts\python.exe`를 직접 사용합니다.

## 로그인과 학생 설정

[내 학생 설정](http://127.0.0.1:8002/account)에서 계정을 만든 뒤 로그인합니다. 학번, 입학년도, 전공과 해당 졸업 트랙·ABEEK·다전공·학생 구분을 입력하고 **설정 확인 및 저장**을 누릅니다. 로그아웃하고 같은 계정으로 다시 로그인하면 저장한 설정이 채워집니다. **내 졸업요건 조회**를 누르면 저장된 설정으로 요건을 바로 확인할 수 있습니다.

계정은 졸UP 자체 아이디·비밀번호를 사용합니다. 학번은 학생 입력값이며 대학 계정이나 학적으로 검증하지 않습니다. 설정과 계정은 저장소의 `data/local/accounts.sqlite3`에 저장됩니다. 파일을 유지하면 서버를 재시작해도 설정을 다시 불러올 수 있습니다. 기본 개발 환경에는 Docker나 PostgreSQL 설정이 필요하지 않습니다. 로컬 DB 디렉터리는 Git에서 제외됩니다.

다른 DB 파일 위치나 프런트엔드 출처가 필요한 경우 Uvicorn을 시작하기 전에 PowerShell에서 설정합니다.

```powershell
$env:JOLUP_DB_PATH = 'data/local/accounts.sqlite3'
$env:JOLUP_FRONTEND_ORIGINS = 'http://localhost:3000'
```

### 프런트엔드 연동

전체 API 스키마는 [openapi.json](openapi.json)입니다. 실행 중인 서버는 [OpenAPI JSON](http://127.0.0.1:8002/openapi.json)과 [Swagger](http://127.0.0.1:8002/docs)도 제공합니다.

| 메서드와 경로 | 입력과 응답 |
| --- | --- |
| `POST /api/v1/auth/register` | JSON `username`, `password`. 201과 계정 ID·사용자 이름 반환. 중복 사용자 이름은 409 |
| `POST /api/v1/auth/login` | 같은 자격 증명. 200과 `access_token`, `token_type`, `expires_in` 반환. 잘못된 자격 증명은 401 |
| `GET /api/v1/me` | 계정과 저장된 `profile` 반환. 첫 로그인에서 `profile`은 `null` |
| `PUT /api/v1/me/profile` | 전체 학생 설정을 검증한 뒤 저장하거나 교체하고 계정과 저장된 설정 반환 |
| `GET /api/v1/me/requirements` | 저장한 설정에 따른 졸업요건 반환. 설정이 없으면 409 |
| `POST /api/v1/auth/logout` | 현재 세션을 폐기하고 204 반환 |

로그아웃과 `/me` 경로에 `Authorization: Bearer <access_token>` 헤더를 붙입니다. 로그인 직후 `GET /api/v1/me`를 호출해 설정 화면을 채웁니다. 세션은 발급 후 8시간 동안 유효합니다. 비밀번호는 솔트를 넣어 해시하고, DB에는 세션 토큰의 해시만 보관합니다.

아래는 `PUT /api/v1/me/profile`의 JSON 예시입니다. 졸업 경로는 전공별 `track`, `track_variant`, `abeek`, `multi_major` 필드를 사용합니다. 글솝 트랙과 플솝 ABEEK 선택지는 `GET /api/v1/requirements/tracks`를 참고합니다.

```json
{
  "student_number": "2024000123",
  "admission_year": 2024,
  "major": "aicom",
  "track": null,
  "multi_major": "none",
  "expected_graduation_date": "2028-02"
}
```

팀에서 규칙 묶음을 별도로 공유하는 경우 저장소 루트에 `jolup_rules_all/`을 추가합니다. 폴더 없이도 서버와 계정 API가 시작되며, 저장할 프로필에 적용되는 규칙이 없으면 503을 반환합니다.

### 대시보드와 이수 내역 연동

[내 학생 설정](http://127.0.0.1:8002/account)에 로그인한 뒤 **이수 내역**을 누르면 과목을 직접 추가·수정·삭제할 수 있습니다.

**졸UP으로 이수 과목 가져오기** 링크를 즐겨찾기 바에 한 번 추가합니다. 학교 **이수성적조회 → 전체성적**을 연 뒤 즐겨찾기를 실행하면 표의 스크롤 영역을 읽습니다. 졸UP 웹 화면에서 로그인한 브라우저는 같은 주소의 새 창에서도 세션을 사용합니다. 전체 건수와 이수 정보를 확인하면 기존에 없는 과목코드만 저장합니다. 읽은 건수가 전체와 다르면 자동 저장하지 않고 차이를 표시합니다.

**통합정보시스템 이수 내역 붙여넣기**는 전체성적 표를 열 머리글과 함께 복사한 탭 구분 텍스트 또는 과목 `data` 배열 JSON을 미리보기합니다. `POST /api/v1/me/transcript/parse`에 `{"text": "..."}`를 보내며, 원문은 저장하지 않습니다. 평점평균 계산을 위해 과목별 성적등급(`grade`: A+~F, P, NP)은 과목과 함께 저장합니다. 등급 F 과목은 재수강으로 통과한 기록이 없으면 `status: "failed"`로 저장합니다. 이미 저장된 과목은 다시 가져올 때 비어 있는 성적등급만 채웁니다(`grades_filled`). 선별된 과목은 별도 저장 버튼으로 저장합니다. 등급이나 학기 등 필요한 값이 확인되지 않은 행은 직접 검토합니다. 같은 과목코드의 이수 확인 행이 여러 개면 판별 가능한 가장 최근 학기를 우선합니다. 성적표 교과구분과 졸업요건상 인정 영역은 별개입니다.

| 메서드와 경로 | 입력과 응답 |
| --- | --- |
| `GET /api/v1/courses?admission_year=2024&major=aicom&q=자료구조` | 코드 또는 과목명 검색. 로컬 교육과정의 학점·교과구분과 자료 기준·추정 여부를 반환. `limit`과 `offset`으로 페이지 이동 |
| `GET /api/v1/me/records` | 저장된 과목 목록과 비교과 기록 반환 |
| `PUT /api/v1/me/records` | `completed_courses`, `nonacademic_records` 전체 목록을 교체 |
| `GET /api/v1/me/evaluation` | 저장된 학생 설정과 이수 내역으로 진행률, 상태별 요건 수, 각 요건의 판정 근거와 학점 인정 내역을 계산. 학생 설정이 없으면 409 |

검색 결과의 학년·학기는 교육과정상 권장값입니다. 실제 수강 학기, 이수 여부, 성적표 교과구분을 입력해 저장합니다. 목록에 없는 과목도 동일한 입력 형식으로 직접 등록할 수 있습니다. 학교 포털의 개인 성적 자동 가져오기와 `sy.knu.ac.kr` 실시간 강좌 검색은 연동되어 있지 않습니다.

### 대시보드 진행률

| 메서드와 경로 | 입력과 응답 |
| --- | --- |
| `GET /api/v1/me/progress` | 대시보드용 진행률. `items`, `progress`, `course_progress`, `notices`, `credit_entries` 반환. 학생 설정이 없으면 409 |
| `PUT /api/v1/me/checklist` | `{"requirement_id": "department-english", "checked": true}`로 비교과 항목 하나의 체크 상태를 저장하고 갱신된 진행률 반환. 현재 요건에 없는 ID는 422 |

서버는 이수 과목으로 판정되는 요건만 계산합니다. 총·전공·교양 학점, 필수과목, 평점평균이 여기에 해당하며 `kind: "course"`로 내려갑니다. 평점평균은 과목의 `grade`로 계산하고 기준은 공통 규정(`min_gpa`, 4.3 만점 1.7 이상, F 포함)을 따릅니다. P/NP 과목은 제외하고, 재이수 과목(동일과목 포함)은 마지막 성적만 반영합니다. 성적등급이 있는 과목이 없으면 `verification_required`입니다. 평점평균은 기준 이상인지만 보므로 진행률에서 충족이면 1, 미달이면 0으로 반영합니다(학점·필수과목처럼 부분 반영하지 않음). TOEIC, 상담, 현장실습, 졸업심사 같은 비교과 요건은 `kind: "checklist"`이고, 학생이 화면에서 체크합니다. 체크 상태는 `nonacademic_records`에 저장되어 다시 로그인해도 유지됩니다.

진행률은 항목별 동일 가중치 평균입니다. 공식은 `progress_policy: "equal_item_weight_v2"`입니다. `course` 항목은 `fraction`(충족이면 1, 아니면 `current / required`, 최대 1)을 사용하고, `checklist` 항목은 체크하면 1, 아니면 0입니다. 모든 항목이 충족되어야 100%이며, 그 전에는 최대 99%입니다. 프런트는 체크박스를 누르면 [app/dashboard-view.js](app/dashboard-view.js)의 `computeProgress(items)`로 즉시 다시 계산하고, 서버의 `overall_percent`와 같은 결과를 냅니다. `course_progress`는 이수 과목 항목만의 진행률입니다. 규칙 해석 확인이 필요한 항목은 `notices`로만 내려가며 진행률에 포함하지 않습니다.

#### 과목 영역으로 자동 판정하는 요건

아래 요건은 체크박스가 아니라 이수 과목의 과목코드를 영역 매핑표와 대조해 계산합니다. 매핑표는 [data/course_classification/](data/course_classification/)에 있고 행마다 출처를 기록합니다.

| 요건 | 매핑 근거 | 판정 |
| --- | --- | --- |
| ABEEK 기본소양·전공기반·공학전공 (플솝 ABEEK) | `abeek_plsop.csv`: 학부 사이트 심화컴퓨터공학 교육과정 2020~2021, 2022, 2023학년도 표 (2023 표는 2025 기준표와 동일) | 입학년도 표 기준. 코드가 바뀐 과목(예: CLTR0689 → FUTR0208)은 다른 학년도 표로 인정하고 근거에 표시. 표가 없는 2017~2019학번은 2021 표로 추정해 미달 시 확인 필요 |
| ABEEK 설계 | 규칙의 설계 과목별 설계학점 (`course_credits`) | 이수한 설계 과목의 설계학점 합계 |
| 교양 영역 (첨성인기초·첨성인핵심, 인컴 2023학번~ 등) | `liberal_areas.csv`: SDG 교양교과목 공식 목록 59과목의 영역, 글솝 2025 교육과정 영역 표기. 성적표 교과구분에 영역이 적혀 있으면 함께 사용 | 경북대 개설 과목만 인정. 매핑에 없는 교양이 있고 기준 미달이면 미충족 대신 확인 필요로 표시하고 과목을 나열 |
| SDG 교양 | SDG 교양교과목 공식 목록 (2024.5.1. 기준) | 목록 과목 학점 합계 |
| SW 졸업자격인정 | 규칙의 대체 과목 (`course_codes`), 첨성인기초(SW) | 대체 과목 또는 첨성인기초(SW) 3학점 이수 시 체크리스트에서 자동 충족(`auto_checked`) |

교양 영역 전체 목록은 학교 교양교육과정 조회 시스템에만 있어 아직 일부 과목만 매핑되어 있습니다. 과목을 추가할 때는 `liberal_areas.csv`에 출처와 함께 행을 넣습니다.

[내 학생 설정](http://127.0.0.1:8002/account)에 로그인하면 저장된 설정 기준으로 대시보드가 바로 표시됩니다. 과목을 저장하면 대시보드도 다시 계산됩니다.

`progress`는 졸UP 서비스 지표입니다. 적용된 요건마다 동일 가중치를 두고, 충족이면 100%, 그 외에는 확인된 `current / required`를 최대 100%까지 반영해 평균을 냅니다. 전 요건이 충족되어야 결과가 100%가 되며, 승인·확인 필요 항목이 남으면 최대 99%입니다. `requirement_counts`로 상태별 개수를 표시하고, `credit_entries`에서 `recognized_category == "major"`인 과목을 전공 인정 내역으로, `major_approval_required == true`인 과목을 교과구분 변경 검토 후보로 표시할 수 있습니다.

## API

| API | 역할 |
| --- | --- |
| `GET /api/v1/majors?admission_year=2024` | 2017~2026학번의 선택 가능한 전공 조회 |
| `GET /api/v1/requirements` | `admission_year`, `major` 및 선택 입력 `track`, `abeek`, `multi_major`, `expected_graduation_date`, `transfer_student`, `international_student`에 맞는 요건 조회 |
| `POST /api/v1/evaluations/preview` | 학생 프로필, 전체 과목, 비교과 기록으로 졸업 판정 미리보기 |
| `GET /`, `GET /health` | 기존 서버 메시지와 상태 확인 |

잘못된 프로필 조합은 422입니다. 공식 규칙이 없거나 적용할 입학년도·전공 데이터가 없으면 `rules_unavailable`, 파일이나 선택된 규칙 구성이 올바르지 않으면 `invalid_rule_data`와 함께 503을 반환합니다.

## 학번·전공별 졸업요건 확인

기본 서버는 `jolup_rules_all/app/rules/index.json`에서 18개 규칙 파일을 찾고, 공통 규정·교육과정표·동일과목표·확인 필요 목록을 조합합니다. 적용 범위는 글솝·플솝 2017~2025학번, 인컴 2023~2026학번, 심화컴퓨팅·첨컴 2026학번입니다. 같은 입학년도·전공에 대한 기존 단독 인컴 예시보다 이 묶음을 우선합니다.

글솝은 `track`이 필요하고, 해외복수학위 트랙에서 베트남 트위닝 3+1이면 `track_variant=vietnam_twinning_3plus1`을 선택합니다. 플솝은 `abeek=true` 또는 `false`가 필요합니다. `student_status`는 `new`, `general_transfer`, `bachelor_transfer`, `foreign`, `disabled`를 지원합니다. `multi_major`는 `none`, `double_or_linked_or_convergence`, `minor_1_only`, `minor_2plus`, `teaching_course`를 지원합니다. 조회 화면에는 해당 선택란이 표시됩니다.

```powershell
python -m uvicorn app.main:app --reload --port 8002
```

Windows에서 검증용 임베디드 Python을 쓰는 경우 아래 명령으로 실행합니다. 코드 변경을 반영하려면 서버를 재시작합니다.

```powershell
.\.venv\graduation-runtime\python.exe -m uvicorn app.main:app --port 8002
```

[규칙 조회](http://127.0.0.1:8002/api/v1/requirements?admission_year=2024&major=aicom)에서 140/72/30학점 기준과 전공필수 7개를 확인할 수 있습니다.

[Swagger](http://127.0.0.1:8002/docs)의 `POST /api/v1/evaluations/preview`에서 아래 요청을 실행합니다.

```json
{
  "student_profile": {"admission_year": 2024, "major": "aicom"},
  "completed_courses": [
    {"course_code": "COMP0333", "semester": "2024-1", "credits": 3, "transcript_category": "전공필수", "offered_by_computer_science": true, "status": "completed"},
    {"course_code": "COMP0454", "semester": "2024-2", "credits": 3, "transcript_category": "전공필수", "offered_by_computer_science": true, "status": "in_progress"}
  ]
}
```

응답은 `rule_id: "aicom_2024-2025"`를 표시합니다. 예시 입력 기준 총·전공 인정학점은 각각 3학점이고, 총학점 부족분은 137학점입니다. 필수 목록의 COMP0453은 파일에 명시된 동일과목 COMP0333으로 완료 처리되고, COMP0454는 수강 중으로 표시됩니다. 개설 학부를 알 수 없는 경우 `offered_by_computer_science`를 생략하면 해당 과목의 전공 인정에 확인이 필요합니다. 편입생 과목은 `prior_institution`으로 전적대 과목 여부를 전달할 수 있습니다.

정확한 학번의 카탈로그가 확인된 경우 전공 과목 인정에 활용합니다. 카탈로그가 추정 표시된 학번은 명시된 필수과목 외 전공 인정 범위를 확인 필요로 둡니다. 필수과목에는 해당 규칙 파일에 명시된 동일코드를 적용하며, 공통 동일과목표의 전후 코드가 모두 이수된 내역은 시행 시점과 재이수 성적 확인을 요청합니다.

교양 영역·SDG, 현장실습·대체, 영어 시험, SW, 상담, 평점, 졸업심사, 연구 활동, ABEEK 영역별 학점은 상세 기준을 조회할 수 있습니다. 미리보기에서는 확인된 값이나 충족 여부를 `nonacademic_records`에 요건 ID와 함께 전달합니다. 시험 성적의 유효기간, 교양 영역별 과목 분류, ABEEK 일부 학번의 교양 초과학점 제외, 증빙 및 대체 승인 여부는 추가 확인이 필요합니다.

### 졸업 예정 연·월과 트랙 선택

[졸업요건 조회 화면](http://127.0.0.1:8002/requirements)에서 입학년도와 전공을 선택하면 해당 규칙에 등록된 트랙이 표시됩니다. 트랙이 없는 2024학번 인컴은 `트랙 없음`으로 표시됩니다. API에서 트랙 목록은 `GET /api/v1/requirements/tracks`로 조회할 수 있습니다.

`expected_graduation_date`는 선택 항목이며, 전달할 때는 `2027-02` 또는 `2027-08`처럼 입력합니다. 기간 조건은 월 단위로 비교합니다.

### Swagger에서 가상 학생 평가

개발 PC에서 테스트 전용 앱을 실행합니다.

```powershell
python -m uvicorn tests.demo_app:create_demo_app --factory --reload --port 8001
```

[테스트 Swagger](http://127.0.0.1:8001/docs)에서 POST 평가 API의 **Try it out**을 누르고 다음 JSON을 입력한 뒤 **Execute**를 누릅니다. 규칙 조회는 `admission_year=2024`, `major=aicom`으로 실행합니다.

```json
{
  "student_profile": {"admission_year": 2024, "major": "aicom"},
  "completed_courses": [
    {"course_code": "TESTOLD", "semester": "2024-1", "credits": 3, "transcript_category": "major", "status": "completed"},
    {"course_code": "TEST002", "semester": "2024-2", "credits": 3, "transcript_category": "major", "status": "in_progress"},
    {"course_code": "TESTLIB", "semester": "2024-1", "credits": 3, "transcript_category": "liberal_arts", "status": "completed"}
  ],
  "nonacademic_records": [
    {"requirement_id": "test-english", "amount": 1, "status": "satisfied"}
  ]
}
```

응답의 `dataset`은 `test`입니다. 총 인정학점은 6, 총 부족학점은 3이며 필수과목 중 TEST002는 `in_progress`입니다.

## 공식 규칙 추가

1. 공식 문서를 확인하고 `data/graduation_rules/`에 UTF-8 JSON 파일을 추가합니다. 최상위 형식은 `{"rules": [...]}`입니다. 파일명 예시는 `aicom_2023-2026.json`, `knu_graduation_qualification.json`입니다.
2. 각 규칙에 고유한 `id`, `dataset: "official"`, `major`, `admission_from`, `admission_to`, `sources`를 입력합니다. 문서명, 링크, 조항·쪽수를 기록할 수 있습니다. `allowed_tracks`의 `null`은 트랙이 없는 프로필을 뜻합니다. 서로 다른 조건의 전공 규칙은 적용 범위가 겹치지 않도록 작성합니다.
3. `credit_requirements`, `course_requirements`, `nonacademic_requirements` 배열을 채웁니다. 조건 필터링 후 총학점, 전공학점, 교양학점 기준이 각각 하나씩 필요합니다. 공통 요건은 `major: "common"`으로 별도 정의합니다. 중복 요건 ID는 허용되지 않습니다.
4. 필수과목 그룹은 `courses`와 `choose`로 작성합니다. 개별 필수과목에만 적용되는 대체과목은 `substitutes`, 재수강 학점과 필수과목 모두에서 같은 과목으로 보는 코드는 `equivalent_courses`를 사용합니다.
5. `course_treatments`와 `transcript_categories`에는 공식 자료로 확인한 학점 인정 분류·상태를 입력합니다. 개별 과목 정책, 동일과목 대표 코드 정책, 성적표 구분 정책 순으로 적용합니다. `credit_caps`에는 적용 영역 또는 과목코드와 상한을 설정합니다. 규칙 필드 전체는 `app/schemas/rules.py`, 파일 형식 예시는 가상 데이터인 `tests/fixtures/demo_rules.json`에서 확인할 수 있습니다.

## 현재 한계와 다음 단계

묶음의 학번 범위 밖에는 추가 규칙 파일이 필요합니다. 자료의 추정값, 알 수 없는 교과구분은 확인 필요로, 규칙에 명시된 교과구분 변경은 승인 필요로 보고합니다. 어느 상태든 전체 판정을 충족으로 확정하지 않습니다.

재수강 완료 건의 학점·인정 기준이 서로 다르면 확인이 필요합니다. 같은 기준의 완료 건은 한 번만 반영합니다. 상한은 학점, 학기 문자열, 과목코드, 성적표 교과구분 내림차순으로 배분합니다. 공식 규칙을 추가할 때 상한 배분 순서와 재수강 정책을 검증해야 합니다. 비교과 수치는 제출된 요건별 누계 중 충족 기록의 최댓값으로 평가합니다. 비교과 증빙과 성적표의 진위는 별도로 확인해야 합니다.

팀 배포에서 PostgreSQL을 사용하려면 DB 연결과 마이그레이션을 추가합니다. 공식 문서와 과목 카탈로그의 추가 검증이 필요합니다.
