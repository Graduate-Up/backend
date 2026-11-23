from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends

from app.schemas.evaluation import EvaluationRequest, EvaluationResponse
from app.services.graduation_engine.evaluator import evaluate_graduation
from app.services.graduation_engine.rule_selector import RuleSource, validate_major

from .requirements import get_rule_provider

router = APIRouter(tags=["evaluations"])


@router.post("/evaluations/preview")
def preview(
    request: EvaluationRequest, provide_rules: Annotated[Callable[[], RuleSource], Depends(get_rule_provider)],
) -> EvaluationResponse:
    validate_major(request.student_profile)
    return evaluate_graduation(
        request.student_profile, request.completed_courses, request.nonacademic_records, provide_rules(),
        request.liberal_summary,
    )
