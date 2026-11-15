# Why agents don't give an image's full details: analysis and recommendation

Date: 2026-11-15. Code: `main` at `eeebabc` (0.8.2, not deployed). Status: analysis only, waiting for approval.

## The case

The user uploaded `Screenshot 2026-11-14 112705.png`, a CPU-Z SPD window. Then they sent three messages:

| Turn | Message | Who answered | What they got |
|---|---|---|---|
| 1 | "what can you tell me about the screen shot" | orchestrator, no tool call | the card summary reworded, no values |
| 2 | "give me the detail of the image" | docs agent, **no tool call** | field names only, no values |
| 3 | "you don't give me the numbers" | orchestrator, no tool call | invented placeholders ("[Part Number]", "CL16-18-18-36") |

LangSmith project `smallie` (local) has the traces: `01a1e043-fa5e…` (turn 1), `01a1e045-0450…` with docs trace `01a1e045-055f…` (turn 2), and `01a1e045-6a43…` (turn 3). All chat calls ran on `deepseek/deepseek-chat`.

## Headline

**The stored reading has every value, and no agent ever read it.** Gemini 2.5 Flash read the image at upload. Its 978-character reading holds every field and the whole JEDEC table (DB chunk `94e0ded7…:1`). None of the three turns retrieved it:

- Turns 1 and 3: the orchestrator answered from the one-line card note.
- Turn 2: the docs agent never searched, although 0.8.2 forces the search.
- Had it searched, the vague query would have returned only the description chunk, which has no values.

Detail is lost on the way from the store to the answer, not at the reading.

## 1. Root causes, ranked by how much detail each loses

### 1.1 Chunking splits the description from the values, and retrieval finds only the description (loses all values on vague questions)

- `image_chunks` (`app/features/documents/ingest.py:64-75`) builds one document of `[Image 1]`, the description and the transcription. `_splitter` (`ingest.py:25`, 800 characters, 120 overlap) then splits that document.
- For this file the split made two chunks:
  - chunk 0 (364 chars): `[Image 1]` plus the description ("a screenshot of the CPU-Z software … module size, manufacturer, part number …");
  - chunk 1 (614 chars): the bare transcription, starting `CPU-Z / CPU Mainboard Memory SPD …`, with no `[Image 1]` label and no words such as "image" or "screenshot".
- The description is phrased the way users ask ("screenshot", "details", "manufacturer, part number, timings"). It wins every vague query. Measured with `search_with_graph` on this user's real data (embeddings only):

| Query | Graph scope | Screenshot chunks returned |
|---|---|---|
| "detail of the image" | none | chunk 0 only (plus 5 other files) |
| "screenshot details" | none | chunk 0 only |
| "what is in the screen shot" | none | chunk 0 only, ranked 2nd |
| "CPU-Z memory module details" | screenshot | chunks 1 and 0 |
| "Kingston part number" | screenshot | chunks 1 and 0 |

- The values come back only when the question already names something in the image. "Give me the detail of the image" never reaches them.
- `_search_by_cards` (`app/agents/docs/tools.py:49-63`) adds to this: it takes **one** passage (`k=1`, `tools.py:55`) per card-matched file and fills the rest of `TOP_K=6` (`tools.py:10`) from all files. A question about one image gets its best chunk plus five chunks from other files.
- Chunk 1 also loses the `[Image N]` label that `AGENTS.md:7` uses to tell the agent "this is an image reading".
- Small images escape this: the receipt (749 chars) fits one chunk, and its values were answered correctly in the 15:46 chat. Any image whose reading passes 800 characters is split: tables, forms, settings windows and scanned pages, the cases the user cares about.

### 1.2 The forced first search is not honoured (turn 2 never searched)

- `search_first` (`app/agents/docs/agent.py:66-73`) worked as written. The turn-2 model call carries `tool_choice={"type":"function","function":{"name":"search_documents"}}` (trace `01a1e045-055f…`).
- `deepseek/deepseek-chat` on OpenRouter ignored it: `finish_reason=stop`, plain text, no tool call.
- The app has no OpenRouter `provider.require_parameters`, which would send the call only to providers that support `tool_choice`. It also has no check that a forced tool was actually called (`grep require_parameters app/` finds nothing). A known related fact: this model is served by DeepInfra or StreamLake on OpenRouter.
- The 0.8.2 test passes because its fake model honours `tool_choice`. Real providers don't always, so the turn-2 behaviour is **still possible on `main`**.
- The model then answered from the chat history: turn 1's card-based reply is in its context, so it listed field names.

### 1.3 The orchestrator answers file-content questions from the card note (turns 1 and 3; turn 3 invented values)

- Routing (`app/orchestration/router.py:73-76`) sends a message to docs without a model call only when `obvious_route` matches.
  - Turn 1 "screen shot" (two words) misses `HINTS` (`app/agents/docs/agent.py:20`, `screenshots?`).
  - The file-name stem `screenshot 2026-11-14 112705` doesn't match either (`agent.py:52`).
  - The name-word mention needs "screenshot" as one word (`agent.py:57-63`).
  - So turn 1 went to the orchestrator.
  - Turn 3 ("you don't give me the numbers") has no hint word, and the router has no rule that sends a short follow-up to the previous specialist.
- The orchestrator's notes hold the card: `Screenshot 2026-11-14 112705.png (software screenshot: The screenshot displays … manufacturer, part number, and timings.)` (`route_info`, `agent.py:40-54`). It had `handoff` and `ask_docs`, and its prompt says a follow-up to a specialist's answer should be handed off. In both turns it answered itself:
  - turn 1 reworded the card;
  - turn 3 wrote a template with placeholders.
- Nothing tells it that the card is a label, not the file's content, or that it must not state values it hasn't been given.

### 1.4 The answer prompt doesn't ask for the values

- `app/agents/docs/AGENTS.md:7` says how to cite an image reading, then: "Say plainly only which asked detail the reading does not cover."
- Nothing says "when asked for an image's details, give every label and value from the transcription" or "never write a field name without its value".
- With the description chunk alone, "list what the image contains" is the natural answer, and the docs agent gave exactly that.
- This cause is minor next to 1.1 to 1.3, but it decides how complete the answer is once the right passage arrives.

### 1.5 The reading's layout (lost nothing here; a risk for other images and models)

- `IMAGE_PROMPT` (`app/features/documents/prompts.py:12-18`) asks for a description and "transcribe every piece of text". It doesn't say how to lay out labels, values and tables.
- Effects seen:
  - The stored reading flattens the two-column panel into lines like `Slot #1 DDR4 (UDIMM) Module Size 8 GBytes` and drops the empty fields (`Correction`, `Registered` sit next to nothing).
  - On a re-rendered proxy of this screenshot, Gemini 2.5 Flash with the current prompt wrote **each label and each value on its own line**, and each table column as a run of single values. Split at 800 characters, a label and its value can land in different chunks, and table columns can no longer be told apart.
- Prompt comparison: 8 real calls. Images: a re-rendered CPU-Z SPD panel (the original isn't kept after upload) and `uploads/vision-check/receipt.jpg`.

| Model | Prompt | Values found (22) | Labels (15) | Layout | Cost/image | Time |
|---|---|---|---|---|---|---|
| gemini-2.5-flash | current | 22 | 15 | one item per line, columns lost | $0.00114 | 3.6 s |
| gemini-2.5-flash | structured | 22 | 15 | `label: value`, Markdown table, `(empty)` | $0.00125 | 2.3 s |
| nemotron-3.5-super-vl:free | current | 22 | 13 | rows kept, labels misspelled (`Manu.`) | $0 | 18 s |
| nemotron-3.5-super-vl:free | structured | 22 | 15 | `label: value` | $0 | 34 s |

- Both models transcribe every value from a clean screenshot. The structured prompt costs about 10% more on Gemini (+$0.0001 per image) and gives a layout that survives chunking and is easy to quote.
- The free model takes 18 to 54 s per image (the receipt took 44 and 55 s). That is slow, but it was complete on these samples.
- Not measured: a low-quality photo or scan. A weaker model there may describe instead of transcribe. The reading-quality check below covers that.

## 2. Approaches

All three keep the 0.8.1 design: read once at upload, store the reading, answer from it.

### A. Whole-reading retrieval plus a structured reading (storage and retrieval only)

1. `IMAGE_PROMPT` asks for `DESCRIPTION:` and `CONTENT:`. Inside CONTENT: one `label: value` per line, tables as Markdown tables with every cell, empty fields as `(empty)`, units and IDs unchanged (the variant tested above).
2. Every chunk of a reading starts with its context: `[filename, Image N, p. P]`, so a values chunk is never anonymous. (Or: don't split readings up to a size cap such as 4,000 characters, which fits a dense table and stays under the embedding limit.)
3. Retrieval expands image hits. When `search_documents` returns any chunk of an image, it returns that image's whole reading, all its chunks in order, as one passage. The expansion is capped per call, for example 6,000 characters.
4. A question scoped to one file (graph scope or a file name) returns more of that file first, not one chunk plus five from other files.

- Tradeoffs:
  - It fixes the main loss (1.1) for every image type, with no new model call.
  - It doesn't fix 1.2 or 1.3: an agent that never searches still gets nothing.
  - Existing uploads need re-ingesting, or the old readings keep their split. A re-chunk script can rebuild them from stored chunks without re-reading.
- Cost:
  - per upload: +~120 prompt tokens, about +$0.0001 per image on Gemini Flash;
  - per message: +300 to 1,500 input tokens to the docs agent when an image is hit, about +$0.0001 to $0.0005 on deepseek-chat.
  - No new calls.

### B. A plus reliable delivery: the search really runs, and the right agent answers (recommended)

Everything in A, plus:

1. **The search runs in code, not by `tool_choice`.** On the docs agent's first call of a turn, if the model returns no `search_documents` call, the middleware runs `search_documents` with the user's message as the query. It adds the tool call and the result to the messages, then calls the model again. A model that honours `tool_choice` never triggers this.
   - Also set OpenRouter's `provider: {"require_parameters": true}` whenever `tool_choice` is set, so providers that ignore it are skipped.
2. **Routing:**
   - `HINTS` also matches `screen ?shots?`, `snap ?shots?` and "picture of".
   - A short message with no hint, right after a specialist's answer, goes back to that specialist. Turn 3 then reaches docs without a model choice.
3. **Orchestrator prompt:** the file notes are labels. Never state a file's values or contents from them. For any question about what a file or image says or shows, hand off or ask docs.
4. **Docs answer rule:** when asked for an image's details, give every label with its value and every table row from the reading, as a list or table. Never list a field name without its value. If the reading lacks something, say which.

- Tradeoffs:
  - It covers all three failing turns.
  - Step 1 adds a model call only when the model ignored the forced tool: one extra docs call, ~2.2k input tokens, about $0.0009 on deepseek-chat, and $0 on a model that complies.
  - Step 2's follow-up rule can send an unrelated short message to the last specialist. Limits: only when the message has no other agent's hint, and the specialist's own fallback still hands back.
- Cost: as A per upload; per message as A, plus up to one docs call (~$0.0009) on non-complying models. Calls per message are unchanged on complying models.

### C. Re-read the image at question time (later; part 2 of the image spec)

- Keep the original image. When a question is about one image, send the image itself plus the question to the image model.
- Pros:
  - highest fidelity;
  - it answers things the reading skipped (colours, positions).
- Cons:
  - storing originals (privacy, disk), which `docs/superpowers/specs/2026-11-15-image-reading-design.md` left out of part 1;
  - one vision call per question: about $0.001 to $0.002 on Gemini Flash, and 18 to 55 s on the free model;
  - deciding which questions need it.
- It doesn't remove the need for B: without B, the orchestrator still answers from the card and the docs agent may still skip the tool.

### Optional, with A or B: a reading quality check at upload

- A rule check in code, with no model call. Flag a reading whose CONTENT part is under ~5% of its length, or empty while the description mentions text, a table or numbers.
- Retry once with the fallback image model. Cost: one extra image call (~$0.001) only for flagged images.
- It catches models that describe instead of transcribe. It was not needed for any image measured here.

### Recommendation

**B.** The reading was complete, and every turn failed between the store and the answer. A alone leaves turn 2 (no search) and turns 1 and 3 (orchestrator from the card) broken. B fixes all three, works for tables, receipts, forms, screenshots and scanned pages, and adds a model call only when a provider ignores the forced tool. Leave C for part 2. Add the quality check if a weak image model shows up in practice.

## 3. What the fix plan would contain

Patch `0.8.3`. One branch per item (`0.8.3/<slug>`), each with regression tests written first from the behaviour below. Before editing any code, prompt or script, each brief includes the output of `mem select --path <file> --traps`. Prompt edits load `writing-llm-document`.

1. **Structured reading** (`app/features/documents/prompts.py` `IMAGE_PROMPT`).
   - Test: a fake vision reply in the new format is stored with labels and values on the same line, and tables intact.
2. **Self-contained image chunks** (`app/features/documents/ingest.py` `image_chunks`).
   - Every chunk of a reading carries `[filename, Image N, p. P]`, or readings up to the cap stay whole.
   - Test: a 2,000-character reading gives chunks that all start with the label.
3. **Whole-reading retrieval** (`app/agents/docs/tools.py`).
   - A hit on any image chunk returns all chunks of that image in order, capped.
   - A file-scoped search returns that file's passages first.
   - Test: the query "detail of the image" against a description chunk plus a values chunk returns the values.
4. **Search that really runs** (`app/agents/docs/agent.py` `search_first`, `app/core/llm.py`).
   - A run in code when the forced first call came back without the tool.
   - `require_parameters` with `tool_choice`.
   - Test: a fake model that ignores `tool_choice` still ends with `search_documents` called once and its result used.
5. **Routing and prompts:**
   - `HINTS` and the follow-up rule (`app/agents/docs/agent.py`, `app/orchestration/router.py`);
   - the orchestrator rule on file notes (orchestrator prompt);
   - the docs answer rule (`app/agents/docs/AGENTS.md`).
   - Tests: "what can you tell me about the screen shot" routes to docs; "you don't give me the numbers" after a docs answer routes to docs; a docs answer to "details of the image" with a reading holding `Part Number: KVR32N22S8/8` contains `KVR32N22S8/8`.
6. **Re-chunk existing uploads:** a script, in the style of `scripts/reindex.py`, that rebuilds image chunks from the stored readings without re-reading the images. It runs with the app stopped and changes only `kind="image"` rows.
7. **Measure:**
   - `bench_chat` (or a short scripted chat) with the three messages above, on deepseek-chat;
   - report calls per message and cost against 0.8.2.
8. **Docs:** `CHANGELOG.md` (0.8.3 fixes), `pyproject.toml` version and `uv.lock`, `ARCHITECTURE.md` (reading format, retrieval expansion, routing rule) and `.claude/rules/data.md`.

Out of scope: storing originals and re-reading at question time (C); the quality check unless approved.

## Evidence index

- Stored chunks: `langchain_pg_embedding` ids `94e0ded7-9373-4266-ba00-78c54fc15059:0` and `:1` in the local dev DB.
- Reading call: LangSmith run `01a1dfdb-7382…`, `google/gemini-2.5-flash`, 380 tokens in, 376 out. `deepseek/deepseek-chat` was tried first and returned a 404 ("No endpoints found that support image input"), so the fallback slot read it.
- Turn traces: see "The case". The turn-2 call's `invocation_params.tool_choice` names `search_documents`; the reply has no tool call.
- Retrieval and prompt-variant scripts: the session scratchpad (`retr.py`, `variants.py`). Real model calls: 8, total about $0.0037 on Gemini 2.5 Flash plus 4 free Nemotron calls.
