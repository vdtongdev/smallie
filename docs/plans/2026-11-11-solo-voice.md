# Solo voice: agents say "I" in 1-on-1 chats

Branch `0.7.1/solo-voice`, worktree `.claude/worktrees/solo-voice`.

**Bug.** In solo (non-team) chats the orchestrator and specialists call themselves "we". Solo should speak as "I"; team mode keeps the team voice.

**Root cause (diagnosis).**
- `app/core/layers/SOUL.md:1` ("WHO WE ARE ... 'we' for the team's work") starts every system prompt (orchestrator, sql, chart, docs), whatever the mode.
- `app/orchestration/AGENTS.md:19` "Say \"we\" for the team's work." (orchestrator).
- `app/orchestration/BOOTSTRAP.md:1` "We don't know yet ... from us" (both modes, first turn).
- `make_call_note` (`app/agents/factory.py:143-153`) adds `TEAM_VOICE` in team mode only; solo gets no voice note. The mode reaches remote specialists through `runtime.context` (`app/agents/remote.py:129-138`).

**Approach.** Keep the system prompt mode-independent (existing `test_team_voice_leaves_system_prompt_unchanged` requires it); put the voice only in the per-call tail: `TEAM_VOICE` for team, new `SOLO_VOICE` for solo. Make the static texts voice-neutral. No new model calls.

## Task 1: keep the regression test
Agent: implementer
- `tests/test_solo_voice.py` is committed and fails on the base (3 failed, 2 passed). Keep it unchanged.

## Task 2: solo voice note and neutral static prompts
Agent: implementer
- Load the `writing-llm-document` skill before editing any prompt text.
- `app/core/prompts.py`: add `SOLO_VOICE` next to `TEAM_VOICE`: speak in the first person singular ("I"), never "we"/"us"/"our" for yourself, greetings and introductions included. No braces (check whether it goes through `.format`).
- `app/agents/factory.py` `make_call_note`: append `SOLO_VOICE` when `context.agent != TEAM_MODE` (a tail note, like `TEAM_VOICE`).
- `app/core/layers/SOUL.md`: describe smallie and its team without "we/us/our" and without fixing the voice; the voice comes from the tail note.
- `app/orchestration/AGENTS.md:19`: remove the "we" instruction (the tail note covers it).
- `app/orchestration/BOOTSTRAP.md`: reword voice-neutral (e.g. "You don't know yet what the user does or wants").
- Keep `LANGUAGE_RULE` placement; examples English or EN/VI pairs.
- If the edited Markdown changes the ARCHITECTURE.md description of voice notes, update it.

Acceptance: `uv run pytest tests/test_solo_voice.py -q` passes; full `uv run pytest` green (run `scripts\dev_db.ps1` first; DB tests skipped is not a pass; the two Missing-credentials tests need `.env` present).

## Task 2b: update tests of the removed behaviour (user-approved 2026-11-11)
Agent: implementer
- Six existing tests assume a solo call adds no voice note to the tail; the user approved updating them to expect `SOLO_VOICE` (team mode already expects `TEAM_VOICE` the same way): `tests/test_call_note.py::test_tail_is_the_last_message_of_every_call`, `::test_empty_tail_adds_no_message`, `tests/test_orchestrator.py::test_handoff_lets_the_specialist_reply_directly`, `::test_asking_one_specialist_first_becomes_a_handoff`, `::test_delegations_run_in_parallel_and_only_the_write_up_streams`, `::test_bootstrap_ends_the_tail_on_the_first_turn`.
- Change only what the solo voice note changes (the expected tail now includes `SOLO_VOICE`); keep every other assertion.
- Also refresh `.claude/rules/agents.md` / `team-mode.md` lines that say a voice note exists only in team mode.

## Task 3: verify
Agent: verifier
- Confirm the regression test fails at the base commit (before Task 2) and passes after; full suite green; diff matches this plan; no new model calls.
