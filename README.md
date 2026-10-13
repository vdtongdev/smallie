# [smallie](https://smallie-976519788978.asia-southeast1.run.app/)

Two LangChain agents behind a FastAPI app with a small web UI. You type into one chat, and a router picks the right agent for each message:

- **Documents**: upload PDF/DOCX files and ask questions about them. Answers cite the file and page.
- **Database**: ask questions in plain language about a stock database (200 popular stocks loaded from Yahoo Finance with `yfinance`). The agent writes and runs read-only SQL.

The **Readme** button in the header explains how to use the app, with example questions.

Both agents understand and reply in **English and Vietnamese**. They remember each conversation and keep **long-term memories about the user across all chats**. They can also look back at your earlier chats. Memories are managed by the agents; the UI has no memory list (`GET/DELETE /api/memories` still exist).

## Prerequisites (install once)

| Tool | How |
|---|---|
| Python 3.12+ | https://www.python.org/downloads/ (tick "Add to PATH") |
| uv | `winget install --id=astral-sh.uv -e` |
| Postgres: **WSL2** (or Docker Desktop) | WSL: `wsl --install -d Ubuntu` in an admin PowerShell, then reboot. Docker Desktop needs Windows 10 build 19045 or newer. |
| OpenRouter API key | https://openrouter.ai/keys |

## Setup

**1. Database.** Pick one.

- **WSL (no Docker).** Open Ubuntu and run the script below. It installs Postgres (the distro's version: 16 on 24.04, 18 on 26.04) + pgvector, listens on port 5433, and loads `db/init/*.sql`. It's safe to re-run.
  ```bash
  bash "/mnt/c/Users/Asus ROG/projects/smallie/db/setup_wsl.sh"
  ```
  WSL stops Postgres when the distro shuts down. Before working on the app, start it from Windows with:
  `wsl -d Ubuntu -u root service postgresql start`
- **Docker.** Run `docker compose up -d`.

**2. App.** Run these on Windows:
```powershell
copy .env.example .env          # then put your OPENROUTER_API_KEY in .env
uv sync                         # creates .venv and installs dependencies
uv run pytest                   # tests that need Postgres are skipped if it isn't running
```

**3. Stock data.** Download the 200 stocks into the `market` schema. This takes about 2 minutes: one profile request per stock, plus 5 years of daily prices.
```powershell
uv run python -m scripts.load_stocks
```
After that, the app keeps the data fresh on its own. While it runs, a background job fetches the day's prices every weekday at `MARKET_UPDATE_TIME` (default 17:30 New York time, after the US close), which takes a few seconds. It refreshes company profiles and key stats once a week. If the app was off at the scheduled time, it catches up when it starts. Set `MARKET_UPDATE_TIME=` (empty) to turn the job off.

You can still run the script by hand. Later runs only fetch the last few days; add `--prices-only` to skip the profiles, or `--full` to re-download all history (for example after a stock split). The script and the background job share a database lock, so they never update at the same time. The ticker list is in `app/features/market/tickers.py`.

## Run

```powershell
uv run uvicorn app.api.main:app --reload --loop asyncio:SelectorEventLoop
```

Open http://localhost:8000. The API docs are at http://localhost:8000/docs.

> `--loop asyncio:SelectorEventLoop` is required on Windows. psycopg's async driver (used for chat memory) does not work on Windows' default Proactor event loop.

### Accounts

Everyone creates their own account on the login screen (name, username, password; up to `MAX_ACCOUNTS`, default 20). Each account has its own chats, documents and memories.

Set `APP_PASSWORD` and `SESSION_SECRET` in `.env` (see `.env.example`). `APP_PASSWORD` is the password of the `admin` account, which can use the app normally and also manage accounts (reset a password, delete an account with all its data) under **Accounts** in the header. The admin owns the data from before accounts existed. If you used the app without a password before, give those anonymous chats to the admin once with `uv run python -m scripts.claim_anonymous_data`.

### Letting others on your network use it

The app only runs while this PC is on and the server is running.

1. Set `APP_PASSWORD` first.
2. Start the server with `--host 0.0.0.0` (and without `--reload`):
   ```powershell
   uv run uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --loop asyncio:SelectorEventLoop
   ```
3. Allow the port once, in an admin PowerShell:
   ```powershell
   New-NetFirewallRule -DisplayName "smallie" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private,Domain
   ```
4. Others open `http://<this PC's IP>:8000`. Find the IP with `ipconfig`.

For access from anywhere without your PC, see [Deploying](#deploying-google-cloud-run--neon).

## Models and cost

These are set in `.env`; all calls go through OpenRouter.

- `OPENROUTER_CHAT_MODEL`: defaults to a free model with tool calling. Free models come and go, so see the list at https://openrouter.ai/models?supported_parameters=tools&max_price=0. `OPENROUTER_FALLBACK_MODEL` is used automatically when the main one fails or is rate-limited.
- `OPENROUTER_ROUTER_MODEL`: the model that decides which agent answers each message. It makes one short call per message, and the call is skipped when you have no documents (the database agent answers then). Empty means it uses `OPENROUTER_CHAT_MODEL`.
- `OPENROUTER_EMBED_MODEL`: defaults to `baai/bge-m3`, which is multilingual and costs about $0.01 per 1M tokens. A $1 top-up covers thousands of documents. The fully free option is `liquid/lfm-2.5-embedding-350m:free`, but the provider may keep and train on your data.
- **Free-tier limits:** about 20 requests/min, and 50 requests/day unless you have bought at least $10 of credits (then 1000/day). One question takes 2–5 model calls, plus 1 for routing.
- **Changing the embedding model or `EMBED_DIM`:** stop the app, then run `uv run python -m scripts.reindex`.

## How it works

```
app/
  core/           shared infrastructure: config, OpenRouter models (llm.py), app tables (db.py),
                  memory (checkpointer + store), auth/accounts, conversations, shared prompt rules
  agents/         one package per agent (agent.py with its SPEC, tools.py, prompts.py)
    registry.py   the list of agents the router can pick
    factory.py    create_agent + memory tools + memory-aware dynamic prompt + call limit
    docs/         search_documents tool over pgvector
    sql/          schema in prompt + read-only run_sql tool
  features/       data the agents use, with its API and background jobs
    documents/    upload routes, ingest.py (PDF/DOCX -> chunks -> pgvector)
    market/       tickers.py (the 200 stocks), loader.py (yfinance -> market schema), daily scheduler
  orchestration/  router graph: START -> router -> one agent (as a subgraph) -> END
  api/            FastAPI app and shared routes: chat (SSE streaming), conversations, memories, agents, admin
  static/         plain HTML/JS UI with an EN/VI toggle
db/init/          extension, read-only role, market schema (run on first `docker compose up`)
Dockerfile        container image for Cloud Run (.gcloudignore / .dockerignore keep secrets out)
scripts/          load_stocks.py (fill/refresh stock data), reindex.py (after changing the embedding model)
```

**Routing**
- Every message goes to `POST /api/chat/auto`. The router node first checks whether you have uploaded documents. If you have none, the message goes straight to the database agent. Otherwise one short model call sees your file names, the last few turns and your message, and answers `docs` or `sql`. Follow-ups stay with the previous agent unless the topic changes.
- Both agents are subgraphs on the same conversation thread, so one chat can switch between them and each sees the whole history. The UI shows which agent answered each turn.
- `POST /api/chat/docs` and `/api/chat/sql` skip the router and force one agent (API only).
- Adding an agent means one new package under `app/agents/` and one line in `app/agents/registry.py`; see "Adding an agent or workflow" in CLAUDE.md.

**Memory**
- *Within a chat:* every message is checkpointed in Postgres (`thread_id` = conversation id), so chats survive restarts.
- *Across chats:* the agent saves durable facts with `save_memory`. Before each turn, the facts most related to the user's message are added to the system prompt. Memories are stored under `("users", <user_id>, "memories")`.
- The checkpointer and the store share one psycopg connection pool that checks each connection before use. If Postgres restarts (for example when WSL shuts down), the app reconnects on its own and doesn't need a restart.
- The UI remembers the open chat, so reloading the page brings it back.

**SQL safety**
- The SQL agent connects as `agent_readonly`. That role has SELECT-only access to the `market` schema, read-only transactions, and a 10 s statement timeout. It cannot see the app's own tables.
- Results are capped at `SQL_MAX_ROWS`.

**Users and accounts**
- Everything (documents, vector chunks, chats, memories) is keyed by `user_id`.
- `app/core/auth.py::get_current_user()` reads the `user_id` from the signed session cookie set at login (401 if missing or the account was deleted). Accounts live in the `users` table (`app/core/accounts.py`).
- The `admin` account (password `APP_PASSWORD`) keeps the old shared id `team` and can list, reset and delete accounts (`app/api/routes_admin.py`). Deleting an account removes its chats, memories, document chunks and uploads.

## Deploying (Google Cloud Run + Neon)

The app runs as a container on **Cloud Run** (free tier, scales to zero). All data lives in a free **Neon** Postgres, so nothing is lost when the container stops. Uploaded files are only needed while they're being indexed.

**1. Neon (database), once**
1. Create a project at https://neon.tech, region AWS Singapore. Copy the **direct** connection string (not the `-pooler` one; the app uses advisory locks).
2. Enable pgvector and create the read-only role, using the statements in `db/init/01_extensions.sql` and `db/init/03_readonly_role.sql` (run them with `psql` or a short psycopg script as the owner):
   - Use a strong generated password instead of `'readonly'`; Neon rejects weak passwords.
   - Use your database's name in `GRANT CONNECT` (Neon's default is not `smallie`).
   - The schema grants come later from `load_stocks`, which re-applies `04_market_schema.sql`.
3. Load the stocks from your PC (Yahoo often blocks cloud IPs on a big first download):
   ```powershell
   $env:DATABASE_URL="<neon owner url>"; uv run python -m scripts.load_stocks --full
   ```

**2. Cloud Run (app)**
1. Install the gcloud CLI (`winget install Google.CloudSDK`), then run `gcloud init` and log in.
2. Create a project and make it the default, then link a billing account to it in the console (*Billing → Linked account*; the free tier still needs one). Set a budget alert at $1 in *Billing → Budgets & alerts*.
   ```powershell
   gcloud projects create <project-id> --name="smallie"
   gcloud config set project <project-id>
   ```
3. Enable the services once. New projects also have to let the default compute service account build from source, otherwise the deploy fails with `PERMISSION_DENIED ... missing required IAM permissions`:
   ```powershell
   gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
   $num = gcloud projects describe <project-id> --format="value(projectNumber)"
   gcloud projects add-iam-policy-binding <project-id> --role="roles/run.builder" --condition=None `
     --member="serviceAccount:$num-compute@developer.gserviceaccount.com"
   ```
4. Create `cloudrun.env.yaml` (gitignored). It's your `.env` as YAML, with the Neon URLs and `SESSION_HTTPS_ONLY: "true"`. All values must be quoted strings.
5. Deploy from the project folder (Cloud Build builds the `Dockerfile`; `.gcloudignore` keeps `.env` and `.venv` out):
   ```powershell
   gcloud run deploy smallie --source . --region asia-southeast1 --allow-unauthenticated `
     --env-vars-file cloudrun.env.yaml --memory 1Gi --cpu 1 --no-cpu-throttling `
     --min-instances 0 --max-instances 1 --timeout 900
   ```
   - `--no-cpu-throttling` keeps the CPU on after a response, so document indexing, chat-history indexing and the stock update (all background tasks) still run.
   - `--max-instances 1` caps the cost.
   - `--allow-unauthenticated` only opens the URL; the app still requires a login.
6. Share the printed `https://smallie-....run.app` URL. Run the same deploy command again after code or `cloudrun.env.yaml` changes; each deploy creates a new revision.

**Current deployment:** project `smallie-review`, region `asia-southeast1`, https://smallie-976519788978.asia-southeast1.run.app, database on Neon (Singapore, Postgres 18). App logs:
```powershell
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=smallie" --limit 50 --freshness 1h
```

**Limits:** the first request after ~15 min idle cold-starts the container (10–20 s). The OpenRouter free quota is shared by everyone using the deployment. The image is stored in Artifact Registry (0.5 GB free; delete old images if you redeploy often).

## Using your own database

Point `READONLY_DATABASE_URL` at a read-only role on your database and set `SCHEMAS` in `app/agents/sql/tools.py`. The agent reads the schema at startup and puts it in its prompt. Table and column comments help it a lot.

## Troubleshooting

- **Port 5433 already in use:** change `PORT` in `db/setup_wsl.sh` (or `ports` in `docker-compose.yml`) and update both URLs in `.env`.
- **Can't connect from Windows to Postgres in WSL:** make sure it's running (`wsl -d Ubuntu -u root service postgresql status`). Also check that `localhostForwarding` isn't set to false in `%UserProfile%\.wslconfig`.
- **Recreating the read-only role:** in WSL, run `PGPASSWORD=smallie psql -h localhost -p 5433 -U smallie -d smallie -c "DROP OWNED BY agent_readonly; DROP ROLE agent_readonly;"`, re-run the setup script, then `uv run python -m scripts.load_stocks` (it re-grants the `market` schema). With Docker, run `docker compose down -v; docker compose up -d`.
- **Chat errors with "the connection is closed", or old chats won't open:** Postgres was restarted while the app was running. Current versions reconnect automatically. If you still see this, make sure Postgres is running and restart the app.
- **Stock questions return nothing / "relation market.stocks does not exist":** run `uv run python -m scripts.load_stocks`, then restart the app (the SQL agent reads the schema at startup). If it reports tickers with no price data, those companies were delisted or renamed; replace them in `app/features/market/tickers.py`.
- **Cloud Run build fails:** `gcloud builds list --region asia-southeast1 --limit 1`, then `gcloud builds log <id> --region asia-southeast1`. `PERMISSION_DENIED` before the build starts means the `roles/run.builder` grant in *Deploying* is missing.
- **"No text found" on a PDF:** scanned or image-only PDFs need OCR, which isn't supported yet.
