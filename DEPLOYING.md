# Deploying smallie to Google Cloud Run + Neon

The app runs as a container on **Cloud Run** (free tier, scales to zero). All data lives in a free **Neon** Postgres, so nothing is lost when the container stops. Uploaded files are only needed while they're being indexed. The four specialists run as their own private Cloud Run services (section 3). For how the parts fit together, see [ARCHITECTURE.md](ARCHITECTURE.md).

**tl;dr, in order (first setup):**
1. Neon: create the project, enable pgvector, create the read-only role, load the stocks (section 1).
2. Agent servers: roles and databases in Neon, Upstash Redis, env file, build the image, deploy the four services, grant the app access (section 3). The app doesn't start without `AGENT_URLS`, so this comes before the app's deploy.
3. Cloud Run app: enable services, write `cloudrun.env.yaml` with the agent URLs, deploy (section 2, step 5).
4. After that, redeploy with the same commands whenever code or config changes.

## 1. Neon (database), once
1. Create a project at https://neon.tech, region AWS Singapore. Copy the **direct** connection string (not the `-pooler` one; the app uses advisory locks).
2. Enable pgvector and create the read-only role, using the statements in `db/init/01_extensions.sql` and `db/init/03_readonly_role.sql` (run them with `psql` or a short psycopg script as the owner):
   - Use a strong generated password instead of `'readonly'`; Neon rejects weak passwords.
   - Use your database's name in `GRANT CONNECT` (Neon's default is not `smallie`).
   - The schema grants come later from `load_stocks`, which re-applies `04_market_schema.sql`.
3. Load the stocks from your PC (Yahoo often blocks cloud IPs on a big first download):
   ```powershell
   $env:DATABASE_URL="<neon owner url>"; uv run python -m scripts.load_stocks --full
   ```

## 2. Cloud Run (app)
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
4. Create `cloudrun.env.yaml` (gitignored). It's your `.env` as YAML, with the Neon URLs and `SESSION_HTTPS_ONLY: "true"`. All values must be quoted strings. Add `TAVILY_API_KEY` for the orchestrator's web search. `MCP_SERVERS` and `EXTERNAL_DATABASE_URL` belong only in `cloudrun.agent.research-secrets.yaml` (section 3, step 3); `MCP_SERVERS` is JSON, so wrap it in single quotes in YAML.
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

## 3. Agent servers (docs, sql, research, chart)

Each specialist runs as its own private Cloud Run service, all from one image (`Dockerfile.agent`). The main app calls them with the service token and a Google ID token. Since 0.4.0 the app doesn't start without `AGENT_URLS`, so on a first setup do this section before the app's deploy (2.5). Do these once, in this order, then repeat steps 4–5 after changing agent code.

1. **Neon: two more roles and the agents' databases.** Create the roles yourself with strong passwords, because Neon rejects the weak ones in the files. Use long passwords of letters and digits only: they also go into URLs and PowerShell strings, where `$ @ / ? # &` break. Then run the two scripts with the `psql` inside the local `smallie-db` container (start it with `scripts\dev_db.ps1`). `--exec` hands the arguments over as they are; without it, bash splits the URL at its `&`.
   ```powershell
   $neon = "<neon owner url>"   # direct URL of smalliedb (not -pooler)
   $sql = { wsl -d Ubuntu -u root --exec docker exec smallie-db psql $neon -v ON_ERROR_STOP=1 @args }
   & $sql -tAc "SELECT current_user, pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()"
   ```
   Both names must be the same (the app's role owns `smalliedb`). If they differ, stop: `06` would lock the live app out. Otherwise:
   ```powershell
   & $sql -c "CREATE ROLE agent_server LOGIN PASSWORD '<strong 1>'" -c "CREATE ROLE agent_reader LOGIN PASSWORD '<strong 2>'"
   & $sql -f /docker-entrypoint-initdb.d/05_agent_databases.sql   # agent_docs, agent_sql, agent_research, agent_chart, owned by agent_server
   & $sql -f /docker-entrypoint-initdb.d/06_agent_reader.sql      # agent_reader's settings; smalliedb stops accepting every login
   ```
   - `agent_server` holds only the agents' run queues and can't open `smalliedb`. `agent_reader` can only read documents, the knowledge graph and chat history, never accounts or chats. The main app grants its tables at every start.
   - After `06`, check that the live app's stock questions still work (`06` grants `agent_readonly` access explicitly).
   - If `05` fails at `CREATE DATABASE ... OWNER agent_server`, create the four databases in the Neon console with owner `agent_server`.
   - The owner URL and both passwords are now in PowerShell's history (`(Get-PSReadLineOption).HistorySavePath`); delete those lines from that file.
2. **Upstash: a free Redis.** Create a database at https://upstash.com (region `ap-southeast-1`) and copy its `rediss://default:...@....upstash.io:6379` URL. The four servers share it; each uses its own key prefix.
3. **`cloudrun.agent.env.yaml`** (gitignored). It holds only what an agent server needs, never `APP_PASSWORD` or `SESSION_SECRET`. All values are quoted strings:
   ```yaml
   OPENROUTER_API_KEY: "..."          # and the OPENROUTER_* / EMBED_DIM / MAX_MODEL_CALLS / RESEARCH_MAX_MODEL_CALLS / SQL_MAX_ROWS values you use in cloudrun.env.yaml
   LANGSMITH_API_KEY: "..."           # required: the free Agent Server licence checks it
   SERVICE_TOKEN: "..."               # the same value as in cloudrun.env.yaml
   # LANGSMITH_TRACING: "true"        # optional, with LANGSMITH_PROJECT: records the servers' part of a traced chat
   DATABASE_URL: "postgresql://agent_reader:<strong 2>@<neon direct host>/smalliedb?sslmode=require"
   READONLY_DATABASE_URL: "<the same value as in cloudrun.env.yaml>"
   REDIS_URI: "rediss://default:...@....upstash.io:6379"
   DB_POOL_SIZE: "2"
   LANGGRAPH_POSTGRES_POOL_MAX_SIZE: "5"
   BG_JOB_MAX_RETRIES: "1"            # counts attempts: a run cut off by a restart is not repeated
   BG_JOB_TIMEOUT_SECS: "360"         # stops a run the main app gave up on (it waits 300 s)
   ```
   Research's keys go in a second gitignored file, `cloudrun.agent.research-secrets.yaml`, so that only the research server gets them (the other servers have no use for them). It holds `TAVILY_API_KEY`, plus `MCP_SERVERS` / `EXTERNAL_DATABASE_URL` if you use them. `MCP_SERVERS` is JSON, so wrap it in single quotes. The main app keeps its own `TAVILY_API_KEY` in `cloudrun.env.yaml`, because the orchestrator also searches the web.
4. **Build the image** (Cloud Build, about 5 minutes):
   ```powershell
   gcloud builds submit --config cloudbuild.agent.yaml
   ```
5. **Deploy the four services.** Each one gets its own env file with three route-specific lines (gcloud takes only one env file, and doesn't combine it with `--set-env-vars`). The files are deleted after each deploy:
   ```powershell
   $agentDb = "postgresql://agent_server:<strong 1>@<neon direct host>"   # not -pooler: the servers take advisory locks
   foreach ($r in "docs", "sql", "research", "chart") {
     $file = "cloudrun.agent.$r.env.yaml"
     $lines = @(Get-Content cloudrun.agent.env.yaml) + "AGENT_ROUTE: `"$r`"",
       "DATABASE_URI: `"$agentDb/agent_${r}?sslmode=require`"", "REDIS_KEY_PREFIX: `"${r}:`""
     if ($r -eq "research") { $lines += @(Get-Content cloudrun.agent.research-secrets.yaml) }
     [IO.File]::WriteAllLines("$PWD\$file", $lines)   # UTF-8 without BOM
     gcloud run deploy "smallie-$r" --image asia-southeast1-docker.pkg.dev/smallie-review/cloud-run-source-deploy/smallie-agent `
       --region asia-southeast1 --no-allow-unauthenticated --env-vars-file $file `
       --memory 1Gi --cpu 1 --min-instances 0 --max-instances 1 --timeout 600
     Remove-Item $file
   }
   ```
   - `--no-allow-unauthenticated`: only callers with `roles/run.invoker` get through. Each server also checks the service token.
   - No `--no-cpu-throttling`: an agent only works while the main app's request is open, so it is billed only then.
6. **Let the main app call them**, then point it at their URLs:
   ```powershell
   $num = gcloud projects describe smallie-review --format="value(projectNumber)"
   foreach ($r in "docs", "sql", "research", "chart") {
     gcloud run services add-iam-policy-binding "smallie-$r" --region asia-southeast1 --role roles/run.invoker `
       --member "serviceAccount:$num-compute@developer.gserviceaccount.com"
     gcloud run services describe "smallie-$r" --region asia-southeast1 --format "value(status.url)"
   }
   ```
   Add the printed URLs and the token to `cloudrun.env.yaml`, then redeploy the app (step 2.5). Use the URLs exactly as printed, because the ID token is issued for that URL:
   ```yaml
   AGENT_URLS: '{"docs": "https://smallie-docs-....run.app", "sql": "...", "research": "...", "chart": "..."}'
   SERVICE_TOKEN: "..."
   ```

## 4. Current deployment

Project `smallie-review`, region `asia-southeast1`, https://smallie-976519788978.asia-southeast1.run.app, database on Neon (Singapore, Postgres 18). App logs:
```powershell
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=smallie" --limit 50 --freshness 1h
```

## 5. Limits

The first request after ~15 min idle cold-starts the container (10–20 s), and the first call to an idle agent server cold-starts that one too. The OpenRouter free quota is shared by everyone using the deployment. The images are stored in Artifact Registry as `asia-southeast1-docker.pkg.dev/smallie-review/cloud-run-source-deploy/smallie` and `.../smallie-agent` (0.5 GB free for both; delete old images if you redeploy often). An agent server that is awake polls Redis about once a second, so watch the Upstash command count in the first week (its free tier has a monthly cap). Neon only hosts the database; a deploy never touches it unless the schema changes.
