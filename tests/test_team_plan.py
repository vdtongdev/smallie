from app.core.config import Settings
from app.orchestration.team_plan import (
    Plan,
    Task,
    add_follow_ups,
    dead_inputs,
    fallback_tasks,
    ready,
    validate_plan,
)


def t(id, agent, goal="g", deps=()):
    return Task(id=id, agent=agent, goal=goal, depends_on=list(deps))


def task(id, status="pending", deps=()):
    return {"id": id, "agent": "sql", "goal": "g", "depends_on": list(deps), "status": status}


def test_unknown_or_unavailable_agent_is_dropped_with_its_dependents_edges():
    plan = Plan(tasks=[t("t1", "docs"), t("t2", "ghost"), t("t3", "sql", deps=["t1", "t2"])])
    out = validate_plan(plan, ["docs", "sql"], 6)
    assert [x["agent"] for x in out] == ["docs", "sql"]
    assert out[1]["depends_on"] == [out[0]["id"]] == ["t1"]
    assert out[0]["status"] == "pending" and out[0]["origin"] == "plan"


def test_empty_plan_means_no_specialist():
    assert validate_plan(Plan(), ["sql"], 6) == []


def test_every_task_dropped_falls_back():
    assert validate_plan(Plan(tasks=[t("t1", "ghost"), t("t2", "ghost")]), ["sql"], 6) is None


def test_cycle_falls_back():
    plan = Plan(tasks=[t("t1", "sql", deps=["t2"]), t("t2", "docs", deps=["t1"])])
    assert validate_plan(plan, ["docs", "sql"], 6) is None


def test_self_dependency_falls_back():
    assert validate_plan(Plan(tasks=[t("t1", "sql", deps=["t1"])]), ["sql"], 6) is None


def test_plan_is_capped():
    plan = Plan(tasks=[t(f"t{i}", "sql", goal=f"g{i}") for i in range(1, 9)])
    out = validate_plan(plan, ["sql"], 6)
    assert [x["goal"] for x in out] == [f"g{i}" for i in range(1, 7)]


def test_same_agent_twice_is_allowed():
    plan = Plan(tasks=[t("t1", "sql", goal="a"), t("t2", "sql", goal="b")])
    assert [x["goal"] for x in validate_plan(plan, ["sql"], 6)] == ["a", "b"]


def test_duplicate_and_empty_ids_are_renumbered():
    plan = Plan(tasks=[t("t1", "sql"), t("t1", "docs"), t("", "sql")])
    out = validate_plan(plan, ["docs", "sql"], 6)
    assert [x["id"] for x in out] == ["t1", "t2", "t3"]


def test_plan_without_ids_parses():
    assert Plan.model_validate({"tasks": [{"agent": "sql", "goal": "g"}]}).tasks[0].id == ""


def test_missing_ids_take_their_position_so_dependencies_resolve():
    plan = Plan.model_validate(
        {"tasks": [{"agent": "docs", "goal": "a"}, {"agent": "sql", "goal": "b", "depends_on": ["t1"]}]}
    )
    out = validate_plan(plan, ["docs", "sql"], 6)
    assert [x["id"] for x in out] == ["t1", "t2"]
    assert out[1]["depends_on"] == ["t1"]


def test_follow_up_without_id_parses():
    assert Task.model_validate({"agent": "sql", "goal": "g"}).depends_on == []


def test_fallback_tasks_are_independent():
    out = fallback_tasks(["docs", "sql"])
    assert [(x["id"], x["agent"], x["goal"], x["origin"]) for x in out] == [
        ("t1", "docs", "", "fallback"),
        ("t2", "sql", "", "fallback"),
    ]
    assert all(x["depends_on"] == [] and x["status"] == "pending" for x in out)


def test_ready_waits_for_dependencies():
    tasks = [task("t1"), task("t2", deps=["t1"]), task("t3")]
    assert [x["id"] for x in ready(tasks)] == ["t1", "t3"]
    tasks[0]["status"] = "done"
    assert [x["id"] for x in ready(tasks)] == ["t2", "t3"]


def test_dead_inputs():
    tasks = [task("t1", "withdrew"), task("t2", "done"), task("t3", deps=["t1", "t2"])]
    assert dead_inputs(tasks[2], tasks) == (["t1"], False)
    tasks[1]["status"] = "failed"
    assert dead_inputs(tasks[2], tasks) == (["t1", "t2"], True)
    assert dead_inputs(tasks[0], tasks) == ([], False)


def test_follow_ups_get_fresh_ids_and_respect_the_cap():
    tasks = [task(f"t{i}", "done") for i in range(1, 5)]
    ups = [t("x", "sql", goal=f"f{i}") for i in range(3)]
    allt, added, dropped = add_follow_ups(tasks, ups, ["sql"], 6)
    assert [x["id"] for x in added] == ["t5", "t6"]
    assert all(x["origin"] == "follow_up" and x["status"] == "pending" for x in added)
    assert len(allt) == 6
    assert dropped == ["f2"]


def test_invalid_follow_ups_are_dropped():
    tasks = [task("t1", "done")]
    ups = [t("x", "ghost"), t("y", "sql", deps=["t9"]), t("z", "sql", goal="ok", deps=["t1"])]
    _, added, dropped = add_follow_ups(tasks, ups, ["sql"], 6)
    assert [x["goal"] for x in added] == ["ok"]
    assert added[0]["depends_on"] == ["t1"]
    assert dropped == []


def test_settings_defaults():
    s = Settings(_env_file=None)
    assert s.team_max_rounds == 2
    assert s.team_max_tasks == 6
