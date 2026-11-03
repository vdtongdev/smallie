import pytest

from app.core.prompts import load_prompt, load_sections


def test_load_prompt_drops_one_trailing_newline(tmp_path):
    for text, expected in [("a\n", "a"), ("a\n\n", "a\n"), ("a", "a")]:
        (tmp_path / "p.md").write_text(text, encoding="utf-8", newline="\n")
        assert load_prompt(tmp_path / "p.md") == expected


def test_load_prompt_reads_crlf_as_lf(tmp_path):
    (tmp_path / "p.md").write_bytes(b"a\r\nb\r\n")
    assert load_prompt(tmp_path / "p.md") == "a\nb"


def test_load_prompt_drops_bom(tmp_path):
    (tmp_path / "p.md").write_bytes("\ufeffhi\n".encode("utf-8"))
    assert load_prompt(tmp_path / "p.md") == "hi"


def test_load_sections_splits_at_markers(tmp_path):
    (tmp_path / "s.md").write_text(
        "<!-- section: one -->\n\nfirst\nline\n\n<!-- section: two -->\n\nsecond\n", encoding="utf-8")
    assert load_sections(tmp_path / "s.md", ("one", "two")) == {"one": "first\nline", "two": "second"}


def test_load_sections_rejects_missing_duplicate_or_stray(tmp_path):
    cases = [
        "<!-- section: one -->\n\nfirst\n",                                         # two missing
        "<!-- section: one -->\n\na\n\n<!-- section: one -->\n\nb\n\n<!-- section: two -->\n\nc\n",  # duplicate
        "stray\n\n<!-- section: one -->\n\na\n\n<!-- section: two -->\n\nb\n",      # text before first marker
        "<!-- section: one -->\n\na\n\n<!-- section: two -->\n\nb\n\n<!-- section: three -->\n\nc\n",  # unexpected
    ]
    for text in cases:
        (tmp_path / "s.md").write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            load_sections(tmp_path / "s.md", ("one", "two"))
