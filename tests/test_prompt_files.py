from pathlib import Path

import pytest

import app.agents.chart.prompts
import app.agents.docs.prompts
import app.agents.research.prompts
import app.agents.sql.prompts
import app.core.prompts
import app.orchestration.prompts
from app.core.prompts import (MEMORIES_SECTION, MEMORY_RULE, NOTES_SECTION, RECENT_CHATS_SECTION, load_prompt,
                              load_sections)


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


ORCHESTRATION = Path(app.orchestration.prompts.__file__).parent
LAYERS = Path(app.core.prompts.__file__).parent / "layers"
LAYER_FILES = [LAYERS / "SOUL.md", LAYERS / "AGENTS.md", LAYERS / "USER.md", LAYERS / "MEMORY.md",
               ORCHESTRATION / "AGENTS.md", ORCHESTRATION / "BOOTSTRAP.md"]
LAYER_FILES += [Path(module.__file__).parent / "AGENTS.md" for module in (
    app.agents.docs.prompts, app.agents.sql.prompts, app.agents.chart.prompts, app.agents.research.prompts)]


def test_layer_paths_do_not_depend_on_the_working_directory():
    assert all(path.is_absolute() for path in LAYER_FILES)


def test_every_layer_file_exists_without_blank_edges():
    for path in LAYER_FILES:
        text = path.read_text(encoding="utf-8")
        assert not text.startswith(chr(10)) and text.endswith(chr(10)) and not text.endswith(chr(10) * 2), path


def test_memory_layer_has_its_own_file():
    sections = load_sections(LAYERS / "MEMORY.md", ("notes", "memories", "recent_chats"))
    assert set(sections) == {"notes", "memories", "recent_chats"}
    load_sections(LAYERS / "USER.md", ("user", "profile"))  # raises if memories/recent_chats are still there


def test_moved_sections_keep_their_bytes():
    assert MEMORIES_SECTION == '\n\nWHAT YOU KNOW ABOUT THIS USER (from earlier chats and their files, newest first; may be empty):\n{memories}'
    assert RECENT_CHATS_SECTION == "\n\nRECENT ACTIVITY IN THE USER'S OTHER CHATS (most recently used chat first):\n{recent_chats}"


def test_notes_section_has_one_slot():
    assert NOTES_SECTION.startswith("\n\n")
    assert NOTES_SECTION.format(notes="- x").count("- x") == 1


def test_memory_rule_tells_agents_to_follow_their_notes():
    assert "NOTES" in MEMORY_RULE
