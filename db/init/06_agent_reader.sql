-- Role the agent servers use as DATABASE_URL: they only search (document passages, graph facts,
-- chat history). The main app grants it SELECT on those tables at every start (grant_reader in
-- app/core/db.py), since on a fresh database they appear only then. Accounts, conversations and
-- checkpoints stay out of reach. Stock data goes through agent_readonly (03, 04).
--
-- Idempotent; scripts/dev_db.ps1 runs it on every start. On Neon: run it once with a strong
-- password instead of 'reader', then restart the main app.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_reader') THEN
        CREATE ROLE agent_reader LOGIN PASSWORD 'reader';
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO agent_reader', current_database());
    GRANT USAGE ON SCHEMA public TO agent_reader;
END $$;

-- Defence in depth: read-only transactions and bounded runtime, like agent_readonly.
ALTER ROLE agent_reader SET default_transaction_read_only = on;
ALTER ROLE agent_reader SET statement_timeout = '10s';
