import httpx

from scripts.record_chat import SCENARIOS, main, parse_sse


def test_parse_sse_yields_data_events_split_by_blank_lines():
    lines = ['data: {"type": "a"}', "", 'data: {"type": "b", "x": 1}', ""]
    assert list(parse_sse(lines)) == [{"type": "a"}, {"type": "b", "x": 1}]


def test_parse_sse_skips_bad_json_and_non_data_lines():
    lines = [": comment", 'data: {"type": "a"}', "", "data: {oops", "", "event: x", "", 'data: {"type": "b"}', ""]
    assert list(parse_sse(lines)) == [{"type": "a"}, {"type": "b"}]


def test_scenarios_has_the_seven_names():
    assert list(SCENARIOS)[:7] == ["general", "sql", "chart", "research", "asked", "fallback", "vi"]
    assert SCENARIOS["general"]["message"] == "What is a P/E ratio? Answer in one sentence."
    assert SCENARIOS["vi"]["message"] == "5 công ty có vốn hoá lớn nhất là những công ty nào?"


def test_main_exits_2_naming_both_variables_when_missing(monkeypatch, capsys):
    monkeypatch.delenv("SMALLIE_USER", raising=False)
    monkeypatch.delenv("SMALLIE_PASSWORD", raising=False)
    assert main([]) == 2
    err = capsys.readouterr().err
    assert "SMALLIE_USER" in err and "SMALLIE_PASSWORD" in err


def test_main_exits_2_when_only_password_missing(monkeypatch, capsys):
    monkeypatch.setenv("SMALLIE_USER", "someone")
    monkeypatch.delenv("SMALLIE_PASSWORD", raising=False)
    assert main([]) == 2
    assert "SMALLIE_PASSWORD" in capsys.readouterr().err


def test_failed_login_exits_1_without_printing_credentials(monkeypatch, capsys):
    monkeypatch.setenv("SMALLIE_USER", "someone")
    monkeypatch.setenv("SMALLIE_PASSWORD", "hunter2hunter2")
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"detail": "Wrong username or password"}))
    assert main([], transport=transport) == 1
    out = capsys.readouterr()
    assert "hunter2hunter2" not in out.out + out.err
    assert "login" in (out.out + out.err).lower()


def test_records_a_scenario_and_deletes_the_conversation(monkeypatch, tmp_path):
    monkeypatch.setenv("SMALLIE_USER", "someone")
    monkeypatch.setenv("SMALLIE_PASSWORD", "hunter2hunter2")
    monkeypatch.chdir(tmp_path)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/api/login":
            return httpx.Response(200, json={})
        if request.url.path == "/api/chat/auto":
            body = 'data: {"type": "conversation", "id": "c1"}\n\ndata: {"type": "done"}\n\n'
            return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
        if request.url.path == "/api/conversations/c1/messages":
            return httpx.Response(200, json=[{"role": "user", "content": "hi"}])
        if request.url.path == "/api/conversations/c1/usage":
            return httpx.Response(200, json={"chat": 1})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404)

    assert main(["--only", "general"], transport=httpx.MockTransport(handler)) == 0
    import json

    data = json.loads((tmp_path / "frontend/src/lib/__fixtures__/general.json").read_text(encoding="utf-8"))
    assert list(data) == ["name", "message", "events", "history", "usage"]
    assert [e["type"] for e in data["events"]] == ["conversation", "done"]
    assert ("DELETE", "/api/conversations/c1") in seen


def _team_handler(seen, first_events, second_events=None):
    second_events = first_events if second_events is None else second_events
    runs = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append((request.method, path))
        if path == "/api/login":
            return httpx.Response(200, json={})
        if path.startswith("/api/chat/"):
            import json as _json

            runs.append(_json.loads(request.content))
            events = first_events if len(runs) == 1 else second_events
            body = "".join(f"data: {_json.dumps(e)}\n\n" for e in events)
            return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
        if path.endswith("/messages"):
            return httpx.Response(200, json=[])
        if path.endswith("/usage"):
            return httpx.Response(200, json={})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404)

    return handler, runs


def _login_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SMALLIE_USER", "someone")
    monkeypatch.setenv("SMALLIE_PASSWORD", "hunter2hunter2")
    monkeypatch.chdir(tmp_path)


def test_scenario_shape_and_team_scenarios():
    assert all(set(v) == {"message", "kind", "reply"} for v in SCENARIOS.values())
    assert all(v["kind"] == "auto" and v["reply"] is None for v in list(SCENARIOS.values())[:7])
    assert list(SCENARIOS)[7:] == ["team_stock", "team_chart", "team_askback"]
    assert SCENARIOS["team_stock"] == {
        "message": "How has NVIDIA's stock done this year, and what is the latest news about it?",
        "kind": "team",
        "reply": None,
    }
    assert SCENARIOS["team_chart"] == {
        "message": "Compare AAPL and MSFT closing prices over the last 3 months and chart them.",
        "kind": "team",
        "reply": None,
    }
    assert SCENARIOS["team_askback"] == {
        "message": "Should I put more money into tech stocks now?",
        "kind": "team",
        "reply": "I'm 30, moderate risk, investing for 10 years.",
    }


def test_team_scenario_posts_to_team_and_old_ones_to_auto(monkeypatch, tmp_path):
    _login_env(monkeypatch, tmp_path)
    ev = [{"type": "conversation", "id": "c1"}, {"type": "done"}]
    seen = []
    handler, _ = _team_handler(seen, ev)
    assert main(["--only", "team_stock,general"], transport=httpx.MockTransport(handler)) == 0
    posts = [p for m, p in seen if p.startswith("/api/chat/")]
    assert posts == ["/api/chat/team", "/api/chat/auto"]


def test_needs_human_with_reply_posts_reply_to_same_conversation(monkeypatch, tmp_path):
    import json

    _login_env(monkeypatch, tmp_path)
    first = [{"type": "conversation", "id": "c1"}, {"type": "needs_human", "question": "How old are you?"}]
    second = [{"type": "conversation", "id": "c1"}, {"type": "done"}]
    seen = []
    handler, runs = _team_handler(seen, first, second)
    assert main(["--only", "team_askback"], transport=httpx.MockTransport(handler)) == 0
    assert len(runs) == 2
    assert runs[1] == {"message": SCENARIOS["team_askback"]["reply"], "conversation_id": "c1"}
    data = json.loads((tmp_path / "frontend/src/lib/__fixtures__/team_askback.json").read_text(encoding="utf-8"))
    assert [e["type"] for e in data["events"]] == [
        "conversation", "needs_human", "user_reply", "conversation", "done",
    ]
    assert data["events"][2] == {"type": "user_reply", "text": SCENARIOS["team_askback"]["reply"]}
    assert seen.index(("GET", "/api/conversations/c1/messages")) > max(
        i for i, (m, p) in enumerate(seen) if p == "/api/chat/team"
    )
    assert ("DELETE", "/api/conversations/c1") in seen


def test_no_needs_human_means_no_second_post(monkeypatch, tmp_path):
    _login_env(monkeypatch, tmp_path)
    ev = [{"type": "conversation", "id": "c1"}, {"type": "done"}]
    seen = []
    handler, runs = _team_handler(seen, ev)
    assert main(["--only", "team_askback"], transport=httpx.MockTransport(handler)) == 0
    assert len(runs) == 1


def test_connection_error_at_login_exits_1_with_message(monkeypatch, capsys):
    monkeypatch.setenv("SMALLIE_USER", "someone")
    monkeypatch.setenv("SMALLIE_PASSWORD", "hunter2hunter2")

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert main([], transport=httpx.MockTransport(refuse)) == 1
    err = capsys.readouterr().err
    assert "running" in err.lower() and "Traceback" not in err
