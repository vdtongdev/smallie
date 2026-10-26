# [smallie](https://smallie-976519788978.asia-southeast1.run.app/)

LangChain agents behind a FastAPI app with a small web UI. You type into one chat, and an **orchestrator** handles each message: it answers general questions itself (with a quick web search for fresh facts), hands a question to the right specialist, or asks several specialists and writes the combined answer. A **Team mode** switch next to the message box makes the agents answer independently from their own sources, and a judge compares the answers. There are four specialists today:

- **Documents**: upload PDF/DOCX files and ask questions about them. Answers cite the file and page.
- **Database**: ask in plain language about a stock database (200 popular stocks from Yahoo Finance via `yfinance`). The agent writes and runs read-only SQL.
- **Charts**: say "chart", "plot" or "vẽ biểu đồ" for an interactive line, bar, area, pie or scatter chart of the stock data, or of numbers from another answer ("plot that").
- **Research**: gathers facts from the web (Tavily), Wikipedia, arXiv, an optional external Postgres, the stock database and your files; the orchestrator writes them up with sources.

All agents understand and reply in **English and Vietnamese**, remember each conversation, and keep long-term memories about you across chats (a knowledge graph per user in Postgres). The **Readme** button in the app header explains how to use it, with example questions.

## Contents

- [Setup](#setup)
  - [Prerequisites](#prerequisites)
  - [Database](#database)
  - [App and stock data](#app-and-stock-data)
  - [Agent servers](#agent-servers)
- [Run](#run)
- [Accounts](#accounts)
  - [Letting others on your network use it](#letting-others-on-your-network-use-it)
- [Models and cost](#models-and-cost)
- [Architecture](#architecture)
- [Adding an agent](#adding-an-agent)
- [Deploying](#deploying)
- [Using your own database](#using-your-own-database)
- [Troubleshooting](#troubleshooting)

## Setup

### Prerequisites

| Tool | How |
|---|---|
| Python 3.12+ | https://www.python.org/downloads/ (tick "Add to PATH") |
| uv | `winget install --id=astral-sh.uv -e` |
| WSL2 + Docker Engine (runs Postgres) | `wsl --install -d Ubuntu` in an admin PowerShell, reboot, then see [Database](#database). No Docker Desktop needed. |
| OpenRouter API key | https://openrouter.ai/keys |

### Database

Postgres 18 + pgvector runs in Docker inside WSL Ubuntu. Once, from Windows:
```powershell
wsl -d Ubuntu -u root -- sh -c "apt-get update && apt-get install -y docker.io docker-compose-v2 && systemctl enable --now docker"
wsl -d Ubuntu -u root --cd "/mnt/c/Users/Asus ROG/projects/smallie" -- docker compose up -d db
```
The first start loads `db/init/*.sql`. The container listens on port 5433 and restarts whenever WSL starts (WSL needs `systemd=true` in `/etc/wsl.conf`, the default on new installs).

**Each time you work on the app**, start the database with:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev_db.ps1
```
WSL shuts down about 25 seconds after the last `wsl` command, even with Docker running, and the database goes with it (tests then get skipped or freeze). The script keeps WSL running until the PC shuts down (or `wsl --shutdown`), starts the container and waits until it's ready. Running it twice is harmless. Or work from a WSL terminal (WSL stays up while it is open): `docker compose up -d db`, `docker compose ps` (should say `healthy`). Keep running `uv run ...` in a Windows terminal: the `.venv` is a Windows one.

<details>
<summary>Moving from the old WSL-installed Postgres (<code>db/setup_wsl.sh</code>, legacy)</summary>

Dump it, stop it, restore into the container:
```powershell
wsl -d Ubuntu -u root -- sh -c "PGPASSWORD=smallie pg_dump -h localhost -p 5433 -U smallie -Fc smallie -f /root/smallie.dump && systemctl disable --now postgresql"
wsl -d Ubuntu -u root --cd "/mnt/c/Users/Asus ROG/projects/smallie" -- docker compose up -d db
wsl -d Ubuntu -u root -- docker cp /root/smallie.dump smallie-db:/tmp/smallie.dump
wsl -d Ubuntu -u root -- docker exec smallie-db psql -U smallie -d postgres -c "DROP DATABASE smallie" -c "CREATE DATABASE smallie OWNER smallie" -c "GRANT CONNECT ON DATABASE smallie TO agent_readonly"
wsl -d Ubuntu -u root -- docker exec smallie-db pg_restore -U smallie -d smallie --exit-on-error /tmp/smallie.dump
```
The old data stays in `/var/lib/postgresql` inside WSL until you remove the `postgresql` package.
</details>

### App and stock data

```powershell
copy .env.example .env          # then put your OPENROUTER_API_KEY in .env
uv sync                         # creates .venv and installs dependencies
uv run pytest                   # tests that need Postgres are skipped if it isn't running
uv run python -m scripts.load_stocks   # 200 stocks into the market schema, about 2 minutes
```
After that the app keeps the data fresh itself: a background job fetches the day's prices every weekday at `MARKET_UPDATE_TIME` (default 17:30 New York time), refreshes profiles weekly, and catches up at startup if the app was off. Set `MARKET_UPDATE_TIME=` (empty) to turn it off. You can still run the script by hand: later runs fetch only the last few days; `--prices-only` skips the profiles, `--full` re-downloads all history (e.g. after a stock split). The ticker list is in `app/features/market/tickers.py`.

### Agent servers

The specialists (docs, sql, research, chart) each run as their own LangGraph Agent Server in Docker, next to the database. The main app calls them over HTTP and won't start without `AGENT_URLS`.
1. In `.env`, set `LANGSMITH_API_KEY` (the Agent Server's free "lite" licence needs it), a `SERVICE_TOKEN` (any long random string), and `AGENT_URLS` (copy the local line from `.env.example`).
2. **Turn off Cloudflare WARP and other VPN clients (e.g. SoftEther) first.** They corrupt downloads inside WSL ("bad record MAC"), and the servers' calls to OpenRouter use the same network.
3. Build and start the stack (first build downloads about 1 GB; run it again after changing agent code):
   ```powershell
   wsl -d Ubuntu -u root --cd "/mnt/c/Users/Asus ROG/projects/smallie" -- docker compose up -d --build
   wsl -d Ubuntu -u root --cd "/mnt/c/Users/Asus ROG/projects/smallie" -- docker compose ps   # all agents "(healthy)"
   ```

## Run

```powershell
uv run uvicorn app.api.main:app --reload --loop asyncio:SelectorEventLoop
```

Open http://localhost:8000 (API docs at http://localhost:8000/docs). `--loop asyncio:SelectorEventLoop` is required on Windows: psycopg's async driver does not work on the default Proactor event loop.

## Accounts

Everyone creates their own account on the login screen (name, username, password; up to `MAX_ACCOUNTS`, default 20). Each account has its own chats, documents and memories.

Set `APP_PASSWORD` and `SESSION_SECRET` in `.env` (see `.env.example`). `APP_PASSWORD` is the password of the `admin` account, which also manages accounts (reset a password, delete an account with all its data) under **Accounts** in the header. The admin owns the data from before accounts existed. To give earlier anonymous chats to the admin, run once `uv run python -m scripts.claim_anonymous_data`.

### Letting others on your network use it

Set `APP_PASSWORD` first, then start the server on all interfaces (without `--reload`):
```powershell
uv run uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --loop asyncio:SelectorEventLoop
```
Allow the port once, in an admin PowerShell, then others open `http://<this PC's IP>:8000` (`ipconfig` shows the IP):
```powershell
New-NetFirewallRule -DisplayName "smallie" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private,Domain
```
It only runs while this PC is on. For access from anywhere, see [Deploying](#deploying).

## Models and cost

Set in `.env`; all calls go through OpenRouter.

- `OPENROUTER_CHAT_MODEL`: defaults to a free model with tool calling (list: https://openrouter.ai/models?supported_parameters=tools&max_price=0). `OPENROUTER_FALLBACK_MODEL` (comma-separated, tried in order) kicks in when the main one fails or is rate-limited; pick models that write good Vietnamese. With a paid model, `OPENROUTER_QUANTIZATIONS=bf16,fp8` keeps OpenRouter off the cheapest fp4 providers, which slip more.
- `OPENROUTER_ORCHESTRATOR_MODEL`: the orchestrator's model; empty means `OPENROUTER_CHAT_MODEL`. The orchestrator is skipped when a message clearly points to one agent. `ORCHESTRATOR_MAX_MODEL_CALLS` (4) caps its calls per message.
- `TEAM_MAX_ROUNDS` (4), `TEAM_MAX_MESSAGE_MODEL_CALLS` (30) and `OPENROUTER_JUDGE_MODEL` (the model that compares the answers in team mode; empty means the orchestrator's model): team mode takes each participant's calls + 1 judge call per round + 1 write-up. Measured: 6 calls for a stock question, 9 with a planted conflict.
- `TAVILY_API_KEY`: web search (free 1,000 credits/month at https://app.tavily.com). Without it, research uses the other sources. `MCP_SERVERS` adds remote MCP servers and `EXTERNAL_DATABASE_URL` lets the researcher read another Postgres (see `.env.example`).
- `LANGSMITH_TRACING` + `LANGSMITH_API_KEY`: records every model call, tool call and routing step as a trace. Off by default. Traces contain full prompts (users' messages, file passages), so enable it in production only if you accept that.
- `OPENROUTER_EMBED_MODEL`: defaults to `baai/bge-m3` (multilingual, about $0.01 per 1M tokens). The free `liquid/lfm-2.5-embedding-350m:free` may keep and train on your data. After changing it or `EMBED_DIM`, stop the app and run `uv run python -m scripts.reindex`.
- **Free-tier limits:** about 20 requests/min, and 50/day unless you bought at least $10 of credits (then 1000/day). A general question takes 1 model call, plus 1 after the reply to learn facts about you. An upload takes 1. A database or documents question takes 2–5 (+1 if handed over); a combined one 1 + each agent's calls + 1; research up to `RESEARCH_MAX_MODEL_CALLS` (8) + 1. `uv run python -m scripts.bench_chat` measures calls and latency on your models.

## Architecture

A router, an orchestrator and four specialists that each run as their own agent server. [ARCHITECTURE.md](ARCHITECTURE.md) has the details: [the router](ARCHITECTURE.md#3-router), [the orchestrator](ARCHITECTURE.md#4-orchestrator), [memory](ARCHITECTURE.md#8-memory), [remote agents](ARCHITECTURE.md#10-remote-agents-040), [team mode](ARCHITECTURE.md#11-agent-team-050) and [deployment](ARCHITECTURE.md#13-deployment). Developer notes for coding agents are in [CLAUDE.md](CLAUDE.md).

## Adding an agent

An agent is a package under `app/agents/<name>/` (`tools.py`, `prompts.py`, `agent.py` with its `SPEC`). Add the `SPEC` to `AGENTS` in `app/agents/registry.py`, give it a service block in `docker-compose.yml`, and the router, orchestrator, API and UI pick it up. If it needs its own data, add a feature under `app/features/<name>/`. The full checklist is in `CLAUDE.md` ("Adding an agent or workflow").

## Deploying

The app runs on Google Cloud Run (project `smallie-review`, region `asia-southeast1`) with its data in Neon Postgres, and each specialist as its own private Cloud Run service. Step-by-step instructions are in [DEPLOYING.md](DEPLOYING.md).

## Using your own database

Point `READONLY_DATABASE_URL` at a read-only role on your database and set `SCHEMAS` in `app/agents/sql/tools.py`. The agent reads the schema at startup and puts it in its prompt; table and column comments help it a lot.

## Troubleshooting

- **Port 5433 already in use:** the old WSL Postgres may still be running (`wsl -d Ubuntu -u root -- systemctl disable --now postgresql`). Otherwise change `ports` in `docker-compose.yml` and both URLs in `.env`.
- **Can't connect from Windows to Postgres:** check the container is up (`wsl -d Ubuntu -u root -- docker ps`) and that `localhostForwarding` isn't false in `%UserProfile%\.wslconfig`.
- **Recreating the database from scratch:** `docker compose down -v` then `docker compose up -d db` (both via `wsl -d Ubuntu -u root --cd "<project path in /mnt/c>" --`; `-v` deletes all local data), then `uv run python -m scripts.load_stocks --full`.
- **Chat errors with "the connection is closed", or old chats won't open:** Postgres restarted while the app ran. The app reconnects on its own; if not, make sure Postgres is running and restart the app.
- **Stock questions return nothing / "relation market.stocks does not exist":** run `uv run python -m scripts.load_stocks`, then restart the app (the SQL agent reads the schema at startup). Tickers reported without price data were delisted or renamed; replace them in `app/features/market/tickers.py`.
- **Cloud Run build fails:** `gcloud builds list --region asia-southeast1 --limit 1`, then `gcloud builds log <id> --region asia-southeast1`. `PERMISSION_DENIED` before the build starts means the `roles/run.builder` grant is missing; see [DEPLOYING.md](DEPLOYING.md).
- **"No text found" on a PDF:** scanned or image-only PDFs need OCR, which isn't supported yet.
