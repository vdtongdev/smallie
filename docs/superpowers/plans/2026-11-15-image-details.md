# Image Details Implementation Plan

> **For agentic workers:** Run this plan with the foreman loop: each task goes to the agent on its `Agent:` line, then to the verifier. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a user asks about an uploaded image, the answer gives every text and number from the image's stored reading.

**Architecture:**
- Image readings are stored in a structured layout, as self-contained chunks that keep their order.
- When a search hits any chunk of an image, it returns that image's whole reading.
- The docs agent's first search runs in code, before its first model call. A provider that ignores `tool_choice` can no longer skip it.
- Two code rules send image questions and short follow-ups to the docs agent instead of the orchestrator.

**Tech Stack:** Python 3.12, LangChain `create_agent` middleware, langchain-postgres PGVector, pytest (asyncio auto).

**Spec:** `docs/superpowers/specs/2026-11-15-image-details-analysis.md` (approach B, approved 2026-11-15). This plan changes B's step 1: instead of a retry call when the model ignores the forced tool, the search runs before the first model call. That removes a model call instead of adding one (trap `extra-model-calls`).

## Global Constraints

- **Branch:** `0.8.3/image-details`. Rename the current branch `0.8.3/image-details-analysis`. The whole plan is one changelog fix ("agents don't give an image's values"), so it ships on one branch and one PR.
- **User scoping:** every query and filter keeps `user_id`. Bulk functions take a `user_ids` scope that tests always set (trap `tests-shared-dev-db`).
- **Sync DB or vector work** in async code goes through `asyncio.to_thread`.
- **No new model call, retry or extra agent step** (trap `extra-model-calls`). Embedding calls are allowed.
- **Prompt edits:**
  - load `writing-llm-document` first;
  - no realistic example values (trap `copyable-prompt-examples`);
  - check `.format` braces (trap `prompt-format-braces`: `AGENTS.md` files go through `.format`);
  - examples in English only (trap `reply-language`).
- **Tests:**
  - write them from the behaviour, before the code (`.claude/rules/testing.md`);
  - never loosen a failing test;
  - run `scripts\dev_db.ps1` before DB tests;
  - a skipped DB test is not a pass.
- **Run tests with** `uv run pytest` from the worktree.

## Review Focus

1. An image reading longer than the expansion cap (a dense scanned page): the search returns the first parts up to the cap, never a cut mid-line. Test in Task 2.
2. A follow-up like "thanks" or "ok" after a docs answer stays with the orchestrator: no docs search for small talk. Test in Task 4.
3. Team mode: the docs speaker still counts as having searched (`source_tools`) when the search ran before its first call, and `source_first` doesn't force a second search. Test in Task 3.
4. A user with no documents: the docs agent isn't available, so nothing runs. A user whose search finds nothing: the agent gets `NO_PASSAGES` and says so. Test in Task 3.
5. Old uploads stored before this change, without `part` metadata, still come back whole after the re-chunk script. Without the script, they come back as before and nothing crashes. Test in Task 2 (missing `part`) and Task 5.

---

### Task 1: Labelled, ordered image chunks

**Agent:** implementer

**Files:**
- Modify: `app/features/documents/ingest.py:24-25` (splitters) and `:64-75` (`image_chunks`)
- Test: `tests/test_ingest.py`

**Interfaces:**
- Produces:
  - every chunk from `image_chunks(readings, pages, filename) -> list[Document]` starts with the image's label line (`[Image N]` or `[Image N, p. P]`);
  - its metadata adds `part: int` (0-based, in reading order) to the existing `filename`, `kind="image"`, `image`, `page`;
  - `IMAGE_LABEL(number: int, page: int | None) -> str` returns that label.

- [ ] **Step 1: Write the failing tests** in `tests/test_ingest.py`:
  - `test_every_image_chunk_starts_with_its_label`: a 2,500-character reading made of 100 distinct `key N: value N` lines, image 2 on page 3. Every chunk's `page_content` starts with `"[Image 2, p. 3]\n"`. `[c.metadata["part"] for c in chunks] == list(range(len(chunks)))`. Every chunk is at most 800 characters.
  - `test_image_chunks_split_on_lines_without_overlap`: the same reading. Join the chunks' contents with the label line removed; the result equals the reading. No line is cut in two.
- [ ] **Step 2: Run them to see them fail**
  - Run: `uv run pytest tests/test_ingest.py -k "image_chunk" -v`
  - Expected: both FAIL. Today only the first chunk has the label, and there is no `part`.
- [ ] **Step 3: Implement**
  - A second splitter, `_image_splitter`: `RecursiveCharacterTextSplitter(chunk_size=800 - <label room>, chunk_overlap=0, separators=["\n\n", "\n", " ", ""])`.
  - `image_chunks` splits the reading body without the label, then prefixes the label line to each piece and sets `part`.
  - Text chunks keep `_splitter` unchanged.
- [ ] **Step 4: Run the tests to see them pass**
  - Run: `uv run pytest tests/test_ingest.py tests/test_images.py -v`
  - Expected: PASS, including the existing `test_image_chunks_follow_text_chunks`.
- [ ] **Step 5: Commit:** `git commit -m "Every image chunk carries its label and part number"`

### Task 2: A hit on an image returns its whole reading

**Agent:** implementer

**Files:**
- Modify: `app/agents/docs/tools.py` (`search_user_chunks`, `search_with_graph`, `_search_by_cards`, `_format`)
- Test: `tests/test_docs_search.py` (create). Fake the embeddings the way the existing search or graph DB tests do (grep `tests/` for `get_embeddings` monkeypatching), and create users with `make_user` (`tests/test_graph.py`).

**Interfaces:**
- Consumes: the Task 1 metadata (`kind`, `image`, `part`, `document_id`, `user_id`).
- Produces: `whole_images(hits: list[Document], user_id: str) -> list[Document]`. Each image hit becomes one Document holding all parts of that image in `part` order, with the label line kept once. Other hits stay as they are.
  - Two hits on the same image collapse into one.
  - The total added text is capped by `MAX_IMAGE_CHARS = 6000` per search. Past the cap, the parts are cut at a part boundary, never mid-line.
  - Chunks without `part` (old uploads) are ordered by their chunk id's number.

- [ ] **Step 1: Write the failing tests** (`requires_db`):
  - `test_vague_query_returns_the_values_of_the_image`: index an image of two parts for a new user.
    - part 0: `[Image 1]` plus a description: "screenshot of a settings window";
    - part 1: `[Image 1]` plus `Serial Number: 0000-TEST`.
    - The fake embeddings put a vague query nearest part 0. `search_with_graph(user, "detail of the image")` contains `"Serial Number: 0000-TEST"` and contains `"[Image 1]"` once for that file.
  - `test_whole_image_is_capped_at_a_part_boundary`: an image of 12 parts of ~780 characters each. The result has at most `MAX_IMAGE_CHARS` + one part, and ends with a complete part.
  - `test_old_chunks_without_part_keep_chunk_order`: two image chunks stored without `part`, ids `doc:1` and `doc:2`. Both texts come back in id order.
  - `test_other_users_images_never_join`: another user's chunks of the same `document_id` value are never added.
- [ ] **Step 2: Run them to see them fail**
  - Run: `uv run pytest tests/test_docs_search.py -v`
  - Expected: FAIL. Today only part 0 is returned.
- [ ] **Step 3: Implement `whole_images`** and apply it to the hits in both search paths, before `_format`.
  - Fetch the parts with one filtered store query per image: filter `user_id`, `document_id`, `kind="image"`, `image=N`; reuse the query vector, `k=50`; then sort.
  - Keep `search_documents`' signature and docstring.
- [ ] **Step 4: Run the tests to see them pass**
  - Run: `uv run pytest tests/test_docs_search.py tests/test_docs_image_answers.py -v`
  - Expected: PASS, with no DB test skipped.
- [ ] **Step 5: Commit:** `git commit -m "A search hit on an image returns its whole reading"`

### Task 3: The docs agent's first search runs in code

**Agent:** implementer

**Files:**
- Modify: `app/agents/docs/agent.py:66-73` (replace `search_first`) and `:76-78` (`build_docs_agent`)
- Test: `tests/test_docs_image_answers.py`

**Interfaces:**
- Consumes: `search_with_graph(user_id, query, entity="") -> str` (`app/agents/docs/tools.py`).
- Produces: `prefetch_search`, a `before_agent` middleware.
  - It appends an `AIMessage` with one `search_documents` tool call, with args `{"query": <latest user text>}`.
  - It then appends the matching `ToolMessage` (`name="search_documents"`, same `tool_call_id`).
  - The latest user text is the newest `HumanMessage` whose `name` is None.
  - The search runs through `asyncio.to_thread`.

- [ ] **Step 1: Write the failing tests:**
  - `test_docs_answers_from_the_reading_even_when_the_model_ignores_tool_choice`: a scripted model that never returns tool calls and answers from any tool result it sees. The final answer contains the reading's part number `KVR32N22S8/8`. Exactly one model call was made.
  - `test_prefetch_searches_the_users_own_message`: the fake `search_with_graph` records its query. It equals the user's message, not the call note.
  - `test_team_speaker_counts_as_searched`: build the docs agent and run it with `AgentContext(agent=TEAM_MODE, sources=frozenset({"user_files"}))`. The returned turn messages pass `docs_agent.SPEC.gave_up(...) is False`, and `search_with_graph` ran once.
  - `test_no_passages_is_said_plainly`: the search returns `NO_PASSAGES`. The answer model sees it, and the docs SPEC's `no_result` flags the tool result.
  - Keep `test_solo_docs_agent_searches_before_answering_an_image_question` unchanged.
- [ ] **Step 2: Run them to see them fail**
  - Run: `uv run pytest tests/test_docs_image_answers.py -v`
  - Expected: the new tests FAIL.
- [ ] **Step 3: Implement `prefetch_search`** and use it in `build_docs_agent` in place of `search_first`.
  - Remove `search_first` and its import of `is_first_call` if nothing else in the file uses it.
  - Name the middleware's node so it doesn't clash with a route or `tools` (trap `workflow-node-names`).
- [ ] **Step 4: Run the tests to see them pass**
  - Run: `uv run pytest tests/test_docs_image_answers.py tests/test_team.py tests/test_call_note.py -v`
  - Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "Docs agent searches in code before its first model call"`

### Task 4: Image questions and short follow-ups reach the docs agent

**Agent:** implementer

**Files:**
- Modify: `app/agents/docs/agent.py:20` (`HINTS`) and `app/orchestration/router.py:66-76` (`router`)
- Test: `tests/test_router.py`

**Interfaces:**
- Produces:
  - `HINTS` also matches `screen shots?` and `snap ?shots?`;
  - `follow_up_route(messages, available, specs) -> str | None` in `router.py`.
- `follow_up_route` returns the route of the previous answer (`previous_route` with `message_routes(specs)`) when all of these hold:
  - the latest message has at most `FOLLOW_UP_WORDS = 10` words;
  - it mentions no agent (`mentioned_routes` is empty);
  - it isn't small talk (`SMALL_TALK`: thanks / thank you / ok / okay / great / cool / bye / hi / hello / cảm ơn / tạm biệt / chào, the whole message);
  - that route's spec has `answers_directly=True`.
- The router uses it only when `obvious_route` returned None, and sets `direct=True`. If the specialist gives up, the orchestrator still takes over (`fallback.py`).

- [ ] **Step 1: Write the failing tests:**
  - `test_screen_shot_in_two_words_routes_to_docs`: with docs available, `obvious_route("what can you tell me about the screen shot", available) == "docs"`.
  - `test_short_follow_up_goes_back_to_the_previous_specialist`: messages = user question, an `AIMessage(name="docs_agent")`, then "you don't give me the numbers". The router returns `{"route": "docs", "direct": True}`.
  - `test_small_talk_after_a_specialist_stays_with_orchestrator`: the same setup with "thanks!" returns the orchestrator.
  - `test_follow_up_naming_another_agent_is_not_sticky`: after a docs answer, "and the AAPL price" returns sql or the orchestrator, never docs.
  - `test_long_new_question_is_not_a_follow_up`: an 11-word message after a docs answer, with no hints, goes to the orchestrator.
- [ ] **Step 2: Run them to see them fail**
  - Run: `uv run pytest tests/test_router.py -v`
  - Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the regex change and `follow_up_route`. Team mode and agents forced by the API stay unchanged.
- [ ] **Step 4: Run the tests to see them pass**
  - Run: `uv run pytest tests/test_router.py tests/test_team.py tests/test_orchestrator.py -v`
  - Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "Route screen-shot questions and short follow-ups to the agent that answered"`

### Task 5: Re-chunk existing image readings

**Agent:** implementer

**Files:**
- Create: `scripts/rechunk_images.py`
- Test: `tests/test_rechunk_images.py`

**Interfaces:**
- Consumes: `image_chunks`, `get_vector_store`, `chunk_ids`, `delete_document_chunks` (`app/features/documents/ingest.py`).
- Produces: `rechunk(user_ids: list[str] | None = None) -> int`, the number of files rebuilt. `main()` calls it with no scope.

What `rechunk` does for each ready file that has `kind="image"` chunks:
1. Read the file's chunks in id order.
2. Rebuild each image's reading by joining its old chunks and removing the overlap between neighbours: the longest suffix of one chunk that is a prefix of the next. Drop the old leading label.
3. Re-embed `[*text chunks, *image_chunks(...)]` under fresh ids `chunk_ids(document_id, n)`.
4. Delete the old ids that are no longer used.
5. Update `documents.chunk_count`.

Readings aren't re-read: no model calls.

- [ ] **Step 1: Write the failing tests** (`requires_db`, users from `make_user`):
  - `test_rechunk_rebuilds_old_overlapping_chunks`: store two old-style chunks (only the first labelled, 120-character overlap) for user A. After `rechunk([A])`, every image chunk starts with the label and has `part`. The joined bodies equal the original reading. `chunk_count` matches the number of rows.
  - `test_rechunk_leaves_other_users_alone`: user B's chunks are unchanged after `rechunk([A])`.
  - `test_rechunk_keeps_text_chunks`: a PDF's text chunks keep their text and come first.
- [ ] **Step 2: Run them to see them fail**
  - Run: `uv run pytest tests/test_rechunk_images.py -v`
  - Expected: FAIL (module missing).
- [ ] **Step 3: Implement** the script.
  - Sync code, run with `uv run python -m scripts.rechunk_images`.
  - If it turns async, apply trap `windows-async-script-loop`.
- [ ] **Step 4: Run the tests to see them pass**
  - Run: `uv run pytest tests/test_rechunk_images.py -v`
  - Expected: PASS, none skipped.
- [ ] **Step 5: Commit:** `git commit -m "Script to re-chunk stored image readings"`

### Task 6: Prompts: structured reading, answer every value, notes are labels

**Agent:** drafter (load `writing-llm-document` first)

**Files:**
- Modify: `app/features/documents/prompts.py` (`IMAGE_PROMPT`, never formatted: single braces)
- Modify: `app/agents/docs/AGENTS.md:7` (formatted: any brace doubled)
- Modify: `app/orchestration/AGENTS.md` near line 7 (check how it is formatted before adding braces)

**Content to say:**
- **`IMAGE_PROMPT`:** the structured variant tested in the spec, §1.5:
  - two parts, `DESCRIPTION:` (one to three sentences) then `CONTENT:`;
  - one `label: value` per line;
  - tables as Markdown tables with every cell;
  - empty fields as `label: (empty)`;
  - units, codes and IDs unchanged;
  - charts give title, axes, legend and readable values;
  - "No text." when none;
  - write only what is visible.
- **Docs `AGENTS.md`**, replacing the image line:
  - a passage starting with `[Image N]` or `[Image N, p. P]` is the full stored reading of that image;
  - when asked about an image or its details, give every label with its value and every table row from the reading, as a list or table;
  - never write a field name without its value, and never a placeholder;
  - say which asked detail the reading lacks.
- **Orchestration `AGENTS.md`:**
  - the uploaded-documents note gives file names and one-line summaries; they are labels, not the files' contents;
  - never state a value, number or detail of a file from them;
  - for what a file says or shows, hand off to docs.

- [ ] **Step 1: Make the edits.**
- [ ] **Step 2: Check the prompts still load**
  - Run: `uv run pytest tests/test_orchestrator.py tests/test_docs_image_answers.py tests/test_images.py -v`
  - Expected: PASS. This also catches brace errors at import.
- [ ] **Step 3: Commit:** `git commit -m "Prompts: structured image reading, answer every value, file notes are labels"`

### Task 7: Measure, then docs, changelog and version

**Agent:** drafter (docs). The main session runs the measurement itself.

**Files:**
- Modify: `CHANGELOG.md`, `pyproject.toml`, `uv.lock`, `ARCHITECTURE.md`, `DEPLOYING.md`, `.claude/rules/data.md`

- [ ] **Step 1: Measure on the real models** (main session, about 6 chat calls on deepseek-chat plus about 1 image call on Gemini Flash; about $0.01).
  1. Rebuild the agent containers: `docker compose -p smallie up -d --build --no-deps docs sql research chart` (trap `compose-worktree-project-name`).
  2. Start the app from the worktree.
  3. Run `rechunk` for the test user only.
  4. Send the three messages from the spec. Record, per turn:
     - who answered;
     - whether `KVR32N22S8/8`, `6E97A6C0` and the tRC row `71 74 74 74` appear;
     - calls per message and cost against the 0.8.2 traces (turn 1: 1 orchestrator call; turn 2: 1 docs call with no search; turn 3: 1 orchestrator call).
- [ ] **Step 2: Docs.**
  - `CHANGELOG.md`: a new `## 0.8.3 - Fixes` heading with one bullet: **Fix: agents describe an uploaded image but don't give its values**. Causes and changes in plain words, as in the spec's §1, with the measured calls and cost.
  - `pyproject.toml` version `0.8.3`, then `uv lock`.
  - `ARCHITECTURE.md` and `.claude/rules/data.md`: the reading format, labelled image parts, whole-reading retrieval, the search running before the first call, the follow-up routing rule.
  - `DEPLOYING.md`: run `uv run python -m scripts.rechunk_images` once after deploying 0.8.3 (embeddings only).
- [ ] **Step 3: Run the full suite**
  - Run: `uv run pytest`
  - Expected: all pass, no DB test skipped.
- [ ] **Step 4: Commit:** `git commit -m "Changelog, version and docs for 0.8.3"`
