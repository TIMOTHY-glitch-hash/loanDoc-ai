"""Shared test fixtures.

The LLM is always faked: these tests assert the *pipeline* (splitting, fan-out,
merging, redaction, error mapping), which must be verifiable without a provider
key or network access.
"""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from app.agents.schemas import PageExtraction


@pytest.fixture
def make_pdf(tmp_path: Path) -> Callable[[list[str], str], Path]:
    """Build a real multi-page PDF whose pages contain the given text."""

    def _make(pages: list[str], name: str = "document.pdf") -> Path:
        path = tmp_path / name
        pdf = canvas.Canvas(str(path), pagesize=LETTER)
        for page_text in pages:
            text_object = pdf.beginText(72, 720)
            for line in page_text.splitlines():
                text_object.textLine(line)
            pdf.drawText(text_object)
            pdf.showPage()
        pdf.save()
        return path

    return _make


class FakeLlm:
    """Stands in for the structured-output runnable.

    Records the pages it was asked about so tests can assert that fan-out
    happened, and returns a scripted result per call.
    """

    def __init__(self, results: list[PageExtraction | Exception]) -> None:
        self._results = list(results)
        self.calls: list[str] = []

    async def ainvoke(self, input: list[SystemMessage | HumanMessage], /) -> PageExtraction:
        self.calls.append(str(input[-1].content))
        if not self._results:
            raise AssertionError("FakeLlm called more times than it has scripted results")
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """`get_settings` is lru_cached; drop it so env overrides take effect."""
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
