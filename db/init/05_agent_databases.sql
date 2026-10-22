-- One database per agent server, for the Agent Server's own tables (its run queue and
-- temporary threads; nothing of the app's). Idempotent: scripts/dev_db.ps1 runs it on every start,
-- since init scripts only run on a fresh volume.
SELECT 'CREATE DATABASE agent_docs' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agent_docs')\gexec
SELECT 'CREATE DATABASE agent_sql' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agent_sql')\gexec
SELECT 'CREATE DATABASE agent_chart' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agent_chart')\gexec
SELECT 'CREATE DATABASE agent_research' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agent_research')\gexec
