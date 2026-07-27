"""Memo generation endpoint."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import Settings, get_settings
from app.memo.errors import (
    MemoPersistenceError,
    MemoRejectedError,
    MemoUnavailableError,
)
from app.memo.generator import MemoGenerator
from app.memo.models import GenerateMemoRequest, UnderwritingMemo
from app.memo.store import ConvexMemoStore, MemoStore, risk_flag_labels

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memo"])


# Both collaborators are injected rather than constructed inline so tests can
# substitute a faked LLM and a faked store via FastAPI's dependency overrides.
def get_memo_generator(settings: Annotated[Settings, Depends(get_settings)]) -> MemoGenerator:
    return MemoGenerator(settings)


def get_memo_store(settings: Annotated[Settings, Depends(get_settings)]) -> MemoStore:
    return ConvexMemoStore(settings)


@router.post(
    "/generate-memo",
    response_model=UnderwritingMemo,
    response_model_by_alias=True,
    summary="Generate an underwriting memo from extraction + policy evaluation",
)
async def generate_memo(
    payload: GenerateMemoRequest,
    generator: Annotated[MemoGenerator, Depends(get_memo_generator)],
    store: Annotated[MemoStore, Depends(get_memo_store)],
) -> UnderwritingMemo:
    try:
        memo = await generator.generate(payload)
    except MemoUnavailableError as exc:
        # 503, not 500: nothing about the request is wrong, the provider is absent.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.message
        ) from exc
    except MemoRejectedError as exc:
        # The model produced prose that failed the tone/consistency contract.
        # 502: an upstream returned something unusable, and storing it would be
        # worse than failing.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message) from exc

    if not payload.persist:
        return memo

    if not store.enabled:
        # Generation succeeded; only the write was skipped. Returning 200 with
        # persisted=false keeps the memo rather than discarding work over a
        # missing deployment URL.
        memo.warnings.append("CONVEX_URL is not configured; memo was not persisted.")
        return memo

    try:
        await store.save(memo, risk_flag_labels(payload.policy_evaluation))
    except MemoPersistenceError as exc:
        logger.warning("Memo persistence failed for %s: %s", payload.application_id, exc.message)
        memo.warnings.append(f"Memo was not persisted: {exc.message}")
        return memo

    memo.persisted = True
    logger.info(
        "Underwriting memo generated",
        extra={
            "application_id": payload.application_id,
            "memo_source": memo.source.value,
            "policy_version": memo.policy_version,
            "recommended_action": memo.recommended_action,
        },
    )
    return memo
