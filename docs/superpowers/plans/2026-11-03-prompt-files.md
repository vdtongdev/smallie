# Prompt layers as Markdown files Implementation Plan

> **For agentic workers:** executed with the foreman loop (one agent per task, verifier after each). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the text of the SOUL, AGENTS, USER and BOOTSTRAP prompt layers from Python constants into `.md` files, with every constant keeping its exact value.

**Architecture:** Two loader functions in `app/core/prompts.py` read the files once at import into the constants used today, so no call site changes. Separators between layers stay in Python; files hold only layer text.

**Tech Stack:** Python 3, pathlib, pytest; uv; uvicorn with watchfiles for dev reload.

**Spec:** `docs/superpowers/specs/2026-11-03-prompt-files-design.md`

**Worktree:** `C:\Users\Asus ROG\projects\smallie\.claude\worktrees\prompt-files`, branch `0.7.0/prompt-files`. Run every command from there.

## Global Constraints

- Every model request stays byte-for-byte the same as on `main` (3f872b1).
- Constant names stay the same; `agent.py` files, `app/agents/factory.py` and `app/orchestration/orchestrator.py` are not changed.
- Paths to layer files are built from `Path(__file__).parent`, never from the working directory.
- Placeholders stay in the files and are filled by the existing `.format()` calls; literal braces in a formatted file are doubled, files never formatted keep single braces.
- Files never start with a blank line and end with exactly one newline.
- Only the layer texts move; everything listed under "What stays in Python" in the spec stays a constant.
- No prompt wording changes.

## Identity check (used by tasks 2, 3 and 5)

Baseline from `main` is saved at `C:\Users\ASUSRO~1\AppData\Local\Temp\claude\c--Users-Asus-ROG-projects-smallie\556c7f57-89fa-43f5-9f65-4ebe17fccf8b\scratchpad\layers_main.json` (13 constants, 14922 bytes). Check from the worktree root (Git Bash):

```bash
S="C:/Users/ASUSRO~1/AppData/Local/Temp/claude/c--Users-Asus-ROG-projects-smallie/556c7f57-89fa-43f5-9f65-4ebe17fccf8b/scratchpad"
PYTHONPATH=. uv run python "$S/dump_layers.py" "$S/layers_branch.json" && cmp "$S/layers_main.json" "$S/layers_branch.json" && echo IDENTICAL
```

Expected: `dumped 13 constants` then `IDENTICAL`. Any `differ` output is a failure.

## Review Focus

- A Windows checkout with `core.autocrlf` gives the `.md` files CRLF endings; the constants must still hold `\n` only (task 1 test).
- An editor that saves UTF-8 with a BOM must not put `\ufeff` into a prompt (task 1 test).
- Agent servers and scripts run from other working directories; the files must load wherever the process starts (task 2 test).
- An editor that adds a blank line at the end or start of a file would silently change the bytes (task 2/3 test over every layer file).
- Both Docker images must ship the `.md` files (task 5 check of `Dockerfile`, `Dockerfile.agent`, `.dockerignore`).

---

### Task 1: Loader functions

Agent: implementer

**Files:**
- Modify: `app/core/prompts.py` (add the two functions near the top, after the imports)
- Create: `tests/test_prompt_files.py`

**Interfaces:**
- Produces: `load_prompt(path: Path) -> str` and `load_sections(path: Path, names: tuple[str, ...]) -> dict[str, str]` in `app/core/prompts.py`.

- [ ] **Step 1: Write the failing tests** in `tests/test_prompt_files.py` (use `tmp_path`):

```python
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
```

- [ ] **Step 2: Run** `uv run pytest tests/test_prompt_files.py -v`. Expected: FAIL (ImportError on `load_prompt`).

- [ ] **Step 3: Implement both functions.** `load_prompt` reads with `encoding="utf-8-sig"` in text mode (universal newlines turn CRLF into LF) and removes one trailing `"\n"`. `load_sections` reads through `load_prompt`, splits at lines matching `^<!-- section: ([a-z_]+) -->$`, strips leading and trailing `"\n"` from each body, and raises `ValueError` naming the file when the markers found are not exactly `names` (missing, duplicated or extra) or when non-blank text precedes the first marker.

- [ ] **Step 4: Run** `uv run pytest tests/test_prompt_files.py -v`. Expected: 5 passed.

- [ ] **Step 5: Commit** `git add app/core/prompts.py tests/test_prompt_files.py` and `git commit -m "Add loaders for prompt layer files"`.

---

### Task 2: Core and orchestrator layers into files

Agent: implementer

**Files:**
- Create: `app/core/layers/SOUL.md`, `app/core/layers/AGENTS.md` (sections `language`, `memory`), `app/core/layers/USER.md` (sections `user`, `profile`, `memories`, `recent_chats`), `app/orchestration/AGENTS.md`, `app/orchestration/BOOTSTRAP.md`
- Modify: `app/core/prompts.py` (SOUL, LANGUAGE_RULE, MEMORY_RULE, USER_SECTION, PROFILE_SECTION, MEMORIES_SECTION, RECENT_CHATS_SECTION), `app/orchestration/prompts.py` (ORCHESTRATOR_SYSTEM, BOOTSTRAP_SECTION)
- Test: `tests/test_prompt_files.py`

**Interfaces:**
- Consumes: `load_prompt`, `load_sections` (task 1).
- Produces: `LAYERS = Path(__file__).parent / "layers"` in `app/core/prompts.py`; `LAYER_FILES: list[Path]` in `tests/test_prompt_files.py` listing every layer file (task 3 appends to it).

- [ ] **Step 1: Write the failing tests:**

```python
LAYER_FILES = [LAYERS / "SOUL.md", LAYERS / "AGENTS.md", LAYERS / "USER.md",
               ORCHESTRATION / "AGENTS.md", ORCHESTRATION / "BOOTSTRAP.md"]
# ORCHESTRATION = Path(app.orchestration.prompts.__file__).parent

def test_layer_paths_do_not_depend_on_the_working_directory():
    assert all(path.is_absolute() for path in LAYER_FILES)

def test_every_layer_file_exists_without_blank_edges():
    for path in LAYER_FILES:
        text = path.read_text(encoding="utf-8")
        assert not text.startswith("\n") and text.endswith("\n") and not text.endswith("\n\n"), path
```

- [ ] **Step 2: Run** `uv run pytest tests/test_prompt_files.py -v`. Expected: the two new tests FAIL (files missing).

- [ ] **Step 3: Move the texts.** Each file holds the constant's current text without the leading `"\n\n"` and with Python escapes resolved (`\"` becomes `"`). Assignments, with the separators kept in Python:
  - `SOUL = load_prompt(LAYERS / "SOUL.md")`
  - `LANGUAGE_RULE`, `MEMORY_RULE` from `load_sections(LAYERS / "AGENTS.md", ("language", "memory"))`
  - `USER_SECTION`, `PROFILE_SECTION`, `MEMORIES_SECTION`, `RECENT_CHATS_SECTION` = `"\n\n" +` the matching section of `load_sections(LAYERS / "USER.md", ("user", "profile", "memories", "recent_chats"))`
  - `ORCHESTRATOR_SYSTEM = SOUL + "\n\n" + load_prompt(Path(__file__).parent / "AGENTS.md")`
  - `BOOTSTRAP_SECTION = "\n\n" + load_prompt(Path(__file__).parent / "BOOTSTRAP.md")`
  Keep the explanatory comments above each constant in the `.py` file.

- [ ] **Step 4: Run** `uv run pytest tests/test_prompt_files.py tests/test_prompt_layers.py tests/test_orchestrator.py -v`. Expected: all pass (DB tests in `test_orchestrator.py` may skip if Postgres is down; that is fine for this step).

- [ ] **Step 5: Run the identity check** (section above). Expected: `IDENTICAL`.

- [ ] **Step 6: Commit** the five `.md` files, both `prompts.py` files and the test file: `git commit -m "Load SOUL, shared rules, USER and orchestrator layers from files"`.

---

### Task 3: Specialist AGENTS.md files

Agent: implementer

**Files:**
- Create: `app/agents/docs/AGENTS.md`, `app/agents/sql/AGENTS.md`, `app/agents/chart/AGENTS.md`, `app/agents/research/AGENTS.md`
- Modify: `app/agents/{docs,sql,chart,research}/prompts.py` (DOCS_SYSTEM, SQL_SYSTEM, CHART_SYSTEM, RESEARCHER_SYSTEM only)
- Test: `tests/test_prompt_files.py`

**Interfaces:**
- Consumes: `load_prompt`, `SOUL`, `LANGUAGE_RULE`, `MEMORY_RULE` from `app/core/prompts.py`; `LAYER_FILES` (task 2).

- [ ] **Step 1: Extend `LAYER_FILES`** with the four new paths (built from each agent's `prompts.__file__`). Run `uv run pytest tests/test_prompt_files.py -v`. Expected: FAIL (files missing).

- [ ] **Step 2: Move the texts.** Each file holds the agent's body after SOUL (or the whole text for the researcher), with its existing `{...}` placeholders.
  - `SQL_SYSTEM`, `CHART_SYSTEM`: `SOUL + <the separator used today> + load_prompt(...)`.
  - `DOCS_SYSTEM`: today an f-string with `LANGUAGE_RULE` and `MEMORY_RULE` inlined; the file gets `{language_rule}` and `{memory_rule}` slots and `prompts.py` sets `DOCS_SYSTEM = SOUL + "\n\n" + load_prompt(...).format(language_rule=LANGUAGE_RULE, memory_rule=MEMORY_RULE)`. If the docs body contains literal braces, double them in the file.
  - `RESEARCHER_SYSTEM = load_prompt(...)`, no SOUL.
  - `ROUTE_DESCRIPTION`, `TOOL_DESCRIPTION`, `REVIEW`, `SQL_RULES`, `TABLES_SECTION` and the research source lines stay in `prompts.py`.

- [ ] **Step 3: Run** `uv run pytest tests/test_prompt_files.py tests/test_prompt_layers.py -v` and the identity check. Expected: all pass, `IDENTICAL`.

- [ ] **Step 4: Commit** the four `.md` files, four `prompts.py` files and the test file: `git commit -m "Load specialist agent prompts from AGENTS.md files"`.

---

### Task 4: Docs and dev reload

Agent: drafter

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (through `uv add --dev watchfiles`), `CLAUDE.md`, `ARCHITECTURE.md`, `.claude/rules/agents.md`
- Modify (main checkout, not committed): `C:\Users\Asus ROG\projects\smallie\CLAUDE.local.md`

Load the `writing-llm-document` skill before editing `CLAUDE.md`, `.claude/rules/agents.md` and `CLAUDE.local.md`.

- [ ] **Step 1:** `uv add --dev watchfiles` (uvicorn's `--reload-include` needs it; without it uvicorn uses its stat reloader and ignores the option). Expected: `pyproject.toml` gains `watchfiles` in the dev group, `uv.lock` updated.
- [ ] **Step 2: `CLAUDE.md` line 39:** the uvicorn command becomes `uv run uvicorn app.api.main:app --reload --reload-include "*.md" --loop asyncio:SelectorEventLoop`.
- [ ] **Step 3: `.claude/rules/agents.md`:** line 15 says `SOUL` is the text of `app/core/layers/SOUL.md`, loaded by `app/core/prompts.py`. Step 1 of "adding an agent" (line 19) says the system prompt text goes in `app/agents/<name>/AGENTS.md`, loaded in `prompts.py` with `load_prompt(Path(__file__).parent / "AGENTS.md")`, keeping `{language_rule}` / `{memory_rule}` slots; the shared rules live in `app/core/layers/AGENTS.md`. Add one line: separators between layers stay in Python, files never start with a blank line, and `tests/test_prompt_files.py` `LAYER_FILES` lists every layer file.
- [ ] **Step 4: `ARCHITECTURE.md`:** in the section that describes the prompt layers (grep `SOUL`), add where each layer's file lives (table from the spec's "File layout") and that files are read once at import.
- [ ] **Step 5: `CLAUDE.local.md` (main checkout):** the `writing-llm-document` bullet's list gains the layer files: `app/core/layers/*.md`, `app/**/AGENTS.md`, `app/orchestration/BOOTSTRAP.md`.
- [ ] **Step 6: Commit** `pyproject.toml`, `uv.lock`, `CLAUDE.md`, `ARCHITECTURE.md`, `.claude/rules/agents.md`: `git commit -m "Document prompt layer files and reload on .md edits"`.

---

### Task 5: Whole-branch verification

Agent: verifier

- [ ] **Step 1:** `powershell -ExecutionPolicy Bypass -File scripts\dev_db.ps1`, then `uv run pytest`. Expected: all pass, no DB tests skipped. Two tests may fail in a fresh worktree from `.env` credentials (memory `worktree-env-credential-tests`); confirm they fail the same way on `main` before accepting.
- [ ] **Step 2:** the identity check. Expected: `IDENTICAL`.
- [ ] **Step 3:** `Dockerfile` and `Dockerfile.agent` copy `app/` whole and `.dockerignore` excludes no `*.md` under `app/` (quote the lines).
- [ ] **Step 4:** `git diff main --stat` touches no `agent.py`, `app/agents/factory.py` or `app/orchestration/orchestrator.py`.
