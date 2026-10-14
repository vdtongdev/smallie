"""Measures how responsive the assistant is on real models: one fresh thread per scenario.

    uv run python -m scripts.bench_chat                       # all scenarios, once
    uv run python -m scripts.bench_chat --runs 2 --out bench.json
    uv run python -m scripts.bench_chat --only generic,sql_obvious

Runs the assistant in-process exactly like `POST /api/chat/auto` (same graph, memories and
SSE events), as BENCH_USER (default: the admin, who owns the uploaded test documents). Reports
per scenario: time to the first visible event and first answer token, total time, model calls
and their latency. Every call spends free-tier quota (one scenario: 1-10 requests), so
scenarios are spaced out to stay under ~20 requests/min. Threads and memories it creates are
deleted afterwards. The app may keep running.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import uuid
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass, field

from langchain_core.callbacks import AsyncCallbackHandler

from app.agents.registry import AGENTS
from app.api.routes_chat import _events_from
from app.core.db import init_db
from app.core.memory import AgentContext, memory_namespace, open_persistence, recall
from app.orchestration.graph import build_assistant

SCENARIOS = {
    # name: (message, what it exercises)
    "generic": ("Write a four-line poem about autumn in Hanoi.", "general chat, no data"),
    "generic_vi": ("15% của 240 là bao nhiêu?", "general chat in Vietnamese"),
    "fresh_fact": ("Who won the most recent Ballon d'Or?", "needs a quick web search"),
    "sql_obvious": ("What is AAPL's dividend yield?", "ticker: routed without a model call"),
    "sql_nonobvious": ("Which tech company is the most valuable right now?", "one specialist, not obvious"),
    "docs_nonobvious": ("What does Naval say about getting rich?", "one specialist (files), not obvious"),
    "compound": ("Based on my resume, which of the five most valuable tech companies would suit me best?",
                 "needs files + database"),
    "compound_ticker": ("Does my resume fit a software engineering job at NVDA?", "a ticker plus the user's file"),
    "research": ("Research the latest news about solid-state batteries.", "research hint: researcher + write-up"),
    "research_topic": ("Tìm hiểu về rối loạn lưỡng cực", "research hint, background topic: Wikipedia plus web"),
    "chart_obvious": ("Chart AAPL vs MSFT closing prices over the last year", "chart hint + tickers: chart agent"),
    "chart_vi": ("Vẽ biểu đồ vốn hóa của 10 công ty công nghệ lớn nhất", "chart hint in Vietnamese"),
    # A list of messages runs on one thread; only the last one is measured.
    "chart_followup": (["What were NVDA's closing prices each day last week?", "plot that"],
                       "reuses the previous answer's table"),
    "chart_repeat": (["Chart AAPL vs MSFT closing prices over the last year",
                      "Chart AAPL vs MSFT closing prices over the last year"], "the same request again: reuse the table"),
    "chart_reworded": (["Chart AAPL vs MSFT closing prices over the last year",
                        "Show Apple's and Microsoft's closes for the past year as an area chart"],
                       "the same data asked differently: reuse the table"),
    "chart_compound": ("Compare the skills in my resume with the five largest tech companies, with a chart of their market caps",
                       "files + database + a chart"),
}
PAUSE_SECONDS = 20  # between scenarios, so bursts stay under the free tier's ~20 requests/min


@dataclass
class Call:
    agent: str
    seconds: float = 0.0
    first_token: float | None = None
    out_chars: int = 0  # text the call generated (tool-call arguments not counted)
    error: str = ""


@dataclass
class Result:
    scenario: str
    run: int
    route: list[str] = field(default_factory=list)  # agent events in order
    tools: list[str] = field(default_factory=list)
    first_event: float | None = None  # first agent badge, tool call or token after sending
    first_tool: float | None = None
    first_token: float | None = None
    total: float = 0.0
    calls: list[Call] = field(default_factory=list)
    answer: str = ""
    charts: list[str] = field(default_factory=list)  # charts drawn: "type: title (series x points)"
    error: str = ""


AGENT_PHRASES = {
    "You are smallie": "orchestrator",
    "You decide which assistant": "router",
    "You are a research assistant": "researcher",
    "You are a research writer": "writer",
    "You are a data analyst": "sql",
    "uploaded documents": "docs",
    "You are a chart assistant": "chart",
}


def agent_of(messages) -> str:
    system = messages[0].content if messages and isinstance(messages[0].content, str) else ""
    return next((name for phrase, name in AGENT_PHRASES.items() if phrase in system), "?")


class Timing(AsyncCallbackHandler):
    """Latency of every chat model call in the run, nested agents included."""

    def __init__(self):
        self.calls: dict = {}
        self.order: list[Call] = []

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        call = Call(agent=agent_of(messages[0]))
        self.calls[run_id] = (time.perf_counter(), call)
        self.order.append(call)

    async def on_llm_new_token(self, token, *, run_id, **kwargs):
        start, call = self.calls.get(run_id, (None, None))
        if call and call.first_token is None and token:
            call.first_token = round(time.perf_counter() - start, 2)
        if call:
            call.out_chars += len(token or "")

    async def on_llm_end(self, response, *, run_id, **kwargs):
        start, call = self.calls[run_id]
        call.seconds = round(time.perf_counter() - start, 2)

    async def on_llm_error(self, error, *, run_id, **kwargs):
        start, call = self.calls[run_id]
        call.seconds, call.error = round(time.perf_counter() - start, 2), type(error).__name__


async def run_scenario(assistant, store, user_id: str, name: str, run: int) -> Result:
    *before, message = messages_of(name)
    result, timing = Result(scenario=name, run=run), Timing()
    config = {"configurable": {"thread_id": f"bench-{uuid.uuid4()}"}, "callbacks": [timing]}
    try:
        for earlier in before:  # setup turns, not measured
            await assistant.ainvoke({"messages": [{"role": "user", "content": earlier}]},
                                    config={"configurable": config["configurable"]},
                                    context=AgentContext(user_id=user_id))
    except Exception as exc:  # noqa: BLE001
        result.error = f"setup failed: {type(exc).__name__}: {str(exc)[:200]}"
    start = time.perf_counter()
    elapsed = lambda: round(time.perf_counter() - start, 2)  # noqa: E731
    try:
        memories = await recall(store, user_id, message)
        stream = assistant.astream(
            {"messages": [{"role": "user", "content": message}]}, config=config,
            context=AgentContext(user_id=user_id, memories=memories),
            stream_mode=["messages", "updates"], subgraphs=True,
        )
        async for event in _events_from(stream):
            kind = event["type"]
            if kind in ("agent", "step", "tool_call", "token") and result.first_event is None:
                result.first_event = elapsed()
            if kind == "agent":
                result.route.append(event["name"])
            elif kind == "tool_call":
                result.tools.append(event["name"])
                result.first_tool = result.first_tool or elapsed()
            elif kind == "token":
                result.first_token = result.first_token or elapsed()
                result.answer += event["text"]
            elif kind == "tool_result" and event.get("charts"):
                result.charts += [f"{c['type']}: {c['title']} ({len(c['series'])}x{len(c['labels'])})"
                                  for c in event["charts"]]
    except Exception as exc:  # noqa: BLE001 - a failed scenario is a result too
        result.error = f"{type(exc).__name__}: {str(exc)[:200]}"
    result.total = elapsed()
    result.calls = timing.order
    await assistant.checkpointer.adelete_thread(config["configurable"]["thread_id"])
    return result


def messages_of(name: str) -> list[str]:
    message = SCENARIOS[name][0]
    return message if isinstance(message, list) else [message]


def fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def report(results: list[Result]) -> None:
    print(f"\n{'scenario':<16} {'route':<26} {'calls':>5} {'1st evt':>7} {'1st tok':>7} {'total':>6}  per-call s (agent)")
    for r in results:
        calls = " ".join(f"{c.seconds:.1f}{'!' if c.error else ''}({c.agent[:4]}{f' {c.out_chars}ch' if c.out_chars else ''})"
                         for c in r.calls)
        route = "->".join(r.route) or "?"
        print(f"{r.scenario:<16} {route[:26]:<26} {len(r.calls):>5} {fmt(r.first_event):>7} "
              f"{fmt(r.first_token):>7} {r.total:>6.1f}  {calls}{'  ERROR ' + r.error if r.error else ''}")
    seconds = [c.seconds for r in results for c in r.calls if not c.error]
    if seconds:
        print(f"\nmodel calls: {len(seconds)} ok, median {statistics.median(seconds):.1f}s, "
              f"max {max(seconds):.1f}s; errors: {sum(1 for r in results for c in r.calls if c.error)}")


async def main(args) -> None:
    init_db()
    user_id = os.environ.get("BENCH_USER", "team")
    names = args.only.split(",") if args.only else list(SCENARIOS)
    results: list[Result] = []
    async with AsyncExitStack() as stack:
        checkpointer, store = await open_persistence(stack)
        for spec in AGENTS:
            if spec.prepare:
                await spec.prepare(stack)
        assistant = build_assistant(checkpointer, store)
        before = {item.key for item in await store.asearch(memory_namespace(user_id), limit=10_000)}
        try:
            for run in range(1, args.runs + 1):
                for i, name in enumerate(names):
                    if results:
                        await asyncio.sleep(PAUSE_SECONDS)
                    print(f"[run {run}] {name}: {' / '.join(messages_of(name))}", flush=True)
                    results.append(await run_scenario(assistant, store, user_id, name, run))
                    r = results[-1]
                    print(f"  {'->'.join(r.route)}  {len(r.calls)} calls  {r.total:.1f}s  {r.error}", flush=True)
        finally:
            # Memories the agents saved while benchmarking are not the user's.
            for item in await store.asearch(memory_namespace(user_id), limit=10_000):
                if item.key not in before:
                    await store.adelete(item.namespace, item.key)
    report(results)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump([asdict(r) for r in results], f, ensure_ascii=False, indent=1)
        print(f"Saved to {args.out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--only", help="comma-separated scenario names: " + ", ".join(SCENARIOS))
    parser.add_argument("--out", help="write all results as JSON")
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.run(main(args), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(main(args))
