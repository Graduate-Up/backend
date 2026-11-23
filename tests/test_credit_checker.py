from app.services.graduation_engine.evaluator import evaluate_graduation


def test_retake_credit_and_shortfall(student, rules, course):
    result = evaluate_graduation(student, [
        course(semester="2024-2"), course("TESTOLD"), course(credits=4, status="in_progress"),
    ], [], rules)
    totals = {item.id: item for item in result.requirements}
    assert (totals["total"].current, totals["total"].shortfall) == (3, 6)
    assert totals["major"].shortfall == 3
    assert [entry.reason for entry in result.credit_entries] == [None, "duplicate_course", "in_progress"]


def test_cap_and_pending_credit(student, rules, course):
    result = evaluate_graduation(student, [
        course("TESTL", 5, "liberal_arts"),
        course("TESTUNKNOWN", category="unknown"),
        course("TESTAP", category="liberal_arts"),
    ], [], rules)
    capped, unknown, approval = result.credit_entries
    assert (capped.recognized_credits, capped.excluded_credits, capped.reason) == (3, 2, "credit_cap")
    assert unknown.reason == "verification_required"
    assert approval.reason == "approval_required"
    assert result.status == "verification_required"
    assert not result.eligible
    assert 0 < result.progress < 100


def test_conflicting_completed_attempts_require_verification(student, rules, course):
    result = evaluate_graduation(student, [course(), course("TESTOLD", credits=4)], [], rules)
    assert result.status == "verification_required"
    assert next(item for item in result.requirements if item.id == "total").current == 0


def test_area_only_credit_and_approval(student, rules, course):
    from app.schemas.rules import CreditTreatment

    rules[0].course_treatments["TESTAREA"] = CreditTreatment(category="major", counts_toward_total=False)
    result = evaluate_graduation(student, [course("TESTAREA"), course("TESTAP")], [], rules)
    totals = {item.credit_area: item for item in result.requirements if item.credit_area}
    assert totals["total"].current == 0
    assert totals["major"].current == 3
    assert result.credit_entries[0].counts_toward_total is False
    assert result.status == "approval_required"
    assert not result.eligible
