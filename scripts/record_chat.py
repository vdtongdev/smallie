"""Records real chat streams as fixtures for the frontend's tests.

    SMALLIE_USER=... SMALLIE_PASSWORD=... uv run python -m scripts.record_chat
    uv run python -m scripts.record_chat --base http://localhost:8000 --only general,sql

Run from the repo root against a running app. Logs in, sends each scenario to
`POST /api/chat/auto` in a new chat, and writes `frontend/src/lib/__fixtures__/<name>.json`
(`name`, `message`, `events`, `history`, `usage`). Each chat is deleted afterwards.
Every scenario spends model quota on the account's own models.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterable, Iterator

import httpx

SCENARIOS = {
    "general": "What is a P/E ratio? Answer in one sentence.",
    "sql": "What are the 5 largest companies by market cap?",
    "chart": "Chart AAPL vs MSFT closing prices over the last 3 months.",
    "research": "Research the latest news about NVIDIA in two sentences, with sources.",
    "asked": "Compare NVIDIA's P/E ratio with what the news says about its valuation this week.",
    "fallback": "What does my contract say about termination?",
    "vi": "5 công ty có vốn hoá lớn nhất là những công ty nào?",
}

FIXTURES = Path("frontend/src/lib/__fixtures__")


def parse_sse(lines: Iterable[str]) -> Iterator[dict]:
    """Yield the JSON of each `data: ` event; events end at a blank line, bad JSON is skipped."""
    data: list[str] = []
    for line in [*lines, ""]:
        if line.startswith("data: "):
            data.append(line[6:])
        elif not line and data:
            try:
                yield json.loads("\n".join(data))
            except json.JSONDecodeError:
                pass
            data = []
        elif not line:
            data = []


def record(client: httpx.Client, name: str, message: str) -> int:
    conversation_id = None
    events = []
    try:
        with client.stream("POST", "/api/chat/auto", json={"message": message, "conversation_id": None}) as res:
            res.raise_for_status()
            for event in parse_sse(res.iter_lines()):
                events.append(event)
                if event.get("type") == "conversation":
                    conversation_id = event["id"]
        if conversation_id is None:
            raise RuntimeError("the stream sent no conversation event")
        history = client.get(f"/api/conversations/{conversation_id}/messages")
        history.raise_for_status()
        usage = client.get(f"/api/conversations/{conversation_id}/usage")
        usage.raise_for_status()
        FIXTURES.mkdir(parents=True, exist_ok=True)
        data = {"name": name, "message": message, "events": events, "history": history.json(), "usage": usage.json()}
        (FIXTURES / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return len(events)
    finally:
        if conversation_id is not None:
            client.delete(f"/api/conversations/{conversation_id}")


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--only", default="", help="comma-separated scenario names")
    args = parser.parse_args(argv)

    user, password = os.environ.get("SMALLIE_USER"), os.environ.get("SMALLIE_PASSWORD")
    if not user or not password:
        print("Set SMALLIE_USER and SMALLIE_PASSWORD in the environment.", file=sys.stderr)
        return 2
    names = [n for n in args.only.split(",") if n] or list(SCENARIOS)
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print(f"Unknown scenario: {', '.join(unknown)}. Choose from {', '.join(SCENARIOS)}.", file=sys.stderr)
        return 2

    failed = 0
    with httpx.Client(base_url=args.base, timeout=300, transport=transport) as client:
        login = client.post("/api/login", json={"username": user, "password": password})
        if login.status_code != 200:
            print(f"Login failed (HTTP {login.status_code}). Check SMALLIE_USER and SMALLIE_PASSWORD.", file=sys.stderr)
            return 1
        for name in names:
            started = time.monotonic()
            try:
                count = record(client, name, SCENARIOS[name])
            except Exception as err:  # one failed scenario must not stop the others
                failed += 1
                print(f"{name}: FAILED ({type(err).__name__}: {err})", file=sys.stderr)
                continue
            print(f"{name}: {count} events, {time.monotonic() - started:.1f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
