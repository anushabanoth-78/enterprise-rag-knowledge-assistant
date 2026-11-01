import pytest

from app.storage import LocalStorage


def test_roundtrip_and_delete(tmp_path):
    s = LocalStorage(str(tmp_path))
    s.put("u1/a.pdf", b"%PDF-1")
    assert s.get("u1/a.pdf") == b"%PDF-1"
    s.delete("u1/a.pdf")
    s.delete("u1/a.pdf")  # deleting twice is fine
    with pytest.raises(FileNotFoundError):
        s.get("u1/a.pdf")


@pytest.mark.parametrize("key", ["../evil", "u1/../../evil", "/etc/passwd"])
def test_path_traversal_is_blocked(tmp_path, key):
    with pytest.raises(ValueError):
        LocalStorage(str(tmp_path)).put(key, b"x")
