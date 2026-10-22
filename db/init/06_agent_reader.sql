-- Role the agent servers use as DATABASE_URL: they only search (document passages, graph facts,
-- chat history), so they get SELECT on those tables and nothing else. Accounts, conversations
-- and checkpoints stay out of reach. Stock data goes through agent_readonly (03, 04).
--
-- Idempotent; scripts/dev_db.ps1 runs it on every start. The app creates most of these tables at
-- its first start (after the init scripts), so a table that doesn't exist yet is skipped: run
-- dev_db.ps1 again after the app's first start on a fresh database (on Neon: run this file once
-- the main app has started, with a strong password instead of 'reader').
DO $$
DECLARE
    t text;
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_reader') THEN
        CREATE ROLE agent_reader LOGIN PASSWORD 'reader';
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO agent_reader', current_database());
    GRANT USAGE ON SCHEMA public TO agent_reader;
    FOREACH t IN ARRAY ARRAY['documents', 'graph_nodes', 'graph_edges', 'langchain_pg_collection',
                             'langchain_pg_embedding', 'store', 'store_vectors', 'store_migrations',
                             'vector_migrations'] LOOP
        IF to_regclass('public.' || t) IS NOT NULL THEN
            EXECUTE format('GRANT SELECT ON public.%I TO agent_reader', t);
        END IF;
    END LOOP;
END $$;

-- Defence in depth: read-only transactions and bounded runtime, like agent_readonly.
ALTER ROLE agent_reader SET default_transaction_read_only = on;
ALTER ROLE agent_reader SET statement_timeout = '10s';
