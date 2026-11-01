import pytest

from app.services.ingestion import PermanentIngestError, extract_pages, validate_pdf
from tests.helpers import make_pdf


def test_pages_keep_their_numbers():
    pages = extract_pages(make_pdf(["alpha text", "beta text", "gamma text"]))
    assert [p for p, _ in pages] == [1, 2, 3]
    assert "beta" in pages[1][1]


def test_validate_pdf_returns_page_count_and_enforces_limit():
    data = make_pdf(["a", "b", "c"])
    assert validate_pdf(data, max_pages=10) == 3
    with pytest.raises(PermanentIngestError, match="limit"):
        validate_pdf(data, max_pages=2)


def test_corrupt_pdf_is_a_permanent_error():
    with pytest.raises(PermanentIngestError):
        validate_pdf(b"%PDF-1.7 this is not really a pdf", max_pages=10)
