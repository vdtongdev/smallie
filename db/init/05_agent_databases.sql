-- One database per agent server, for the Agent Server's own tables (its run queue and
-- temporary threads; nothing of the app's). Idempotent: scripts/dev_db.ps1 runs it on every start,
-- since init scripts only run on a fresh volume.
--
-- They belong to agent_server, the login the agent containers hold (DATABASE_URI). It is no
-- superuser and can't open the app's database (06 removes the default CONNECT), so code running
-- inside a container can't reach accounts or chats. On Neon: use a strong password instead of 'server'.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_server') THEN
        CREATE ROLE agent_server LOGIN PASSWORD 'server';
    END IF;
    -- CREATE DATABASE ... OWNER needs the creator to be able to act as that role (Neon's owner
    -- isn't a superuser); the membership runs this way only, agent_server gains nothing.
    EXECUTE format('GRANT agent_server TO %I', current_user);
END $$;
SELECT 'CREATE DATABASE agent_docs OWNER agent_server' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agent_docs')\gexec
SELECT 'CREATE DATABASE agent_sql OWNER agent_server' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agent_sql')\gexec
SELECT 'CREATE DATABASE agent_chart OWNER agent_server' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agent_chart')\gexec
SELECT 'CREATE DATABASE agent_research OWNER agent_server' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agent_research')\gexec
