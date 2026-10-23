# README Refactor: Split into Compact README + DEPLOYING.md

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the bloated README (335 lines) into a compact quick-start guide and a separate deployment guide, with a table of contents linking to sections.

**Architecture:** 
- README.md becomes a 150-180 line quick-reference with setup, run, accounts, and models sections, pointing to ARCHITECTURE.md for architecture details and DEPLOYING.md for deployment
- DEPLOYING.md becomes a new 200+ line deployment guide with all step-by-step Cloud Run, Neon, and agent server setup instructions
- TOC uses markdown link syntax to allow quick navigation within the README

**Tech Stack:** Markdown, no dependencies

**Spec:** User's inline request in this conversation

## Global Constraints

- README must stay under 180 lines (currently 335)
- Architecture details must point to ARCHITECTURE.md, not duplicate them
- Deployment details must move to DEPLOYING.md entirely
- All code blocks must have language tags (powershell, bash, yaml)
- Links to other docs use markdown syntax: `[ARCHITECTURE.md](ARCHITECTURE.md)`
- Preserve exact command-line examples and configuration values

## Review Focus

The plan carries no novel business logic, so we focus on structure:

1. **Navigation:** TOC links in README correctly point to H2 headers, and links between docs work
2. **Completeness:** No deployment steps remain in README; all moved to DEPLOYING.md
3. **Brevity:** README explains concepts once, in ~1-3 sentences per section
4. **Accuracy:** Exact values from current README preserved (paths, ports, defaults, model names)
5. **Discoverability:** Key info (prerequisites, quick start, troubleshooting) is easy to find from README top

---

## File Structure

- **README.md** (rewrite, ~160 lines)
  - Intro paragraph
  - Quick Start TOC (table format with links)
  - Setup section (condensed: prerequisites, database, app, agent servers)
  - Run section
  - Accounts section
  - Models and cost section (brief, links to config file)
  - Architecture section (brief, links to ARCHITECTURE.md)
  - Adding an agent section (one paragraph, links to CLAUDE.md)
  - Deploying section (one paragraph intro, full link to DEPLOYING.md)
  - Troubleshooting section (condensed, ~10 common issues)

- **DEPLOYING.md** (create, ~220 lines)
  - Intro (what it covers)
  - tl;dr for experienced users
  - Section 1: Neon setup (create DB, enable pgvector, create roles, load stocks)
  - Section 2: Cloud Run setup (gcloud init, enable services, create env file, deploy app)
  - Section 3: Agent servers (Neon roles/databases, Upstash Redis, env files, build, deploy 4 services, IAM bindings)
  - Section 4: Upgrading database from pre-graph version
  - Section 5: Current deployment info (project, region, URLs, log command)
  - Section 6: Using your own database (brief)

---

### Task 1: Create DEPLOYING.md with deployment instructions

**Files:**
- Create: `DEPLOYING.md` (root level, alongside README.md)
- Source: Extract from current README.md lines 193-334 (Deploying section and related content)

**Interfaces:**
- Consumes: Current README.md Deploying section (lines 193-334)
- Produces: DEPLOYING.md as a standalone deployment guide with clear numbered sections

- [ ] **Step 1: Identify the deployment content to extract**

Review current README.md lines 193-334. The section "Deploying (Google Cloud Run + Neon)" contains:
- Neon setup (create project, enable pgvector, create roles)
- Cloud Run setup (gcloud init, billing, services, env file, deploy)
- Agent servers setup (Neon roles/databases, Upstash Redis, env files, build, deploy)
- Upgrading database (build_graph script)
- Current deployment info (project, logs command)
- Using your own database

Extract these in order, preserving exact commands and configuration values.

- [ ] **Step 2: Write DEPLOYING.md header and overview**

Create `DEPLOYING.md` at the project root with:
- Title: "# Deploying Smallie to Google Cloud Run + Neon"
- Intro: 1 paragraph explaining what this covers (Cloud Run container, Neon database, agent servers)
- tl;dr: Brief numbered list for experienced users (Neon, Cloud Run, agent servers, point app at URLs)
- "For detailed steps, see below"

- [ ] **Step 3: Add Neon setup section**

Create **"1. Neon (database)"** section from current lines 197-206:
- Create project at neon.tech, copy direct connection string (explain why not -pooler)
- Enable pgvector: exact SQL from db/init/01_extensions.sql
- Create agent_reader role with strong password
- Load stocks from PC: exact command with `$env:DATABASE_URL`

Include note: "Use strong generated password instead of 'readonly'; Neon rejects weak ones"

- [ ] **Step 4: Add Cloud Run app section**

Create **"2. Cloud Run (app)"** section from current lines 208-232:
- Install gcloud CLI
- Create project, set billing, enable services (exact gcloud commands)
- Create `cloudrun.env.yaml` with exact YAML structure shown
- Deploy command with all flags: `--no-cpu-throttling`, `--max-instances 1`, `--timeout 900`
- Explain what each flag does

- [ ] **Step 5: Add agent servers section**

Create **"3. Agent servers (docs, sql, research, chart)"** section from current lines 234-304:
- Intro: why agents are separate services
- Neon setup: Create agent_server and agent_reader roles, run 05 and 06 scripts
- Upstash: create free Redis database
- `cloudrun.agent.env.yaml`: exact YAML structure
- `cloudrun.agent.research-secrets.yaml`: note that only research gets these
- Build image: gcloud builds submit command
- Deploy 4 services: PowerShell loop with exact structure, explaining each flag
- Add IAM bindings: gcloud command to let main app call agents

- [ ] **Step 6: Add database upgrade section**

Create **"4. Upgrading database from pre-graph version"** section from current lines 306-313:
- When: for databases created before knowledge graph feature
- Steps: backup in Neon, deploy, run build_graph script
- What it does: converts old memories, reads past chats, writes cards

- [ ] **Step 7: Add current deployment info**

Create **"5. Current deployment"** section from current line 315:
- Project `smallie-review`, region `asia-southeast1`, URL
- Log command for troubleshooting

- [ ] **Step 8: Add own-database section**

Create **"6. Using your own database"** section from current lines 322-324:
- Point READONLY_DATABASE_URL at your database
- Set SCHEMAS in app/agents/sql/tools.py
- Agent reads schema at startup

- [ ] **Step 9: Add limits section**

Create **"Limits"** section (new, from implicit knowledge):
- Cold starts: container ~15 min, agent servers when idle
- Quotas: OpenRouter free, Upstash monthly cap
- Artifact Registry: 0.5 GB free, delete old images
- Neon: free tier limits (databases per project, storage)

- [ ] **Step 10: Commit DEPLOYING.md**

```bash
git add DEPLOYING.md
git commit -m "docs: add DEPLOYING.md with step-by-step deployment guide"
```

---

### Task 2: Rewrite README.md to be compact with table of contents

**Files:**
- Modify: `README.md` (rewrite, keep ~160 lines)
- Test: None (documentation-only change)

**Interfaces:**
- Consumes: Current README.md, ARCHITECTURE.md (for context on what to link), DEPLOYING.md (created in Task 1)
- Produces: Compact README.md with TOC and section links

- [ ] **Step 1: Write intro section**

Rewrite README.md with:
- Title: `# [smallie](https://smallie-976519788978.asia-southeast1.run.app/)`
- Intro paragraph (keep as-is, current lines 1-3): "LangChain agents behind a FastAPI app..."
- Bullet list of 4 agents: Documents, Database, Charts, Research (condensed from current lines 5-8)
- Agents' shared traits: English/Vietnamese, memory across chats, knowledge graph (condensed from current lines 12)

Target: 10 lines total (intro + agents + memory note)

- [ ] **Step 2: Write Quick Start TOC**

Create **"## Quick Start"** section with a table (not bullets):

```markdown
| Section | Use for |
|---------|---------|
| [Setup](#setup) | Installing dependencies and database |
| [Run](#run) | Starting the app |
| [Accounts](#accounts) | Creating and managing logins |
| [Models and cost](#models-and-cost) | LLM configuration and pricing |
| [Architecture](#architecture) | How the app works |
| [Deploying](#deploying-to-google-cloud-run--neon) | Hosting on Cloud Run |
| [Troubleshooting](#troubleshooting) | Common issues |
```

This replaces current lines 14-166 (all of "Prerequisites", "Setup", and most detailed sections).

- [ ] **Step 3: Write Setup section (condensed)**

Create **"## Setup"** section (~50 lines) with:

**Prerequisites** subsection:
- Table with Python, uv, WSL2+Docker, OpenRouter (current lines 14-21, no changes)

**Database** subsection:
- Explain: Postgres 18 + pgvector in Docker inside WSL
- Once: exact commands for WSL Docker install + docker compose up (current lines 25-29, condensed)
- Each time: point to `scripts\dev_db.ps1` with brief explanation (current lines 32-36)
- Alternative: WSL terminal commands (current lines 39-45, condensed to 3 lines)

**App and stock data** subsection:
- Four exact lines: copy .env.example, uv sync, pytest, load_stocks
- One-sentence explanation of what happens after (daily updates)
- Link to Troubleshooting for options

**Agent servers** subsection:
- Three steps: set .env values, turn off VPN, build and start
- One-sentence warning about VPN corruption
- One-sentence about `docker compose ps` showing healthy status

Target: 50-60 lines total (currently 73 lines of sprawling text)

- [ ] **Step 4: Write Run section**

Create **"## Run"** section (~8 lines):
- Single code block with exact uvicorn command (current line 87)
- One-line instruction: "Open http://localhost:8000"
- Note about --loop flag (current lines 92-93, condensed to 2 sentences)

- [ ] **Step 5: Write Accounts section**

Create **"## Accounts"** section (~12 lines):
- Brief explanation: everyone creates their own, up to MAX_ACCOUNTS
- What to set: APP_PASSWORD and SESSION_SECRET
- Claim anonymous data: exact script command
- **"Sharing with others on your network"** subsection:
  - One code block: uvicorn with --host 0.0.0.0
  - Windows firewall command (condensed from current lines 109-113)
  - One-sentence: "For access from anywhere, see [Deploying](#deploying-to-google-cloud-run--neon)"

- [ ] **Step 6: Write Models and cost section**

Create **"## Models and cost"** section (~25 lines):
- Intro: "All calls go through OpenRouter (defaults in `.env`)"
- Five bullet points:
  - **Main model**: free tier limits, fallback models for Vietnamese
  - **Orchestrator**: when skipped, call cap, empty means main model
  - **Embedding**: default, cost, free option
  - **Web search**: Tavily, credit limit, what happens without it
  - **Tracing**: optional, when to enable, privacy note
- **Call counts per question** subsection: 5 bullet points (general, database, research, multi-agent, measurement script)
- **Changing embedding**: one sentence about stopping app and running reindex

Target: 25 lines (currently 127 lines)

- [ ] **Step 7: Write Architecture section**

Create **"## Architecture"** section (~12 lines):
- Intro (2 lines): "The app is a router + orchestrator + four specialists. See [ARCHITECTURE.md](ARCHITECTURE.md) for details:"
- Bullet list with section numbers from ARCHITECTURE.md:
  - Routing (section 4)
  - Orchestrator (section 5)
  - Memory (section 7)
  - Research (section 8)
  - Remote agents (section 10)
  - Database design (section 2)

- [ ] **Step 8: Write Adding an agent section**

Create **"## Adding an agent"** section (~4 lines):
- Three steps: create folder, define SPEC, add to registry (current lines 162-166)
- Two sentences on workflows and features
- One sentence: "See `CLAUDE.md` ("Adding an agent") for the full checklist."

- [ ] **Step 9: Write Deploying section**

Create **"## Deploying to Google Cloud Run + Neon"** section (~6 lines):
- Brief intro (2 lines): "The app runs in a Cloud Run container with data in Neon Postgres."
- Three-step summary (brief): Neon, Cloud Run, agent servers
- One sentence: "See [DEPLOYING.md](DEPLOYING.md) for step-by-step instructions."
- Current deployment line: project, region, URL

- [ ] **Step 10: Write Troubleshooting section**

Create **"## Troubleshooting"** section (~20 lines):
- Keep only the 6-8 most common issues from current lines 326-334:
  - Port 5433 already in use (two fixes)
  - Can't connect from Windows
  - Recreate database from scratch
  - Chat errors: connection closed (brief)
  - Stock questions return nothing
  - No text found on PDF (brief)
  - Cloud Run build fails (brief, link to DEPLOYING.md)
- Format: bullet points with problem + solution (one sentence each)

- [ ] **Step 11: Write footer**

Add brief footer:
- One blank line
- Note about Readme button in app
- Links to CLAUDE.md and ARCHITECTURE.md

- [ ] **Step 12: Verify README structure and line count**

Check:
- TOC links match H2 headers exactly (section anchors)
- No architectural details beyond "see ARCHITECTURE.md"
- No deployment details beyond "see DEPLOYING.md"
- All code blocks have language tags (powershell, bash, yaml)
- Line count: target 150-180 lines (currently 335)
- Each section explains concepts once, in 1-3 sentences

- [ ] **Step 13: Commit README.md**

```bash
git add README.md
git commit -m "docs: refactor README to be compact with TOC, move deployment to DEPLOYING.md"
```

---

## Self-Review Checklist

✓ **Spec coverage:** The user requested compact README (under 180 lines), TOC with links, move deployment details, point to ARCHITECTURE.md. Both tasks address all requirements.

✓ **Step clarity:** Each step names what to write or extract, with line number references or exact values to preserve. Implementer can proceed without rereading README context.

✓ **Type consistency:** No code changes, only documentation structure. No inconsistency risk.

✓ **Review Focus:** Documentation carries no correctness risk. Focus shifts to:
1. **Navigation works:** TOC links land on correct headers (markdown anchor syntax)
2. **Nothing lost:** All current content preserved, just reorganized (deployment moved to new file, architecture condensed)
3. **Brevity met:** README < 180 lines, explanations 1-3 sentences each
4. **Links active:** DEPLOYING.md, ARCHITECTURE.md, CLAUDE.md all exist and are referenced correctly
5. **Code accuracy:** Exact values (ports, defaults, commands) match current README

✓ **Proportion:** Plan is ~450 lines for ~300 lines of output (README 160 + DEPLOYING 220). Plan includes decisions (which sections, how to condense), not transcription. Reasonable.

---
