from app.agents.registry import AGENTS, MESSAGE_ROUTES, ROUTES, get


def test_routes_and_message_names_are_unique():
    assert len(set(ROUTES)) == len(ROUTES)
    assert "auto" not in ROUTES  # reserved for "let the router pick"
    names = [name for spec in AGENTS for name in spec.message_names]
    assert len(set(names)) == len(names) == len(MESSAGE_ROUTES)


def test_every_agent_has_a_description_and_labels():
    for spec in AGENTS:
        assert spec.description
        assert set(spec.labels) == {"en", "vi"}


def test_get():
    assert get("sql").route == "sql"
    assert get("nope") is None


def test_every_agent_can_review():
    assert all(spec.review for spec in AGENTS)


def test_team_settings_defaults():
    from app.core.config import Settings

    s = Settings(_env_file=None)
    assert (s.team_max_rounds, s.team_max_message_model_calls) == (4, 30)


def test_every_agent_has_sources():
    assert all(spec.sources for spec in AGENTS)
    assert get("sql").sources == get("chart").sources


def test_research_joins_with_others():
    assert get("research").joins == "with_others"
    assert all(spec.joins == "mentioned" for spec in AGENTS if spec.route != "research")
