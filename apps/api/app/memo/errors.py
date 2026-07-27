"""Memo generation errors."""


class MemoError(Exception):
    code = "memo_failed"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class MemoUnavailableError(MemoError):
    """No LLM configured (and no fallback requested), or the provider failed."""

    code = "memo_unavailable"


class MemoRejectedError(MemoError):
    """The model returned prose that failed the tone/structure contract."""

    code = "memo_rejected"


class MemoPersistenceError(MemoError):
    """The memo was generated but could not be written to Convex."""

    code = "memo_persistence_failed"
