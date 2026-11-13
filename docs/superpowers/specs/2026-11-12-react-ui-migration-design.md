# React UI migration: design

Date: 2026-11-12. Status: approved in conversation, awaiting review of this file.
Release: 0.8.0 (architecture change: how the UI is built and deployed).

## Goal

Rebuild smallie's UI (`app/static/`: `index.html`, `app.js`, `charts.js`, `i18n.js`, `style.css`, about 2,600 lines of plain HTML/JS with no build step) in React + Vite + TypeScript + Tailwind + shadcn/ui, so the UI can grow (the planned team-room chat, more screens) without hand-written DOM updates.

**Port first.** The new UI does what today's UI does, in shadcn's look. The team-room redesign is a separate, later spec on top of the new stack.

Success: every line of the parity checklist (below) works at `/next` on real data, then `/next` replaces `/` and `app/static/` is deleted. No backend API change is needed.

## Approach: side by side at `/next`

The old UI stays at `/`, untouched, until the last milestone. The new UI is served at `/next` from the built `frontend/dist/`. One PR per milestone. Rejected: one long branch switched in one PR (drifts from `main`, huge review); React islands inside the old page (fights `app.js` globals, two styling systems on one page).

## Stack

| Part | Choice | Reason |
|---|---|---|
| Build | Vite | Dev server with hot reload and `/api` proxy; production build with hashed filenames |
| Language | TypeScript | API and event shape changes become compile errors |
| UI kit | shadcn/ui on Radix | Most examples, shadcn's longest-supported base |
| Styling | Tailwind v4 | Required by shadcn |
| Charts | Chart.js (kept) | A port does not change how charts look; shadcn charts can come with the redesign |
| Markdown | marked + DOMPurify (kept, from npm) | Same rendering and sanitising as today |
| State | React state + one `AppContext` | One user, one chat view; no state library until one is needed |
| Tests | Vitest | Logic tests for stream, reducer, i18n, API helper |

## 1. Layout, serving, development, deploy

```
frontend/
  package.json, vite.config.ts, tsconfig.json, components.json
  src/
  dist/            (gitignored build output)
app/static/        (old UI, untouched until M5)
```

**Serving.** `app/api/main.py` mounts `frontend/dist/` at `/next`, before the existing `/` mount.
- `index.html` is served with `Cache-Control: no-cache`.
- `/next/assets/*` is served with `Cache-Control: public, max-age=31536000, immutable` (filenames carry a content hash).
- When `frontend/dist/` does not exist, the `/next` mount is skipped with one log line and the app starts normally.
- Vite `base` is `"/next/"` until M5, then `"/"`.

**Development.** Two processes: `uv run uvicorn ...` on :8000 as today, and `npm run dev` in `frontend/` on :5173. Vite proxies `/api/*` to `http://localhost:8000`, so the session cookie and the chat stream work unchanged. Backend-only work needs no Node.

**Deploy.** Same `gcloud run deploy --source .` command.
- `Dockerfile` gains a first stage `FROM node:22-slim` that runs `npm ci && npm run build` in `frontend/`. The Python stage copies only `frontend/dist/`; the runtime image has no Node.
- Node version pinned in `frontend/package.json` `engines` and in the Dockerfile tag.
- `.dockerignore` and `.gcloudignore` exclude `frontend/node_modules` and `frontend/dist`. In `.gcloudignore` the entries are root-anchored (`/frontend/node_modules`, `/frontend/dist`).
- `Dockerfile.agent` is unchanged.

**Docs in the same branch.** `ARCHITECTURE.md` (UI part), `DEPLOYING.md` (Node build stage), `CLAUDE.md` (commands: `npm run dev`, `npm run build`, `npm test`), `.gitignore` (`frontend/node_modules`, `frontend/dist`).

## 2. Frontend structure

```
src/
  lib/
    api.ts        fetch wrapper: /api prefix, JSON, 401 -> login, error detail
    stream.ts     reads the chat response body, yields parsed "data: ...\n\n" events, skips malformed ones
    turn.ts       reduceTurn(turn, event) -> turn   (pure: no DOM, no React)
    types.ts      types for every stream event and API response
    markdown.ts   marked + DOMPurify + citation links + plain math, as in app.js
  i18n/           en.ts, vi.ts (same keys as i18n.js), useT() hook
  state/          AppContext: me, lang, agents, conversations, current conversation
  features/
    auth/         Login, Signup
    sidebar/      conversation list, new chat, team switch, usage box
    chat/         MessageList, Message, Composer, AgentBadges, ToolBlock, ChartCard, documents panel
    activity/     team activity dialog
    settings/  usage/  readme/  accounts/  market/
  components/ui/  shadcn components
```

**Chat logic as pure functions.** Today `sendMessage` in `app.js` reads the stream, handles every event type (`conversation`, `routing`, `model_fallback`, `agent`, `step`, `fallback`, `plan`, `plan_updated`, `wave`, `review`, `task_started`, `spoke`, `synthesize`, `charts`, `needs_human`, `usage`, `done`, `token`, `tool_call`, `tool_result`, `error`) and edits the DOM in one function. In the new UI `stream.ts` yields events, `reduceTurn` turns them into a turn object, and components render the turn. Reopening a conversation replays its stored events through the same `reduceTurn`, so a reloaded turn looks the same as a live one (today `teamActivity` repeats part of that logic).

**shadcn mapping.** `<dialog>` -> `Dialog`; team checkbox -> `Switch`; settings -> `Tabs`, `RadioGroup`, `Select`, `Input`; accounts -> `Table`; sidebar -> `Sidebar`; buttons -> `Button`; errors -> `Sonner` toast or inline `Alert`; tool blocks and "show data" -> `Collapsible`.

**Theme.** shadcn CSS variables in three named colour themes: `chatroom` (default; the Sherwin-Williams Chatroom palette), `shadcn` (neutral defaults) and `smallie` (the M0 green). `?theme=<name>` sets `data-theme` on `<html>` and is remembered in `localStorage` key `theme`; unknown or missing falls back to the stored value, then `chatroom`. `--series-1..8` (light and dark values, carried over from `style.css`) stay the same in every theme, so charts keep their colours. Dark mode follows `prefers-color-scheme`, applied by toggling the `.dark` class. No manual theme toggle in this port.

**Persistence.** Language and current conversation stay in `localStorage` under the same keys as `app.js`, so switching between `/` and `/next` keeps them.

## 3. Milestones

One PR each, branch `0.8.0/react-ui-m<N>`. Each is usable at `/next` when merged; `/` is untouched until M5.

| # | Milestone | Works at `/next` after it | Rough effort |
|---|---|---|---|
| M0 | Scaffold | Vite + React + TS + Tailwind + shadcn, `/next` mount, Dockerfile stage, ignore files, proxy, Vitest, docs; a blank page with theme and EN/VI toggle | half a day |
| M1 | Sign-in and shell | `/api/me`, login, signup, logout, header, language toggle, Readme dialog | half a day |
| M2 | 1-to-1 chat | conversation list, new chat, open, delete, streamed answer with badges, tools, markdown, citations, math, charts, errors, ask-back, usage line, documents panel | 2-3 days |
| M3 | Team mode | team switch, plan, waves, reviews, write-up, team charts, activity dialog | 2 days |
| M4 | The rest | settings, usage dialog, accounts admin, market status | 2 days |
| M5 | Switch | parity checklist ticked, `base: "/"`, `dist` mounted at `/`, `app/static/` deleted, docs | half a day |

M0-M4 may deploy at any time (nobody reaches `/next` without the link). The changelog lists the new UI under the release that ships M5.

## 4. Testing and error handling

**Vitest (`npm test` in `frontend/`).**
- `stream.ts`: an event split across chunks; several events in one chunk; a malformed event is skipped and later events still arrive; a stream that ends mid-event.
- `turn.ts`: recorded event sequences of real chats, captured once from the live API during M2/M3 and stored as JSON in `frontend/src/lib/__fixtures__/`: 1-to-1 with handoff; tools and a chart; model fallback; ask-back; full team run (plan, waves, review, write-up, charts); error mid-stream. Each must produce the turn the old UI shows for it.
- Reload equals live: replaying a stored conversation gives the same turn as its live stream.
- i18n: `en.ts` and `vi.ts` have the same keys; every key in the old `i18n.js` exists in them (until M5).
- `api.ts`: 401 goes to login; a non-OK response surfaces `detail`.
- Layout and styling are not unit-tested; the parity checklist covers them.

**Python.** Existing suite unchanged. New test: `/next` serves `index.html` when `frontend/dist/` exists, and the app starts with a log line when it does not.

**Error handling.**
- 401 from any call: `api.ts` shows the login screen.
- `error` event or network drop: the turn ends with the inline error and retry, as today (`streamCut`).
- Uncaught render error: a React error boundary shows a reload message instead of a blank page.
- Background loads (usage, market status) fail quietly and log to the console, as today.

**Per-PR verification.** `npm test`, `npm run build`, `npx tsc --noEmit`, `uv run pytest`; the user checks the screens at `/next`.

## Parity checklist

Ticked in the browser at `/next` on real data before M5.

**M1 Sign-in and shell**
- [ ] Logged-out visit shows login; wrong login shows the error
- [ ] Signup with name, username (3-32, letters/digits/_/-) and password (8+), with hints and server errors
- [ ] Switch between login and signup forms
- [ ] Header: user name, Readme, Accounts (admin only), language toggle, logout
- [ ] Language toggle switches every label EN/VI and is remembered
- [ ] Readme dialog opens and closes (backdrop click closes)

**M2 1-to-1 chat**
- [ ] Conversation list with titles; open, delete with confirm; empty-list text
- [ ] New chat; welcome text; last conversation reopens after reload
- [ ] Composer: Enter sends, Shift+Enter new line, auto-grows, disabled while busy
- [ ] "Thinking" placeholder, then streamed tokens
- [ ] Agent badges and step labels (handoff relabels the first badge)
- [ ] Tool blocks: "using tool", arguments, collapsible result
- [ ] Markdown, sanitised; citation links; plain-text math
- [ ] Chart cards: title, chart, PNG download, "show data" table; no duplicate chart ids; redraw on dark/light change
- [ ] Model fallback line
- [ ] Ask-back (`needs_human`): waiting state and answer placeholder
- [ ] Error mid-stream and cut stream message
- [ ] Per-reply usage line
- [ ] Documents panel: upload, list with chunk count, delete

**M3 Team mode**
- [ ] Team switch, locked to the conversation's kind
- [ ] Plan, plan updates, waves (answer, challenge, conflict), reviews, follow-ups
- [ ] Write-up streaming, team charts drawn after it
- [ ] Activity dialog: waves, answers, tools, models, fallbacks, board (agreements, differences, conflicts), summary counts
- [ ] Reloaded team chat looks the same as live

**M4 The rest**
- [ ] Sidebar usage box (tokens, cost) and usage dialog (per model, cache note)
- [ ] Settings: API key, endpoint, model list fetch and cache, standard/small/strong slots, profile (economy, balanced, advanced), slot warnings, save, clear with confirm, status text
- [ ] Accounts admin: count, list, admin badge, reset password, delete with confirm
- [ ] Market status: last update or empty text
