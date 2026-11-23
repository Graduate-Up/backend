import pytest

from app.services.graduation_engine.rule_loader import RuleDataError, RuleUnavailableError, load_rules
from app.services.graduation_engine.rule_selector import ProfileError, select_rules


def test_2021_cannot_select_aicom(student, rules):
    with pytest.raises(ProfileError, match="Major is not available"):
        select_rules(student.model_copy(update={"admission_year": 2021}), rules)


def test_2024_can_select_aicom(student, rules):
    selected = select_rules(student, rules)
    assert selected.major == "aicom"
    assert [item.id for item in selected.course_requirements] == ["core"]
    assert selected.nonacademic_requirements[0].sources[0].title.startswith("가상 테스트 공통")


def test_track_only_applies_to_matching_admission_year(student, rules):
    track_student = student.model_copy(update={"track": "test-track"})
    assert [item.id for item in select_rules(track_student, rules).course_requirements] == ["core", "track"]
    earlier = track_student.model_copy(update={"admission_year": 2023})
    assert [item.id for item in select_rules(earlier, rules).course_requirements] == ["core"]


def test_invalid_track(student, rules):
    with pytest.raises(ProfileError, match="Track"):
        select_rules(student.model_copy(update={"track": "invalid"}), rules)


def test_missing_rules(tmp_path):
    for directory in (tmp_path, tmp_path / "missing"):
        with pytest.raises(RuleUnavailableError):
            load_rules(directory)


@pytest.mark.parametrize("content", ["{", '{"rules": [{}]}'])
def test_invalid_rules(tmp_path, content):
    (tmp_path / "bad.json").write_text(content, encoding="utf-8")
    with pytest.raises(RuleDataError, match="bad.json"):
        load_rules(tmp_path)


def test_default_loader_rejects_test_dataset():
    from pathlib import Path

    with pytest.raises(RuleDataError, match="Unexpected dataset"):
        load_rules(Path(__file__).parent / "fixtures")


def test_graduation_date_abeek_and_multi_major_conditions(student, rules):
    from datetime import date

    from app.schemas.rules import Conditions

    rule = rules[0]
    base_major = next(item for item in rule.credit_requirements if item.area == "major")
    base_major.conditions = Conditions(multi_majors=["none"])
    double_major = base_major.model_copy(update={
        "id": "test-double-major",
        "minimum": 3,
        "conditions": Conditions(multi_majors=["double_major"]),
    })
    rule.credit_requirements.append(double_major)
    rule.course_requirements[0].conditions = Conditions(abeek=True, graduation_from=date(2028, 1, 1))
    profile = student.model_copy(update={"multi_major": "double_major"})
    with pytest.raises(ProfileError, match="expected_graduation_date"):
        select_rules(profile, rules)
    profile = profile.model_copy(update={"expected_graduation_date": "2028-02"})
    with pytest.raises(ProfileError, match="abeek"):
        select_rules(profile, rules)
    result = select_rules(profile.model_copy(update={"abeek": False}), rules)
    assert [item.minimum for item in result.credit_requirements if item.area == "major"] == [3]
    assert result.course_requirements == []
