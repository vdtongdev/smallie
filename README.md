# [smallie](https://smallie-976519788978.asia-southeast1.run.app/)

LangChain agents behind a FastAPI app with a small web UI. You type into one chat, and an **orchestrator** handles each message. It answers general questions itself (explanations, maths, writing, small talk, with a quick web search for fresh facts). It hands a question to the right specialist, or asks several specialists and writes the combined answer. When nothing it has can answer, it says so. There are four specialists today, and the code is laid out so more can be added (see "Adding an agent" below):

- **Documents**: upload PDF/DOCX files and ask questions about them. Answers cite the file and page.
- **Database**: ask questions in plain language about a stock database (200 popular stocks loaded from Yahoo Finance with `yfinance`). The agent writes and runs read-only SQL.
- **Charts**: say "chart", "plot" or "vẽ biểu đồ" to get an interactive line, bar, area, pie or scatter chart of the stock data, or of numbers from another answer ("plot that"). The data goes from the database straight to the chart (Chart.js in the browser); the model only sees a short summary, so charts of hundreds of points stay fast.
- **Research**: a researcher gathers facts from the web (Tavily), Wikipedia, arXiv, an optional external Postgres, the stock database and your files. The orchestrator then turns those findings into a summary or a structured report with sources. External tools come in through MCP (`langchain-mcp-adapters`), and the researcher loads "skills" (instruction files) on demand.

The **Readme** button in the header explains how to use the app, with example questions.

All agents understand and reply in **English and Vietnamese**. They remember each conversation and keep **long-term memories about the user across all chats**. They can also look back at your earlier chats. Memories live in a **knowledge graph per user** (plain Postgres tables): facts about you, learned automatically after replies, plus a node per uploaded file linked to the people and companies it is about, so a question about one of them searches the right file. The UI has no memory list (`GET/DELETE /api/memories` still exist).

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
- **Docker.** Run `docker compose up -d`. This is the alternative to WSL; the current local setup uses WSL, and Docker isn't needed for anything else locally (the Cloud Run image is built by Cloud Build).

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

- `OPENROUTER_CHAT_MODEL`: defaults to a free model with tool calling. Free models come and go, so see the list at https://openrouter.ai/models?supported_parameters=tools&max_price=0. `OPENROUTER_FALLBACK_MODEL` (comma-separated, tried in order) is used automatically when the main one fails or is rate-limited; pick models that write good Vietnamese, since weaker ones slip Chinese, Korean or Thai words into Vietnamese answers. With a paid model, `OPENROUTER_QUANTIZATIONS=bf16,fp8` keeps OpenRouter off the cheapest fp4 providers, which slip more.
- `OPENROUTER_ORCHESTRATOR_MODEL`: the model of the orchestrator. It is skipped when the message clearly points to one agent (a ticker, "stock", a file name, "research", "latest news"…). Empty means it uses `OPENROUTER_CHAT_MODEL`. `ORCHESTRATOR_MAX_MODEL_CALLS` (4) caps its own calls per message.
- `TAVILY_API_KEY`: web search for the research agent (free 1,000 credits/month at https://app.tavily.com). Without it, research uses the other sources. `MCP_SERVERS` adds more remote MCP servers, and `EXTERNAL_DATABASE_URL` lets the researcher read another Postgres (see `.env.example`).
- `OPENROUTER_EMBED_MODEL`: defaults to `baai/bge-m3`, which is multilingual and costs about $0.01 per 1M tokens. A $1 top-up covers thousands of documents. The fully free option is `liquid/lfm-2.5-embedding-350m:free`, but the provider may keep and train on your data.
- **Free-tier limits:** about 20 requests/min, and 50 requests/day unless you have bought at least $10 of credits (then 1000/day). A general question takes 1 model call, plus 1 after the reply to learn facts about you. Each uploaded file takes 1 (its document card). A database or documents question takes 2–5, plus 1 when the orchestrator has to hand it over. A question that combines agents takes 1 + each agent's calls + 1 to write the answer. A research question takes up to `RESEARCH_MAX_MODEL_CALLS` (8) for the researcher plus 1 for the write-up. `uv run python -m scripts.bench_chat` measures the calls and latency of typical questions on your models.
- **Changing the embedding model or `EMBED_DIM`:** stop the app, then run `uv run python -m scripts.reindex` (it also re-embeds the knowledge graph's names).

## How it works

```
app/
  core/           shared infrastructure: config, OpenRouter models (llm.py), app tables (db.py),
                  memory (checkpointer + store), knowledge graph (graph.py), auth/accounts,
                  conversations, shared prompt rules
  agents/         one package per agent (agent.py with its SPEC, tools.py, prompts.py)
    registry.py   the list of agents the router can pick
    factory.py    create_agent + memory tools + memory-aware dynamic prompt + call limit
    docs/         search_documents tool over pgvector
    sql/          schema in prompt + read-only run_sql tool
  features/       data the agents use, with its API and background jobs
    documents/    upload routes, ingest.py (PDF/DOCX -> chunks -> pgvector)
    market/       tickers.py (the 200 stocks), loader.py (yfinance -> market schema), daily scheduler
  orchestration/  START -> router -> one agent, or the orchestrator (answers, hands off, or asks agents) -> END
    formats/      answer formats the orchestrator follows when it writes up results (summary, list, report...)
  api/            FastAPI app and shared routes: chat (SSE streaming), conversations, memories, agents, admin
  static/         plain HTML/JS UI with an EN/VI toggle
db/init/          extension, read-only role, market schema (loaded by db/setup_wsl.sh or on first `docker compose up`)
Dockerfile        container image for Cloud Run (.gcloudignore / .dockerignore keep secrets out)
scripts/          load_stocks.py (fill/refresh stock data), reindex.py (after changing the embedding model),
                  bench_chat.py (latency and model calls per scenario)
```

**Routing**
- Every message goes to `POST /api/chat/auto`. If it clearly points to one agent (e.g. "stock", a known ticker, one of your file names, "research") and doesn't also mention another agent's data ("my resume" or a word of one of your file names next to a ticker), the router (`app/orchestration/router.py`) sends it there without a model call. Everything else goes to the orchestrator (`app/orchestration/orchestrator.py`).
- The orchestrator knows each agent's description, which agents have something for you (no documents uploaded: no documents agent), your file names, and who answered last. It answers itself, **hands off** to one agent (which then replies directly with the whole chat), or **asks** agents with self-contained questions (several in parallel) and writes the answer from their results, with citations. Small free models rarely choose a handoff themselves, so asking a single agent in the first step is turned into a handoff, unless the message also mentions another agent's data. Stock figures and anything in your files always come from those agents, so a question like "which of the most valuable tech companies fits my resume?" asks both and gets one combined answer.
- Only the final answer is saved to the chat; the agents it asked show in the UI as tool calls.
- Agents are subgraphs on the same conversation thread, so one chat can switch between them and each sees the whole history. The UI shows which agent answered each turn; badge labels (EN/VI) come from `GET /api/agents`.
- `POST /api/chat/<agent>` (`/api/chat/docs`, `/api/chat/sql`) skips the router and forces that agent (API only). An unknown agent name returns 404.

**Adding an agent**
- An agent is a package under `app/agents/<name>/`: `tools.py`, `prompts.py`, and `agent.py`, which builds the agent and defines its `SPEC` (route name, description, UI labels, hint words, and a `tool_description` if the orchestrator may ask it). Add the `SPEC` to `AGENTS` in `app/agents/registry.py`. The router, orchestrator, chat API and UI pick it up with no other changes.
- A multi-step workflow is added the same way; its `build` returns a small graph instead of a single agent. An entry that only gathers material (like research) sets `answers_directly=False`, and the orchestrator writes its answer.
- If the agent needs its own data (tables, ingestion, API routes, background jobs), add a feature under `app/features/<name>/` and register it in `app/features/registry.py`.
- The full checklist is in CLAUDE.md ("Adding an agent or workflow").

**Memory**
- *Within a chat:* every message is checkpointed in Postgres (`thread_id` = conversation id), so chats survive restarts.
- *Across chats:* a knowledge graph per user (`app/core/graph.py`, tables `graph_nodes` and `graph_edges`).
  - After each reply the orchestrator writes (not plain stock, chart or document lookups), one extra model call picks out facts about you as triples (`User works_at <company>`, risk tolerance, goals, preferences, what a file of yours is).
  - Names are matched to existing nodes (exact, then by embedding). A new value of a one-value fact (where you live or work, your risk tolerance…) replaces the old one.
  - Each upload gets a *document card*: one model call reads the start of the file and stores its type, a one-line summary and up to 10 names it is about.
  - Before each turn, your newest facts go into every agent's prompt. When a documents question names a person or company the graph links to a file, the search looks in that file first, then in all files.
  - Deleting a chat keeps the facts; deleting a file or the account removes its part of the graph.
- The checkpointer and the store share one psycopg connection pool that checks each connection before use. If Postgres restarts (for example when WSL shuts down), the app reconnects on its own and doesn't need a restart.
- The UI remembers the open chat, so reloading the page brings it back.

**Research agent (`app/agents/research/`)**
- The researcher (`gather`) only collects findings; whenever it searches Wikipedia it also runs a web search in the same step (when Tavily is configured); the orchestrator writes the answer, which is the only part saved to the chat and streamed. The researcher's tool calls show in the UI as progress, but its notes don't.
- MCP (`app/core/mcp.py`): remote servers (Tavily, `MCP_SERVERS`) are loaded with `MultiServerMCPClient`, one short session per tool call. Our own servers (`app/features/research/mcp_servers/`: `knowledge` for Wikipedia and arXiv, `external_db` for `EXTERNAL_DATABASE_URL`) run in-process over MCP's memory transport. That needs no subprocess, which wouldn't start on Windows under the selector event loop. Tools are named `<server>_<tool>`. A server that fails to load is skipped with a warning.
- Skills (`app/agents/skills.py`) are `skills/<role>/<name>/SKILL.md` files. The prompt lists their names and descriptions, and the agent calls `load_skill` to read one. The researcher's skills are `web-research`, `market-data` and `academic`. The answer formats in `app/orchestration/formats/` (`summary`, `list`, `structured-report`, `comparison` and `company-brief`) are always in the orchestrator's prompt while it writes up results, and it picks one from the question.

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
4. Create `cloudrun.env.yaml` (gitignored). It's your `.env` as YAML, with the Neon URLs and `SESSION_HTTPS_ONLY: "true"`. All values must be quoted strings. Add `TAVILY_API_KEY` (and `MCP_SERVERS` / `EXTERNAL_DATABASE_URL` if you use them) for the research agent. `MCP_SERVERS` is JSON, so wrap it in single quotes in YAML.
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

**Upgrading a database from before the knowledge graph** (once): the app creates the graph tables at startup, but old memories and files need converting.
1. Back up first: in the Neon console create a branch (an instant copy), e.g. `pre-graph-<date>`. Delete it after a week or so.
2. Deploy the new version.
3. From your PC, with the same embedding settings as the deployment, run:
   ```powershell
   $env:DATABASE_URL="<neon owner url>"; uv run python -m scripts.build_graph
   ```
   For each user it turns the old memory sentences into graph facts (one model call per 5 memories, then deletes them) and reads every turn of their past chats for facts (one call per 4 turns of a chat; short chats still take one call each, so expect roughly one call per chat). Then it writes a card for every file that has none (one call each, read from the stored chunks). It prints the number of calls first. Each user is built once: a finished user is skipped on later runs, and a run stopped by the rate limit resumes where it stopped. New accounts never need it.

**Current deployment:** project `smallie-review`, region `asia-southeast1`, https://smallie-976519788978.asia-southeast1.run.app, database on Neon (Singapore, Postgres 18). App logs:
```powershell
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=smallie" --limit 50 --freshness 1h
```

**Limits:** the first request after ~15 min idle cold-starts the container (10–20 s). The OpenRouter free quota is shared by everyone using the deployment. The image is stored in Artifact Registry as `asia-southeast1-docker.pkg.dev/smallie-review/cloud-run-source-deploy/smallie` (0.5 GB free; delete old images if you redeploy often). Neon only hosts the database; a deploy never touches it unless the schema changes.

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
