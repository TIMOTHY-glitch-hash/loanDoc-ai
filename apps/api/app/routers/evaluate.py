"""Policy evaluation endpoint.

JSON in, JSON out, no I/O in between: the request body *is* the full input to the
decision, which is what makes the response reproducible from the audit record
alone.
"""

import logging

from fastapi import APIRouter, HTTPException, status

from app.policy.engine import PolicyEngine
from app.policy.models import (
    ExtractedFinancials,
    LoanRequest,
    PolicyEvaluation,
    PolicyVersion,
)
from app.policy.registry import UnknownPolicyVersionError, get_policy, list_policies
from app.schemas import CamelModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["policy"])


class EvaluateRequest(CamelModel):
    """Everything the decision depends on.

    ``policy_version`` is optional and defaults to the active policy. Passing an
    explicit version is how a historical decision is replayed: same body, same
    version, same output - byte for byte.
    """

    extracted_data: ExtractedFinancials
    loan_request: LoanRequest
    policy_version: str | None = None


@router.get(
    "/policies",
    response_model=list[PolicyVersion],
    response_model_by_alias=True,
    summary="List published policy versions",
)
async def list_policy_versions() -> list[PolicyVersion]:
    return list_policies()


@router.post(
    "/evaluate",
    response_model=PolicyEvaluation,
    response_model_by_alias=True,
    summary="Evaluate a loan request against a versioned policy",
)
async def evaluate(payload: EvaluateRequest) -> PolicyEvaluation:
    try:
        policy = get_policy(payload.policy_version)
    except UnknownPolicyVersionError as exc:
        # 422, not 404: the resource exists, the submitted version does not, and
        # silently falling back to the active policy would corrupt the audit trail.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    result = PolicyEngine(policy=policy).evaluate(payload.extracted_data, payload.loan_request)

    logger.info(
        "Policy evaluation complete",
        extra={
            "policy_version": result.policy_version,
            "policy_checksum": result.policy_checksum,
            "input_fingerprint": result.input_fingerprint,
            "recommended_action": result.recommended_action.value,
            "overall_risk_score": result.overall_risk_score,
        },
    )
    return result
