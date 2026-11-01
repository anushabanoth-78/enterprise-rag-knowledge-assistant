import pytest

from app.services.ingestion import chunk_pages


def test_chunks_keep_page_numbers_and_overlap():
    pages = [(1, " ".join(f"a{i}" for i in range(50))), (2, "short page")]
    chunks = chunk_pages(pages, size=20, overlap=5)
    assert {p for p, _ in chunks} == {1, 2}
    first, second = chunks[0][1].split(), chunks[1][1].split()
    assert first[-5:] == second[:5]


def test_empty_pages_produce_no_chunks():
    assert chunk_pages([(1, "   ")], 20, 5) == []


def test_bad_overlap_rejected():
    with pytest.raises(ValueError):
        chunk_pages([(1, "x y z")], 5, 5)
