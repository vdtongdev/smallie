#!/usr/bin/env bash
# Install Postgres + pgvector inside WSL Ubuntu and load db/init/*.sql.
# The non-Docker equivalent of `docker compose up -d`. Safe to re-run.
# Uses the distro's Postgres version (16 on Ubuntu 24.04, 18 on 26.04).
#
# Run from WSL:  bash "/mnt/c/Users/<you>/projects/smallie/db/setup_wsl.sh"
set -euo pipefail

PORT=5433            # matches DATABASE_URL in .env
INIT_DIR="$(cd "$(dirname "$0")" && pwd)/init"

echo "==> Installing PostgreSQL"
sudo apt-get update -qq
sudo apt-get install -y -qq postgresql postgresql-common ca-certificates
PG_MAJOR="$(ls /usr/lib/postgresql | sort -V | tail -1)"
echo "    PostgreSQL $PG_MAJOR"

echo "==> Installing pgvector"
if ! sudo apt-get install -y -qq "postgresql-$PG_MAJOR-pgvector"; then
    echo "    not in Ubuntu's repositories, adding the official PostgreSQL (PGDG) repository"
    sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
    sudo apt-get install -y -qq "postgresql-$PG_MAJOR-pgvector"
fi

CONF_DIR="/etc/postgresql/$PG_MAJOR/main"
echo "==> Configuring port $PORT and access from Windows"
sudo sed -i -E "s/^#?port = [0-9]+/port = $PORT/" "$CONF_DIR/postgresql.conf"
sudo sed -i -E "s/^#?listen_addresses = .*/listen_addresses = '*'/" "$CONF_DIR/postgresql.conf"
if ! sudo grep -q "smallie-windows" "$CONF_DIR/pg_hba.conf"; then
    # WSL2's NAT network is only reachable from this PC; logins still need a password.
    echo "host all all 0.0.0.0/0 scram-sha-256  # smallie-windows" | sudo tee -a "$CONF_DIR/pg_hba.conf" >/dev/null
fi
sudo service postgresql restart

as_postgres() { sudo -u postgres psql -p "$PORT" -v ON_ERROR_STOP=1 -qtA "$@"; }

echo "==> Creating role and database 'smallie'"
# Superuser, like POSTGRES_USER in the Docker image (dev only).
if [ -z "$(as_postgres -c "SELECT 1 FROM pg_roles WHERE rolname = 'smallie'")" ]; then
    as_postgres -c "CREATE ROLE smallie LOGIN SUPERUSER PASSWORD 'smallie'"
fi
if [ -z "$(as_postgres -c "SELECT 1 FROM pg_database WHERE datname = 'smallie'")" ]; then
    as_postgres -c "CREATE DATABASE smallie OWNER smallie"
fi

echo "==> Loading db/init"
run_sql() { PGPASSWORD=smallie psql -h localhost -p "$PORT" -U smallie -d smallie -v ON_ERROR_STOP=1 -q "$@"; }
if [ -n "$(run_sql -tA -c "SELECT 1 FROM pg_roles WHERE rolname = 'agent_readonly'")" ]; then
    echo "    role 'agent_readonly' already exists, skipping"
else
    for f in "$INIT_DIR"/*.sql; do
        echo "    $(basename "$f")"
        run_sql -f "$f"
    done
fi

echo "==> Done. Postgres is listening on localhost:$PORT"
echo "    Next: load the stock data with 'uv run python -m scripts.load_stocks' (from Windows)"
