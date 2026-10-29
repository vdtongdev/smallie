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
