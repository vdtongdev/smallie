"""Adding an agent server is config only: every registered agent needs its compose service, its
AGENT_URLS entry and its agent database (ARCHITECTURE.md section 10, "Adding an agent")."""

import json
import re
from pathlib import Path

import yaml

from app.agents.registry import ROUTES

ROOT = Path(__file__).resolve().parent.parent


def test_every_route_has_a_service_and_a_url():
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    urls = json.loads(re.search(r"^# AGENT_URLS=(\{.*\})$", example, re.MULTILINE)[1])
    databases = (ROOT / "db" / "init" / "05_agent_databases.sql").read_text(encoding="utf-8")
    for route in ROUTES:
        env = services[route]["environment"]
        assert env["AGENT_ROUTE"] == route
        assert env["DATABASE_URI"].endswith(f"/agent_{route}")  # a copied block keeps the old database
        assert "//agent_server:" in env["DATABASE_URI"]  # never the app database's owner
        assert route in urls
        assert f"agent_{route}" in databases
    # Two servers on one Redis database or port would share a run queue or clash.
    redis = [services[route]["environment"]["REDIS_URI"] for route in ROUTES]
    assert len(set(redis)) == len(redis)
    ports = [port for route in ROUTES for port in services[route]["ports"]]
    assert len(set(ports)) == len(ports)
