"""PII detection and redaction.

Runs on the text we extracted from the PDF *before* any of it is returned to a
caller or written to a log. The patterns are deliberately conservative: in a bank
back office a false positive costs an operator two seconds, while a false negative
means a Social Security Number ends up in a log aggregator.
"""

from __future__ import annotations

import logging
import re
from typing import Final

from app.agents.schemas import PiiFinding, PiiKind

logger = logging.getLogger(__name__)

#: US SSN: 123-45-6789 or 123 45 6789. Bare 9-digit runs are excluded on purpose -
#: they collide with account and routing numbers and produce noise.
_SSN_RE: Final = re.compile(r"\b(?!000|666|9\d\d)\d{3}[-\s](?!00)\d{2}[-\s](?!0000)\d{4}\b")

#: Account numbers are usually labelled; keying off the label avoids flagging
#: every long number (invoice ids, phone numbers, totals).
_ACCOUNT_RE: Final = re.compile(
    r"\b(?:account|acct|a/c)\s*(?:number|no\.?|#)?\s*[:#]?\s*(\d[\d\s-]{5,17}\d)\b",
    re.IGNORECASE,
)

#: DOB requires an explicit label for the same reason: pay period and statement
#: dates are everywhere in these documents and are not PII.
_DOB_RE: Final = re.compile(
    r"\b(?:date\s+of\s+birth|d\.?o\.?b\.?|birth\s*date)\s*[:#]?\s*"
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)

_PATTERNS: Final[tuple[tuple[PiiKind, re.Pattern[str]], ...]] = (
    (PiiKind.SSN, _SSN_RE),
    (PiiKind.ACCOUNT_NUMBER, _ACCOUNT_RE),
    (PiiKind.DATE_OF_BIRTH, _DOB_RE),
)

_REDACTION: Final = "[REDACTED]"


def _mask(value: str) -> str:
    """Keep the last four characters, mask the rest - the bank-statement idiom."""
    digits = re.sub(r"\D", "", value)
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"{'*' * (len(digits) - 4)}{digits[-4:]}"


def scan_page(page_number: int, text: str) -> list[PiiFinding]:
    """Return findings for one page. Never returns the raw matched value."""
    findings: list[PiiFinding] = []

    for kind, pattern in _PATTERNS:
        matches = pattern.findall(text)
        if not matches:
            continue
        # findall returns the group when a pattern has one, else the whole match.
        first = matches[0] if isinstance(matches[0], str) else matches[0][0]
        findings.append(
            PiiFinding(
                kind=kind,
                page=page_number,
                occurrences=len(matches),
                masked_sample=_mask(first),
            )
        )

    return findings


def redact(text: str) -> str:
    """Strip anything PII-shaped from a snippet before it is returned or logged."""
    redacted = text
    for _, pattern in _PATTERNS:
        redacted = pattern.sub(_REDACTION, redacted)
    return redacted


def build_privacy_notice(findings: list[PiiFinding]) -> str | None:
    """Operator-facing notice. ``None`` when there is nothing to disclose."""
    if not findings:
        return None

    kinds = sorted({finding.kind.value for finding in findings})
    pages = sorted({finding.page for finding in findings})
    return (
        f"Sensitive data detected ({', '.join(kinds)}) on page(s) "
        f"{', '.join(str(page) for page in pages)}. Values were redacted from all "
        "returned evidence and were not logged. Handle this document under the "
        "institution's data-retention policy."
    )


def log_privacy_notice(file_name: str, findings: list[PiiFinding]) -> None:
    """Emit the audit line.

    Only counts and masked samples are logged - logging the notice must never
    itself become the leak it is warning about.
    """
    if not findings:
        return

    logger.warning(
        "PII detected in uploaded document",
        extra={
            "file_name": file_name,
            "pii": [
                {
                    "kind": finding.kind.value,
                    "page": finding.page,
                    "occurrences": finding.occurrences,
                    "masked_sample": finding.masked_sample,
                }
                for finding in findings
            ],
        },
    )
