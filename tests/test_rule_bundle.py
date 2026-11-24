from itertools import product

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.student import StudentProfile
from app.services.graduation_engine.bundle import RuleBundle
from app.services.graduation_engine.rule_selector import available_majors, select_rules

loader = pytest.importorskip("jolup_rules_all.app.rules.loader")


def test_graduation_schedule_exposes_only_known_dates_and_sources():
    with TestClient(app) as client:
        response = client.get("/api/v1/graduation/schedule")
    assert response.status_code == 200, response.text
    data = response.json()
    assert [(item["graduation"], item["date"]) for item in data["qualification_deadlines"]] == [
        ("2027-02", "2026-12-31"),
    ]
    assert data["department_deadline"]["label"] == "최종 계절학기 성적 확정일까지"
    assert data["early_graduation"]["min_semesters"] == 6
    assert data["early_graduation"]["min_gpa"] == 3.7
    assert data["early_graduation"]["not_for_transfer"] is True
    assert all(item["sources"] for item in [
        *data["qualification_deadlines"], data["department_deadline"], data["early_graduation"],
    ])
    assert data["deferral"]["max_semesters"] == 2
    assert data["deferral"]["facility_fee_percent"] == 5
    assert data["classification_change"]["major_department_codes"]["glsop"] == "1O0204"


def test_schedule_filters_out_other_cohorts_and_marks_unverified_dates():
    with TestClient(app) as client:
        events = client.get("/api/v1/graduation/schedule", params={
            "graduation": "2027-02", "major": "glsop", "admission_year": 2023,
        }).json()["events"]
    assert {(item["kind"], item["status"], item["end_date"]) for item in events} == {
        ("portfolio_submission", "confirmed", "2026-11-30"),
        ("graduation_interview", "tentative", None),
        ("classification_change", "standing_rule", None),
        ("graduation_review_result", "unverified", None),
    }


def test_catalog_distinguishes_required_and_recommended_prerequisites():
    with TestClient(app) as client:
        def catalog(major, year, code, **params):
            response = client.get("/api/v1/courses", params={
                "major": major, "admission_year": year, "q": code, **params,
            })
            assert response.status_code == 200, response.text
            return response.json()["items"][0]

        design = catalog("plsop", 2025, "ITEC0401", abeek=True)
        assert design["offered_by_computer_science"] is True
        assert design["prerequisite_kind"] == "required"
        assert design["prerequisite_groups"] == [["COMP0205"], ["COMP0217"]]
        for code in ("COMP0217", "ELEC0462"):
            assert catalog("plsop", 2025, code, abeek=True)["prerequisite_groups"] == [["COMP0205"]]
        assert catalog("plsop", 2025, "ITEC0401", abeek=False)["prerequisite_groups"] is None
        security = catalog("simcom", 2026, "COMP0341")
        assert security["career_tags"] == ["security"]
        assert security["prerequisite_kind"] == "recommended"


def test_glsop_catalog_and_evaluation_cite_the_same_cohort_document():
    params = {"major": "glsop", "admission_year": 2023}
    with TestClient(app) as client:
        search = client.get("/api/v1/courses", params={**params, "q": "ITEC0401"})
        rule = client.get("/api/v1/requirements", params={**params, "track": "multi_major"})
        evaluation = client.post("/api/v1/evaluations/preview", json={
            "student_profile": {**params, "track": "multi_major"}, "completed_courses": [],
        })
    assert all(response.status_code == 200 for response in (search, rule, evaluation))
    data = search.json()
    source = data["curriculum_source"]
    assert data["inferred"] is False
    assert data["basis"] == source["title"] == "글솝 교육과정 (2023학년도 기준)"
    assert source["url"] == "https://cse.knu.ac.kr/file/global_2023.pdf"
    assert data["items"][0]["credits"] == 4
    assert data["items"][0]["curriculum_category"] == "전공"
    assert [item for item in rule.json()["sources"] if item["title"].startswith("글솝 교육과정")] == [source]
    major = next(item for item in evaluation.json()["requirements"] if item["id"] == "major")
    assert source in major["sources"]


def test_catalog_uses_each_cohorts_course_classification_and_fallback():
    with TestClient(app) as client:
        older, newer = [
            client.get("/api/v1/courses", params={"major": "aicom", "admission_year": year, "q": "FUTR0208"}).json()
            for year in (2024, 2025)
        ]
        removed_course = client.get("/api/v1/courses", params={
            "major": "plsop", "admission_year": 2025, "q": "CLTR0264",
        }).json()
    assert older["inferred"] is True and "2023학년도" in older["basis"]
    assert older["items"][0]["curriculum_category"] == "교양"
    assert newer["inferred"] is False and "2025학년도" in newer["basis"]
    assert newer["items"][0]["curriculum_category"] == "일반선택"
    assert removed_course["total"] == 0


@pytest.mark.parametrize("name", ["SW융합설계1", "SW 융합설계 2", "종합설계프로젝트1", "종합설계프로젝트 2"])
def test_glsop_capstone_accepts_transcript_name(name):
    with TestClient(app) as client:
        response = client.post("/api/v1/evaluations/preview", json={
            "student_profile": {"major": "glsop", "admission_year": 2023, "track": "multi_major"},
            "completed_courses": [{
                "course_code": "TEST0999", "course_name": name, "semester": "2025-1",
                "credits": 3, "transcript_category": "전공", "status": "completed",
            }],
        })
    assert response.status_code == 200, response.text
    capstone = next(item for item in response.json()["requirements"] if item["id"] == "track-capstone")
    assert (capstone["status"], capstone["current"], capstone["required"]) == ("satisfied", 1, 1)

def test_every_bundled_year_major_track_status_and_multi_major_resolves():
    bundle = RuleBundle()
    entries = loader.load_index()
    assert {(entry.major, year) for entry in entries
            for year in range(entry.admission_from, entry.admission_to + 1)} == {
        (major, year) for year in range(2017, 2027) for major in available_majors(year)
    }
    statuses = loader.load_common("statuses")["statuses"]
    for entry in entries:
        raw = loader.load_rule_set(entry.id)
        for year, track, status, multi_major in product(
            range(entry.admission_from, entry.admission_to + 1), entry.tracks or [None],
            statuses, raw["credits"]["major"]["by_multi_major"],
        ):
            student = StudentProfile(
                major=entry.major, admission_year=year, abeek=entry.abeek, track=track,
                student_status=status, multi_major=multi_major,
            )
            rule = select_rules(student, bundle)
            assert rule.id == entry.id
            assert {item.area for item in rule.credit_requirements} >= {"total", "major"}
            assert {option.course_code for group in rule.course_requirements if group.id.startswith("required-")
                    for option in group.courses} == {course["code"] for course in raw["required_courses"]["all_of"]}


def test_tracks_abeek_and_student_branches_in_api():
    with TestClient(app) as client:
        catalog = client.get("/api/v1/courses", params={"major": "aicom", "admission_year": 2024, "q": "come0331"})
        assert catalog.status_code == 200
        result = catalog.json()
        assert result["source"] == "local_curriculum"
        assert result["inferred"] is True
        assert result["total"] == 1
        assert result["items"][0]["course_code"] == "COME0331"
        assert result["items"][0]["course_name"] == "자료구조"

        def query(**params):
            response = client.get("/api/v1/requirements", params=params)
            assert response.status_code == 200, response.text
            return response.json()

        info = client.get("/api/v1/requirements/tracks", params={"major": "glsop", "admission_year": 2024}).json()
        assert len(info["tracks"]) == 3
        assert "vietnam_twinning_3plus1" in info["track_variants"]["overseas_dual_degree"]
        glsop = query(major="glsop", admission_year=2024, track="overseas_dual_degree",
                      track_variant="vietnam_twinning_3plus1")
        assert next(item["minimum"] for item in glsop["nonacademic_requirements"]
                    if item["id"] == "department-english") == 700
        assert not any(item["id"] == "field-practice" for item in glsop["nonacademic_requirements"])

        plsop = query(major="plsop", admission_year=2023, abeek=False, multi_major="minor_1_only")
        assert next(item["minimum"] for item in plsop["credit_requirements"] if item["area"] == "major") == 77
        abeek = query(major="plsop", admission_year=2023, abeek=True, expected_graduation_date="2029-02")
        assert not any(item["area"] == "liberal_arts" for item in abeek["credit_requirements"])
        assert next(item["minimum"] for item in abeek["nonacademic_requirements"]
                    if item["id"] == "abeek-design_min") == 14
        late = client.get("/api/v1/requirements", params={
            "major": "plsop", "admission_year": 2023, "abeek": True, "expected_graduation_date": "2029-08",
        })
        assert late.status_code == 422

        transferred = query(major="aicom", admission_year=2024, student_status="bachelor_transfer")
        assert transferred["transfer_credit_max"] == 70
        assert not any(item["id"] == "practical-english" for item in transferred["nonacademic_requirements"])
        assert next(item["minimum"] for item in transferred["credit_requirements"] if item["area"] == "major") == 54

        chumcom = query(major="chumcom", admission_year=2026)
        assert {item["id"] for item in chumcom["nonacademic_requirements"]} >= {
            "graduate-credits", "lab-semesters", "research-events", "review-presentation",
        }
