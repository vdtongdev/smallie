"""Team plan: task models and the pure checks around them (no model calls)."""

from collections import Counter
from collections.abc import Collection, Sequence

from pydantic import BaseModel

SETTLED = ("done", "failed", "withdrew", "skipped")
DEAD = ("failed", "withdrew", "skipped")


class Task(BaseModel):
    id: str = ""
    agent: str
    goal: str
    depends_on: list[str] = []


class Plan(BaseModel):
    tasks: list[Task] = []


def _item(id: str, agent: str, goal: str, depends_on: list[str], origin: str) -> dict:
    return {
        "id": id,
        "agent": agent,
        "goal": goal,
        "depends_on": depends_on,
        "status": "pending",
        "origin": origin,
    }


def _acyclic(tasks: Sequence[dict]) -> bool:
    """Kahn's algorithm."""
    left = {x["id"]: set(x["depends_on"]) for x in tasks}
    while left:
        free = [i for i, deps in left.items() if not deps]
        if not free:
            return False
        for i in free:
            del left[i]
        for deps in left.values():
            deps.difference_update(free)
    return True


def validate_plan(plan: Plan, available: Sequence[str], max_tasks: int) -> list[dict] | None:
    """Workspace tasks, [] for an empty plan, None when the caller must fall back."""
    kept = [x for x in plan.tasks if x.agent in available]
    if plan.tasks and not kept:
        return None
    kept = kept[:max_tasks]
    # a missing id is its position; a duplicate id can't be told apart, so edges to it are dropped
    given = [x.id or f"t{i}" for i, x in enumerate(kept, 1)]
    counts = Counter(given)
    new_ids = {g: f"t{i}" for i, g in enumerate(given, 1) if counts[g] == 1}
    tasks = [
        _item(f"t{i}", x.agent, x.goal, [new_ids[d] for d in x.depends_on if d in new_ids], "plan")
        for i, x in enumerate(kept, 1)
    ]
    return tasks if _acyclic(tasks) else None


def fallback_tasks(routes: Sequence[str]) -> list[dict]:
    """One independent task per route; the empty goal means "answer the latest message"."""
    return [_item(f"t{i}", r, "", [], "fallback") for i, r in enumerate(routes, 1)]


def after_user_files(tasks: list[dict], file_routes: Collection[str]) -> list[dict]:
    """Tasks with no inputs wait for the tasks that read the user's files, so they see what the files hold.
    The waited-for ids are also kept in `files_first`: those inputs never make the task skip."""
    readers = [t["id"] for t in tasks if t["agent"] in file_routes]
    return [{**t, "depends_on": list(readers), "files_first": list(readers)}
            if readers and t["agent"] not in file_routes and not t["depends_on"] else dict(t)
            for t in tasks]


def ready(tasks: Sequence[dict]) -> list[dict]:
    status = {x["id"]: x["status"] for x in tasks}
    return [
        x
        for x in tasks
        if x["status"] == "pending" and all(status.get(d) in SETTLED for d in x["depends_on"])
    ]


def dead_inputs(task: dict, tasks: Sequence[dict]) -> tuple[list[str], bool]:
    """The dependencies that produced nothing, and whether the task has no live input left.
    Inputs in `files_first` are never counted: the task still runs when they are dead."""
    status = {x["id"]: x["status"] for x in tasks}
    dead = [d for d in task["depends_on"] if status.get(d) in DEAD]
    planned = [d for d in task["depends_on"] if d not in task.get("files_first", [])]
    return dead, bool(planned) and all(status.get(d) in DEAD for d in planned)


def add_follow_ups(
    tasks: list[dict], follow_ups: Sequence[Task], available: Sequence[str], max_tasks: int
) -> tuple[list[dict], list[dict], list[str]]:
    """Append valid follow-ups; returns (all tasks, added, goals dropped by the cap)."""
    ids = {x["id"] for x in tasks}
    valid = [x for x in follow_ups if x.agent in available and set(x.depends_on) <= ids]
    room = max(max_tasks - len(tasks), 0)
    added = [
        _item(f"t{len(tasks) + i}", x.agent, x.goal, list(x.depends_on), "follow_up")
        for i, x in enumerate(valid[:room], 1)
    ]
    return tasks + added, added, [x.goal for x in valid[room:]]
