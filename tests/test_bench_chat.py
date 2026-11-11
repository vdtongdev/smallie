from scripts import bench_chat
from app.core.usage import UsageCounter


class FakeAssistant:
    """Reports two model calls the way a RemoteAgent does: into the run's UsageCounter."""

    class checkpointer:
        @staticmethod
        async def adelete_thread(thread_id):
            pass

    async def astream(self, input, *, config, **kwargs):
        for callback in config["callbacks"]:
            if isinstance(callback, UsageCounter):
                callback.add({"model_calls": 2})
        return
        yield


async def test_remote_model_calls_are_counted(monkeypatch):
    async def snapshot(*args):
        return {"user": "", "memories": "", "recent_chats": ""}

    monkeypatch.setattr(bench_chat, "build_snapshot", snapshot)
    result = await bench_chat.run_scenario(FakeAssistant(), None, "u", "generic", 1)
    assert result.error == "" and result.model_calls == 2


def test_choice_missing_required_lowers_recall():
    got = bench_chat.choice({"required": ["docs", "sql"], "optional": []}, ["docs"])
    assert got["recall"] < 1 and got["missing_required"] == ["sql"] and got["precision"] == 1


def test_choice_optional_agent_does_not_lower_precision():
    got = bench_chat.choice({"required": ["docs"], "optional": ["research"]}, ["docs", "research"])
    assert got["precision"] == 1 and got["recall"] == 1 and got["missing_required"] == []


def test_choice_unlisted_agent_lowers_precision():
    got = bench_chat.choice({"required": ["docs"], "optional": []}, ["docs", "chart"])
    assert got["precision"] == 0.5 and got["recall"] == 1


def transcript():
    return {"tasks": [{"id": "t1", "agent": "docs", "status": "done", "origin": "plan"},
                      {"id": "t2", "agent": "sql", "status": "withdrew", "origin": "plan"}],
            "results": {"t1": {"agent": "docs", "text": "x"}},
            "evidence": [{"task": "t1", "claim": "AAPL is worth 3.1 trillion", "source": "file", "as_of": ""}]}


def test_contribution_withdrawn_agent_is_not_live():
    assert bench_chat.contribution(transcript(), "anything")["sql"]["live"] is False


def test_contribution_claim_in_answer_is_used():
    got = bench_chat.contribution(transcript(), "Per the file, AAPL is worth 3.1 trillion.")
    assert got["docs"] == {"live": True, "used": True}
    assert bench_chat.contribution(transcript(), "nothing relevant")["docs"]["used"] is False


def test_every_team_scenario_has_expected():
    team = [n for n in bench_chat.SCENARIOS if n.startswith("team")]
    assert 12 <= len(team) <= 15
    for name in team:
        assert set(bench_chat.EXPECTED[name]) == {"required", "optional"}
    assert set(bench_chat.FIXTURES) <= set(bench_chat.SCENARIOS)


def test_follow_up_check():
    tasks = [{"agent": "sql", "origin": "follow_up"}, {"agent": "docs", "origin": "plan"}]
    assert bench_chat.has_follow_up({"tasks": tasks}, "sql") and not bench_chat.has_follow_up({"tasks": tasks}, "docs")


def test_planted_error_flagged_by_challenge_or_conflict():
    tasks = [{"id": "t1", "agent": "docs"}, {"id": "t2", "agent": "sql"}]
    challenge = {"history": [{"type": "review", "challenges": [{"to_task": "t1"}], "conflicts": []}], "tasks": tasks}
    conflict = {"history": [{"type": "review", "challenges": [], "conflicts": [{"routes": ["docs", "sql"]}]}], "tasks": tasks}
    clean = {"history": [{"type": "review", "challenges": [{"to_task": "t2"}], "conflicts": []}], "tasks": tasks}
    assert bench_chat.planted_flagged(challenge, "docs") and bench_chat.planted_flagged(conflict, "docs")
    assert not bench_chat.planted_flagged(clean, "docs")


def test_contribution_evidence_claim_in_answer_is_used():
    t = {"tasks": [{"id": "t1", "agent": "sql", "status": "done"}], "results": {"t1": {"text": "no links"}},
         "evidence": [{"task": "t1", "claim": "market cap 3.1T", "source": "db", "as_of": "2025"}]}
    assert bench_chat.contribution(t, "Its market cap 3.1T today.")["sql"]["used"] is True


def test_hit_rate():
    assert bench_chat.hit_rate(200, 50) == 25
    assert bench_chat.hit_rate(0, 0) is None
    assert bench_chat.hit_rate(100, None) is None


def test_overall_cache_counts_only_reporting_results():
    R = bench_chat.Result
    assert bench_chat.overall_cache([R("a", 1, input_tokens=200, cached_tokens=50),
                                     R("b", 1, input_tokens=100)]) == (300, 50, 25)
    assert bench_chat.overall_cache([R("b", 1, input_tokens=100)]) == (100, None, None)


def test_bootstrap_check():
    asked, plain = "Autumn is cool.\n\nWhat do you do for work?\n", "Here is the answer.\nHope it helps."
    assert bench_chat.bootstrap_ok(asked, plain)
    assert not bench_chat.bootstrap_ok(plain, plain)
    assert not bench_chat.bootstrap_ok(asked, asked)


def test_agent_phrases_match_the_prompts():
    from app.agents.chart.prompts import CHART_SYSTEM
    from app.agents.docs.prompts import DOCS_SYSTEM
    from app.agents.research.prompts import RESEARCHER_SYSTEM
    from app.agents.sql.prompts import SQL_SYSTEM
    from app.core.prompts import SOUL
    from app.orchestration.prompts import ORCHESTRATOR_SYSTEM

    prompts = {"orchestrator": ORCHESTRATOR_SYSTEM, "sql": SQL_SYSTEM, "docs": DOCS_SYSTEM,
               "chart": CHART_SYSTEM, "researcher": RESEARCHER_SYSTEM}
    phrases = {agent: phrase for phrase, agent in bench_chat.AGENT_PHRASES.items()}
    assert set(phrases) == set(prompts)
    for agent, phrase in phrases.items():
        assert [a for a, system in prompts.items() if phrase in system] == [agent]
        assert phrase not in SOUL


def test_only_the_bootstrap_scenario_onboards():
    assert bench_chat.onboards("bootstrap")
    assert not any(bench_chat.onboards(name) for name in ("generic", "team_vi", "bootstrap2", ""))


# ---- notes_followup: a correction in one chat is followed in the next ----

import uuid
from types import SimpleNamespace

from langchain_core.messages import AIMessage
from sqlalchemy import func, insert, select

from app.api import routes_chat
from app.core.db import get_engine, graph_nodes, users
from app.orchestration.orchestrator import MESSAGE_ROUTES
from tests.conftest import requires_db

NOTE = "- Write percentages with exactly one decimal place."


def test_notes_followup_is_a_scenario_that_does_not_onboard():
    assert "notes_followup" in bench_chat.SCENARIOS
    assert len(bench_chat.messages_of("notes_followup")) >= 2
    assert not bench_chat.onboards("notes_followup")


def test_followed_needs_every_percentage_with_one_decimal():
    assert bench_chat.followed_one_decimal("KO yields 3.1%, up from 2.9 %.")
    assert not bench_chat.followed_one_decimal("KO yields 3.14%.")
    assert not bench_chat.followed_one_decimal("KO yields 3.1% and 3%.")
    assert not bench_chat.followed_one_decimal("KO pays a dividend.")


class NotesAssistant(FakeAssistant):
    """Answers as the sql agent; writes percentages with one decimal only when its context carries a note for sql."""

    def __init__(self):
        self.contexts, self.text = [], ""
        self.name = next(n for n, route in MESSAGE_ROUTES.items() if route == "sql")

    def reply(self, context):
        self.contexts.append(context)
        self.text = "The yield is 3.1%." if context.agent_notes.get("sql") else "The yield is 3.14%."

    async def ainvoke(self, input, *, config, context, **kwargs):
        self.reply(context)
        return {"messages": [AIMessage(content=self.text, name=self.name)]}

    async def astream(self, input, *, config, context, **kwargs):
        self.reply(context)
        yield ((), "custom", {"type": "spoke", "text": self.text})

    async def aget_state(self, config):
        return SimpleNamespace(values={"messages": [AIMessage(content=self.text, name=self.name)]})


@requires_db
async def test_notes_followup_scripted_run(monkeypatch):
    saved, calls, seen = {}, [], {}

    async def snapshot(checkpointer, user_id, conversation_id):
        seen["user"] = user_id
        return {"user": "", "profile": "", "memories": "", "recent_chats": "", "agent_notes": dict(saved)}

    async def extractor(user_id, question, answer, callbacks, source="chat", conversation_id=None, agent=None):
        calls.append((user_id, agent))
        callbacks[0].add({"model_calls": 1, "input_tokens": 50, "output_tokens": 7})
        saved[agent] = NOTE
        with get_engine().begin() as conn:  # a row the user's deletion must take along
            conn.execute(insert(graph_nodes).values(id=str(uuid.uuid4()), user_id=user_id, kind="note", name="n", norm="sql:n"))
        return 0

    monkeypatch.setattr(bench_chat, "build_snapshot", snapshot)
    monkeypatch.setattr(bench_chat, "recall_notes", lambda user_id: dict(saved))
    monkeypatch.setattr(routes_chat, "extract_facts", extractor)
    other = await bench_chat.create_bench_user()
    with get_engine().begin() as conn:
        conn.execute(insert(graph_nodes).values(id=str(uuid.uuid4()), user_id=other, kind="note", name="keep", norm="sql:keep"))
    assistant = NotesAssistant()
    try:
        result = await bench_chat.run_notes_followup(assistant, None, 1)
        assert result.error == ""
        assert [route for _, route in calls] == ["sql"] and calls[0][0] == seen["user"] != other
        assert result.checks["extraction"]["model_calls"] == 1
        assert (result.checks["extraction"]["input_tokens"], result.checks["extraction"]["output_tokens"]) == (50, 7)
        assert result.checks["chat1"]["agent"] == "sql" and result.checks["chat2"]["agent"] == "sql"
        assert result.checks["notes_saved"] == {"sql": NOTE}
        assert result.checks["chat2"]["snapshot_note"] is True
        assert [bool(c.agent_notes) for c in assistant.contexts] == [False, False, True]  # chat 1's two turns, then chat 2
        assert result.checks["followed"] is True and result.answer == "The yield is 3.1%."
        assert not any(c.bootstrap for c in assistant.contexts)
        with get_engine().connect() as conn:
            assert conn.execute(select(func.count()).select_from(users).where(users.c.id == seen["user"])).scalar() == 0
            assert conn.execute(select(func.count()).select_from(graph_nodes).where(graph_nodes.c.user_id == seen["user"])).scalar() == 0
            assert conn.execute(select(func.count()).select_from(graph_nodes).where(graph_nodes.c.user_id == other)).scalar() == 1
    finally:
        bench_chat.delete_user_row(other)


@requires_db
async def test_notes_followup_not_followed_when_the_answer_ignores_the_note(monkeypatch):
    async def snapshot(checkpointer, user_id, conversation_id):
        return {"user": "", "profile": "", "memories": "", "recent_chats": "", "agent_notes": {}}

    async def extractor(*args, **kwargs):
        return 0

    monkeypatch.setattr(bench_chat, "build_snapshot", snapshot)
    monkeypatch.setattr(bench_chat, "recall_notes", lambda user_id: {})
    monkeypatch.setattr(routes_chat, "extract_facts", extractor)
    result = await bench_chat.run_notes_followup(NotesAssistant(), None, 1)
    assert result.checks["followed"] is False and result.checks["chat2"]["snapshot_note"] is False
