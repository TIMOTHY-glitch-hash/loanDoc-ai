"""Convex persistence for memos.

Convex is the system of record for application state, so the memo is written
there rather than into a second store the UI would have to reconcile. The write
goes over Convex's HTTP mutation API (``POST /api/mutation``) because the backend
is Python; the mutation itself still runs inside Convex, so the patch and its
audit-log entry commit in one transaction.

Note that Convex returns HTTP 200 with ``{"status": "error"}`` for a mutation
that raised - checking the status code alone would silently drop failed writes.
"""

import logging
from typing import Protocol

import httpx

from app.config import Settings
from app.memo.errors import MemoPersistenceError
from app.memo.models import UnderwritingMemo
from app.policy.models import PolicyEvaluation, Severity

logger = logging.getLogger(__name__)

#: `<module>:<export>` form Convex uses to address a function.
SAVE_MEMO_MUTATION = "loanApplications:saveDecisionMemo"


class MemoStore(Protocol):
    """Persistence seam, so the endpoint can be tested without a deployment."""

    @property
    def enabled(self) -> bool: ...

    async def save(self, memo: UnderwritingMemo, risk_flags: list[str]) -> None: ...


def risk_flag_labels(evaluation: PolicyEvaluation) -> list[str]:
    """Stable ``riskFlags`` strings for the application row.

    ``SEVERITY:RULE_ID`` rather than the rendered message: the row stores a
    reference to the rule, and the message can then be re-rendered from the
    policy version without a data migration.
    """
    return [
        f"{flag.severity.value}:{flag.rule_id}"
        for flag in evaluation.flags
        if flag.severity is not Severity.INFO
    ]


class ConvexMemoStore:
    """Writes memos to the Convex ``loanApplications`` table."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def enabled(self) -> bool:
        return bool(self._settings.convex_url)

    async def save(self, memo: UnderwritingMemo, risk_flags: list[str]) -> None:
        if not self.enabled:
            raise MemoPersistenceError("CONVEX_URL is not configured.")

        url = f"{self._settings.convex_url.rstrip('/')}/api/mutation"
        body = {
            "path": SAVE_MEMO_MUTATION,
            "args": {
                "id": memo.application_id,
                "decisionMemo": memo.markdown,
                "policyVersion": memo.policy_version,
                "riskFlags": risk_flags,
            },
            "format": "json",
        }

        try:
            if self._client is not None:
                response = await self._client.post(url, json=body)
            else:
                async with httpx.AsyncClient(
                    timeout=self._settings.convex_timeout_seconds
                ) as client:
                    response = await client.post(url, json=body)
        except httpx.HTTPError as exc:
            raise MemoPersistenceError(f"Convex request failed: {exc}") from exc

        if response.status_code >= 400:
            raise MemoPersistenceError(
                f"Convex returned HTTP {response.status_code} for {SAVE_MEMO_MUTATION}."
            )

        payload = response.json()
        if payload.get("status") != "success":
            raise MemoPersistenceError(
                f"Convex mutation {SAVE_MEMO_MUTATION} failed: "
                f"{payload.get('errorMessage') or payload}"
            )
