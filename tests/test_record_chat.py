import httpx

from scripts.record_chat import SCENARIOS, main, parse_sse


def test_parse_sse_yields_data_events_split_by_blank_lines():
    lines = ['data: {"type": "a"}', "", 'data: {"type": "b", "x": 1}', ""]
    assert list(parse_sse(lines)) == [{"type": "a"}, {"type": "b", "x": 1}]


def test_parse_sse_skips_bad_json_and_non_data_lines():
    lines = [": comment", 'data: {"type": "a"}', "", "data: {oops", "", "event: x", "", 'data: {"type": "b"}', ""]
    assert list(parse_sse(lines)) == [{"type": "a"}, {"type": "b"}]


def test_scenarios_has_the_seven_names():
    assert list(SCENARIOS) == ["general", "sql", "chart", "research", "asked", "fallback", "vi"]
    assert SCENARIOS["general"] == "What is a P/E ratio? Answer in one sentence."
    assert SCENARIOS["vi"] == "5 công ty có vốn hoá lớn nhất là những công ty nào?"


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
