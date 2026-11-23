from itertools import product

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.student import StudentProfile
from app.services.graduation_engine.bundle import RuleBundle, loader
from app.services.graduation_engine.rule_selector import available_majors, select_rules


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
