import pytest

from app.services.graduation_engine.evaluator import evaluate_graduation


@pytest.mark.parametrize(("second_status", "result", "detail"), [
    ("completed", "satisfied", "completed"),
    ("failed", "unsatisfied", "missing"),
    ("in_progress", "unsatisfied", "in_progress"),
])
def test_required_course_status(student, rules, course, second_status, result, detail):
    evaluation = evaluate_graduation(
        student, [course("testold"), course("TESTALT", status=second_status)], [], rules,
    )
    core = next(item for item in evaluation.requirements if item.id == "core")
    assert core.status == result
    assert [item.status for item in core.required_courses] == ["completed", detail]


def test_shared_substitute_only_counts_once(student, rules, course):
    core = rules[0].course_requirements[0]
    core.courses[0].substitutes.append("TESTALT")
    evaluation = evaluate_graduation(student, [course("TESTALT")], [], rules)
    result = next(item for item in evaluation.requirements if item.id == "core")
    assert result.current == 1
    assert result.shortfall == 1
