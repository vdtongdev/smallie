"""Measures how responsive the assistant is on real models: one fresh thread per scenario.

    uv run python -m scripts.bench_chat                       # all scenarios, once
    uv run python -m scripts.bench_chat --runs 2 --out bench.json
    uv run python -m scripts.bench_chat --only generic,sql_obvious
    uv run python -m scripts.bench_chat --only team_planted --ablate   # reruns without each agent that took part; a judge compares

Runs the assistant in-process exactly like `POST /api/chat/auto` (same graph, memories and
SSE events), as BENCH_USER (default: the admin, who owns the uploaded test documents). Reports
per scenario: time to the first visible event and first answer token, total time, model calls
(the agent servers' reported calls included) and the latency of the calls made in this process
(the orchestrator's; the agent servers' calls run elsewhere). Every call spends free-tier quota (one scenario: 1-10 requests), so
scenarios are spaced out to stay under ~20 requests/min. Threads and memories it creates are
deleted afterwards. The app may keep running.
On Windows with redirected output, set PYTHONIOENCODING=utf-8 (Vietnamese scenario names).
"""

import argparse
import asyncio
import json
import os
import random
import re
import statistics
import sys
import time
import uuid
from pathlib import Path
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass, field

from typing import Literal, Sequence
from unittest.mock import patch

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.registry import AGENTS
from app.api.routes_chat import _events_from
from app.core.config import get_settings
from app.core.db import init_db
from app.core.graph import recall_facts
from app.core.llm import get_chat_model
from app.core.memory import AgentContext, open_persistence
from app.core.usage import UsageCounter
from app.orchestration import team as team_module
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
    "team": ("What is AAPL's market cap, and how does it compare with MSFT's?", "team mode: delegate, critique, synthesize"),
    "team_research": ("What is the latest news about solid-state batteries?", "team mode: a recent-news question"),
    "team_compound": ("Is NVDA's market cap high for a company of its kind, and why do chipmakers get such valuations?",
                      "team mode: market data plus general knowledge, so the orchestrator authors"),
    "team_vi": ("Vốn hóa của AAPL so với MSFT thế nào?", "team mode: a Vietnamese stock question"),
    "team_ambiguous": ("Which one is better?", "team mode: a comparison with no subject, to try needs_human"),
    "team_conflict": ("What is AAPL's market cap according to the file bench_team_conflict.docx, and what does the stock database say?",
                      "team mode: the uploaded file and the database disagree"),
    "team_chart": ("Chart AAPL closing prices over the last month", "team mode: a chart request, a small gap should settle fast"),
    "team_none": ("Write a two-line poem about the sea.", "team mode: no specialist needed, the orchestrator answers"),
    "team_docs": ("Summarize my notes in bench_team_notes.docx.", "team mode: docs only"),
    "team_a_topic": ("Explain sleep hygiene using my notes in bench_team_notes.docx and what other sources say.",
                     "scenario A: a topic from the user's notes plus outside sources"),
    "team_b_report": ("Review bench_team_report.docx and tell me whether its claims are supported.",
                      "scenario B: claims from a file, checked externally"),
    "team_c_all": ("Find interesting patterns in the tech stocks' market caps, chart them, explain them with recent news "
                   "and relate them to my files.", "scenario C: sql, chart, research and docs"),
    "team_d_files": ("The files bench_team_d_a.docx and bench_team_d_b.docx disagree on a figure. Determine why.",
                     "scenario D: two files disagree on a figure"),
    "team_e_followup": ("Investigate bench_team_e_note.docx: is what it says about NVDA right?",
                        "scenario E: a document's numerical claim needs a database check (follow-up)"),
    "team_planted": ("What is MSFT's market cap according to bench_team_planted.docx, and is it right?",
                     "team mode: a wrong stock figure planted in an uploaded file"),
}
EXPECTED = {  # team scenario -> agents that must / may take part
    "team": {"required": ["sql"], "optional": ["research"]},
    "team_research": {"required": ["research"], "optional": []},
    "team_compound": {"required": ["sql"], "optional": ["research"]},
    "team_vi": {"required": ["sql"], "optional": ["research"]},
    "team_ambiguous": {"required": [], "optional": ["docs", "sql", "research"]},
    "team_conflict": {"required": ["docs", "sql"], "optional": ["research"]},
    "team_chart": {"required": ["sql", "chart"], "optional": []},
    "team_none": {"required": [], "optional": []},
    "team_docs": {"required": ["docs"], "optional": []},
    "team_a_topic": {"required": ["docs", "research"], "optional": ["sql", "chart"]},
    "team_b_report": {"required": ["docs", "research"], "optional": ["sql", "chart"]},
    "team_c_all": {"required": ["sql", "chart", "research", "docs"], "optional": []},
    "team_d_files": {"required": ["docs"], "optional": ["research", "sql"]},
    "team_e_followup": {"required": ["docs", "sql"], "optional": ["research"]},
    "team_planted": {"required": ["docs", "sql"], "optional": ["research"]},
}
CHECKS = {"team_planted": ("planted", "docs"), "team_e_followup": ("follow_up", "sql")}  # scenario -> (check, agent)
FIXTURES = {  # scenario -> [(source, uploaded name)]
    "team_conflict": [("team_conflict.md", "bench_team_conflict.docx")],
    "team_docs": [("team_notes.md", "bench_team_notes.docx")],
    "team_a_topic": [("team_notes.md", "bench_team_notes.docx")],
    "team_b_report": [("team_report.md", "bench_team_report.docx")],
    "team_d_files": [("team_d_a.md", "bench_team_d_a.docx"), ("team_d_b.md", "bench_team_d_b.docx")],
    "team_e_followup": [("team_e_note.md", "bench_team_e_note.docx")],
    "team_planted": [("team_planted.md", "bench_team_planted.docx")],
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
    model_calls: int = 0  # every call of the message, the agent servers' included
    calls: list[Call] = field(default_factory=list)  # timed calls made in this process
    answer: str = ""
    charts: list[str] = field(default_factory=list)  # charts drawn: "type: title (series x points)"
    error: str = ""
    team: dict = field(default_factory=dict)  # the answer's workspace (team scenarios)
    scores: dict = field(default_factory=dict)  # choice, contribution, check (team scenarios)
    ablation: dict = field(default_factory=dict)  # agent -> {"better", "why"} (--ablate)


class Comparison(BaseModel):
    better: Literal["A", "B", "tie"]
    why: str


def choice(expected: dict, took_part: Sequence[str]) -> dict:
    """Agents that took part against `required` + `optional`: an optional one is not an error, an unlisted one is."""
    got, listed = set(took_part), set(expected["required"]) | set(expected["optional"])
    missing = [a for a in expected["required"] if a not in got]
    return {"precision": len(got & listed) / len(got) if got else 1.0,
            "recall": 1 - len(missing) / len(expected["required"]) if expected["required"] else 1.0,
            "missing_required": missing}


def contribution(transcript: dict, answer: str) -> dict[str, dict]:
    """Per agent: `live` (a task of it finished, none withdrawn), `used` (an evidence claim of its tasks or a link its results cite is in the answer)."""
    out = {}
    for agent in {t["agent"] for t in transcript["tasks"]}:
        mine = [t for t in transcript["tasks"] if t["agent"] == agent]
        ids = {t["id"] for t in mine}
        claims = [e["claim"] for e in transcript.get("evidence", []) if e.get("task") in ids and e.get("claim")]
        links = [u for i in ids for u in re.findall(r"https?://[^\s)\]>]+", transcript["results"].get(i, {}).get("text", ""))]
        out[agent] = {"live": any(t["status"] in ("done", "failed") for t in mine),
                      "used": any(c in answer for c in claims + links)}
    return out


def has_follow_up(transcript: dict, agent: str) -> bool:
    return any(t["agent"] == agent and t.get("origin") == "follow_up" for t in transcript["tasks"])


def planted_flagged(transcript: dict, agent: str) -> bool:
    """A review challenged a task of `agent`, or named the agent in a conflict."""
    ids = {t["id"] for t in transcript["tasks"] if t["agent"] == agent}
    reviews = [e for e in transcript.get("history", []) if e["type"] == "review"]
    return any(c["to_task"] in ids for r in reviews for c in r["challenges"]) or any(
        agent in c["routes"] for r in reviews for c in r["conflicts"])


async def judge(question: str, full: str, without: str) -> dict:
    """One structured call on OPENROUTER_JUDGE_MODEL (the pattern of `ask_board`); the order is shuffled. better: with | without | tie."""
    settings = get_settings()
    swap = random.random() < 0.5
    first, second = (without, full) if swap else (full, without)
    model = settings.openrouter_judge_model
    board = get_chat_model(model or settings.openrouter_orchestrator_model or None,
                           **({"exact_model": True} if model else {})).with_structured_output(Comparison, method="function_calling")
    human = f"QUESTION:\n{question}\n\nANSWER A:\n{first}\n\nANSWER B:\n{second}"
    verdict = await board.ainvoke([SystemMessage("Compare two answers to the question. Say which is better (more correct, complete and "
                                                 "better supported): A, B or tie. Give the reason in one sentence."),
                                   HumanMessage(human)])
    better = verdict.better if verdict.better == "tie" else ("with" if (verdict.better == "A") != swap else "without")
    return {"better": better, "why": verdict.why}


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
    result, timing, usage = Result(scenario=name, run=run), Timing(), UsageCounter()
    config = {"configurable": {"thread_id": f"bench-{uuid.uuid4()}"}, "callbacks": [timing, usage]}
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
        memories = await asyncio.to_thread(recall_facts, user_id)
        stream = assistant.astream(
            {"messages": [{"role": "user", "content": message}]}, config=config,
            context=AgentContext(user_id=user_id, memories=memories, agent="team" if name.startswith("team") else "auto"),
            stream_mode=["messages", "updates", "custom"], subgraphs=True,
        )
        first_spoke, synthesized = None, False  # as the answer bubble: a lone speaker's text is the reply
        async for event in _events_from(stream):
            kind = event["type"]
            if kind == "spoke" and first_spoke is None:
                first_spoke = event["text"]
            elif kind == "synthesize":
                synthesized = True
            if kind in ("agent", "step", "tool_call", "token") and result.first_event is None:
                result.first_event = elapsed()
            if kind == "agent":
                result.route.append(event["name"])
            elif kind == "tool_call":
                result.tools.append(event["name"])
                result.first_tool = result.first_tool or elapsed()
            elif kind == "token":
                result.first_token = result.first_token or elapsed()
                if not event.get("agent"):  # tokens with an agent are a round's draft
                    result.answer += event["text"]
            elif kind == "tool_result" and event.get("charts"):
                result.charts += [f"{c['type']}: {c['title']} ({len(c['series'])}x{len(c['labels'])})"
                                  for c in event["charts"]]
        if not result.answer and not synthesized and first_spoke:
            result.answer = first_spoke
        if name in EXPECTED:  # the answer carries the workspace
            last = (await assistant.aget_state(config)).values["messages"][-1]
            result.team = last.additional_kwargs.get("team", {})
    except Exception as exc:  # noqa: BLE001 - a failed scenario is a result too
        result.error = f"{type(exc).__name__}: {str(exc)[:200]}"
    result.total = elapsed()
    result.calls, result.model_calls = timing.order, usage.model_calls
    await assistant.checkpointer.adelete_thread(config["configurable"]["thread_id"])
    return result


async def upload_fixture(store, user_id: str, source: str, filename: str) -> str:
    """Ingest a fixture like POST /api/documents does (a .docx made from the markdown text); returns the document id."""
    import docx

    from app.core.config import get_settings
    from app.core.db import documents, get_engine
    from app.features.documents.card import process_document

    doc_id = str(uuid.uuid4())
    path = get_settings().upload_dir / user_id / f"{doc_id}.docx"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = docx.Document()
    for line in (Path(__file__).parent / "bench_data" / source).read_text(encoding="utf-8").splitlines():
        document.add_paragraph(line)
    document.save(path)
    with get_engine().begin() as conn:
        conn.execute(documents.insert().values(id=doc_id, user_id=user_id, filename=filename, path=str(path), status="processing"))
    await process_document(store, doc_id, user_id, path, filename)
    return doc_id


def delete_fixture(user_id: str, doc_id: str) -> None:
    from sqlalchemy import delete, select

    from app.core.db import documents, get_engine
    from app.core.graph import drop_orphans
    from app.features.documents.ingest import delete_document_chunks

    with get_engine().begin() as conn:
        row = conn.execute(select(documents).where(documents.c.id == doc_id)).mappings().first()
        if row:
            delete_document_chunks(doc_id, row["chunk_count"])
            conn.execute(delete(documents).where(documents.c.id == doc_id))
    drop_orphans(user_id)
    if row:
        Path(row["path"]).unlink(missing_ok=True)


def score(name: str, result: Result) -> None:
    """Choice, contribution and the scenario's check, from the answer's workspace."""
    team = result.team or {"tasks": [], "results": {}}
    took_part = list(dict.fromkeys(t["agent"] for t in team["tasks"]))
    result.scores = {"choice": choice(EXPECTED[name], took_part), "contribution": contribution(team, result.answer)}
    if name in CHECKS:
        kind, agent = CHECKS[name]
        result.scores["check"] = {kind: planted_flagged(team, agent) if kind == "planted" else has_follow_up(team, agent)}


async def ablate(assistant, store, user_id: str, name: str, result: Result) -> None:
    """Rerun without each agent that took part (it is unavailable to the plan) and let the judge compare the answers."""
    real = team_module.available_agents
    for agent in dict.fromkeys(t["agent"] for t in result.team.get("tasks", [])):
        async def without(user, specs, agent=agent):
            return [(s, i) for s, i in await real(user, specs) if s.route != agent]

        with patch.object(team_module, "available_agents", without):
            rerun = await run_scenario(assistant, store, user_id, name, 1)
        result.ablation[agent] = ({"better": "error", "why": rerun.error} if rerun.error
                                  else await judge(messages_of(name)[-1], result.answer, rerun.answer))


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
        print(f"{r.scenario:<16} {route[:26]:<26} {r.model_calls:>5} {fmt(r.first_event):>7} "
              f"{fmt(r.first_token):>7} {r.total:>6.1f}  {calls}{'  ERROR ' + r.error if r.error else ''}")
    for r in results:
        if r.scores:
            c = r.scores["choice"]
            used = " ".join(f"{a}:{'live' if v['live'] else 'withdrew'}/{'used' if v['used'] else 'unused'}"
                            for a, v in r.scores["contribution"].items())
            print(f"{r.scenario:<16} precision {c['precision']:.2f} recall {c['recall']:.2f}"
                  f"{' MISSING ' + ','.join(c['missing_required']) if c['missing_required'] else ''}  {used}"
                  f"  {r.scores.get('check', '')}")
        for agent, v in r.ablation.items():
            print(f"{'':<16} without {agent}: {v['better']} - {v['why']}")
    print(f"\nmodel calls: {sum(r.model_calls for r in results)}")
    seconds = [c.seconds for r in results for c in r.calls if not c.error]
    if seconds:
        print(f"timed in this process: {len(seconds)} ok, median {statistics.median(seconds):.1f}s, "
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
        # Facts are only learned after a chat request (routes_chat), so benchmarking adds none.
        for run in range(1, args.runs + 1):
            for i, name in enumerate(names):
                if results:
                    await asyncio.sleep(PAUSE_SECONDS)
                print(f"[run {run}] {name}: {' / '.join(messages_of(name))}", flush=True)
                doc_ids = [await upload_fixture(store, user_id, *f) for f in FIXTURES.get(name, [])]
                try:
                    results.append(await run_scenario(assistant, store, user_id, name, run))
                    if name in EXPECTED and not results[-1].error:
                        score(name, results[-1])
                        if args.ablate:
                            await ablate(assistant, store, user_id, name, results[-1])
                finally:
                    for doc_id in doc_ids:
                        delete_fixture(user_id, doc_id)
                r = results[-1]
                print(f"  {'->'.join(r.route)}  {r.model_calls} calls  {r.total:.1f}s  {r.error}", flush=True)
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
    parser.add_argument("--ablate", action="store_true",
                        help="team scenarios: rerun without each agent that took part and have the judge compare (multiplies the calls)")
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.run(main(args), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(main(args))
