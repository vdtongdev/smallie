-- Role used by the SQL agent. Its read access to the "market" schema is granted
-- in 04_market_schema.sql.
CREATE ROLE agent_readonly LOGIN PASSWORD 'readonly';

GRANT CONNECT ON DATABASE smallie TO agent_readonly;

-- Defence in depth: read-only transactions, bounded runtime, market-only search path.
ALTER ROLE agent_readonly SET default_transaction_read_only = on;
ALTER ROLE agent_readonly SET statement_timeout = '10s';
ALTER ROLE agent_readonly SET search_path = market;

-- App tables (documents, memories, chat history) live in "public" and are never
-- granted to this role, so the agent cannot read them.
