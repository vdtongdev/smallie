// "auto": the server picks one of its agents (GET /api/agents) for each message.
const KIND = "auto";

const state = {
  lang: localGet("lang") || "en",
  me: null, // {user_id, name, username, is_admin, max_accounts}
  conversationId: null,
  busy: false,
  docsPoll: null,
  awaiting: false, // the open chat is paused on a question to the user
  kinds: {}, // conversation id -> kind ("auto" | "team"), from /api/conversations
  agents: {}, // route -> {labels: {en, vi}, steps: {step: {en, vi}}}, from /api/agents
};

const $ = (id) => document.getElementById(id);
const t = (key) => I18N[state.lang][key] ?? key;
const agentLabel = (route) => state.agents[route]?.labels[state.lang] ?? route;
const stepLabel = (route, step) => state.agents[route]?.steps?.[step]?.[state.lang] ?? step;

function localGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function localSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* storage unavailable */ }
}

async function api(path, options = {}) {
  const res = await fetch(`/api${path}`, options);
  if (res.status === 401) {
    showLogin();
    throw new Error("Login required");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || res.statusText);
  }
  return res.status === 204 ? null : res.json();
}

// A link written as "example.com/page" (no scheme) would resolve inside the app; make it absolute.
const BARE_DOMAIN = /^(?!.*\.(?:pdf|docx?)$)(?:www\.)?[\p{L}\d-]+(?:\.[\p{L}\d-]+)*\.[a-z]{2,}(?:[/?#]|$)/iu;

// Source links open in a new tab, so clicking one doesn't leave the chat.
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName !== "A") return;
  const href = node.getAttribute("href") || "";
  if (BARE_DOMAIN.test(href)) node.setAttribute("href", `https://${href}`);
  if (/^https?:/i.test(node.getAttribute("href"))) {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

function renderMarkdown(text) {
  return DOMPurify.sanitize(marked.parse(plainMath(text || "")));
}

// Models sometimes write LaTeX ("$\rightarrow$", "$5\%$"), which marked shows raw. Known commands
// become Unicode; a $...$ span loses its $ only when nothing TeX is left, so "$5 to $10" is untouched.
const TEX_SYMBOLS = {
  rightarrow: "→", to: "→", longrightarrow: "⟶", leftarrow: "←", gets: "←", leftrightarrow: "↔",
  Rightarrow: "⇒", implies: "⇒", Leftarrow: "⇐", Leftrightarrow: "⇔", iff: "⇔",
  uparrow: "↑", downarrow: "↓", nearrow: "↗", searrow: "↘",
  times: "×", div: "÷", pm: "±", cdot: "·", approx: "≈", sim: "~", neq: "≠", ne: "≠",
  le: "≤", leq: "≤", ge: "≥", geq: "≥", ll: "≪", gg: "≫", infty: "∞", circ: "°", degree: "°",
  ldots: "…", dots: "…", cdots: "⋯", checkmark: "✓",
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", Delta: "Δ", epsilon: "ε", theta: "θ",
  lambda: "λ", mu: "μ", pi: "π", rho: "ρ", sigma: "σ", Sigma: "Σ", sum: "Σ", tau: "τ", omega: "ω",
};
const TEX_CMD_RE = /\\([A-Za-z]+)(?![A-Za-z])/g;

function texToText(s) {
  return s
    .replace(/\\(?:text|mathrm|textbf|mathbf)\{([^{}]*)\}/g, "$1")
    .replace(/\\([%$&#_])/g, "$1")
    .replace(/\\[,;: ]/g, " ")
    .replace(TEX_CMD_RE, (m, name) => TEX_SYMBOLS[name] ?? m);
}

function plainMath(text) {
  return text
    .replace(/\$(\S(?:[^$\n]*\S)?)\$/g, (m, inner) => {
      if (!inner.includes("\\")) return m;
      const out = texToText(inner);
      return /\\[A-Za-z]/.test(out) ? m : out.replace(/[{}]/g, "");
    })
    .replace(TEX_CMD_RE, (m, name) => TEX_SYMBOLS[name] ?? m);
}

// Reference numbers ("...text.[1][2]" or "[1, 2]") point into the numbered source list at the
// end of the same message (its last top-level <ol>); each becomes a superscript that scrolls there.
const CITE_RE = /\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]/g;

function linkCitations(el) {
  const refs = [...el.querySelectorAll(":scope > ol")].pop();
  if (!refs) return;
  const count = refs.children.length;
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) =>
      refs.contains(node) || node.parentElement.closest("a, code, pre") || !/\[\d/.test(node.data)
        ? NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT,
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const frag = document.createDocumentFragment();
    let last = 0;
    for (const match of node.data.matchAll(CITE_RE)) {
      const nums = match[1].split(",").map((n) => Number(n.trim()));
      if (nums.some((n) => n < 1 || n > count)) continue;
      frag.append(node.data.slice(last, match.index));
      for (const n of nums) {
        const sup = document.createElement("sup");
        const a = document.createElement("a");
        a.className = "cite";
        a.href = "#";
        a.dataset.ref = n;
        a.textContent = n;
        a.title = refs.children[n - 1].textContent.trim();
        sup.append(a);
        frag.append(sup);
      }
      last = match.index + match[0].length;
    }
    if (!last) continue;
    frag.append(node.data.slice(last));
    node.replaceWith(frag);
  }
}

$("messages").addEventListener("click", (e) => {
  const cite = e.target.closest("a.cite");
  if (!cite) return;
  e.preventDefault();
  const item = [...cite.closest(".msg").querySelectorAll(":scope > ol")].pop()?.children[cite.dataset.ref - 1];
  if (!item) return;
  item.scrollIntoView({ behavior: "smooth", block: "center" });
  item.classList.remove("ref-flash");
  void item.offsetWidth; // restart the animation on repeated clicks
  item.classList.add("ref-flash");
});

// ---------- i18n ----------
function applyLanguage() {
  document.documentElement.lang = state.lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => (el.textContent = t(el.dataset.i18n)));
  document.querySelectorAll("[data-i18n-title]").forEach((el) => (el.title = t(el.dataset.i18nTitle)));
  $("langBtn").textContent = state.lang === "en" ? "Tiếng Việt" : "English";
  $("input").placeholder = t(state.awaiting ? "answerPlaceholder" : "placeholder");
  document.querySelectorAll(".status-line").forEach((node) => renderStatus(node.turn));
  document.querySelectorAll("[data-lang]").forEach((el) => (el.hidden = el.dataset.lang !== state.lang));
  labelAgentBadges();
}

function labelAgentBadges() {
  document.querySelectorAll(".agent-badge").forEach((el) => (el.textContent = badgeLabel(el)));
}

const badgeLabel = (el) => (el.dataset.step ? stepLabel(el.dataset.agent, el.dataset.step) : agentLabel(el.dataset.agent));

async function loadAgents() {
  const agents = await api("/agents").catch(() => []);
  state.agents = Object.fromEntries(agents.map((a) => [a.route, { labels: a.labels, steps: a.steps || {} }]));
  labelAgentBadges();
}

$("langBtn").onclick = () => {
  state.lang = state.lang === "en" ? "vi" : "en";
  localSet("lang", state.lang);
  applyLanguage();
  if (!state.me) return; // logged out: only the login form needs the new language
  loadConversations();
  loadDocuments();
  loadUsage();
  if ($("readmeDialog").open) loadMarketStatus();
  if ($("adminDialog").open) loadAccounts();
  if ($("activityDialog").open) renderActivity();
  if (!state.conversationId) showWelcome();
};

// ---------- team mode ----------
// A new conversation starts as a team chat when the switch is on; an opened one stays team or else uses auto.
const chatKind = () => state.conversationId
  ? (state.kinds[state.conversationId] === "team" ? "team" : "auto")
  : ($("teamMode").checked ? "team" : "auto");

function syncTeamSwitch() {
  const id = state.conversationId;
  $("teamMode").checked = id ? state.kinds[id] === "team" : localGet("team_mode") === "1";
  $("teamMode").disabled = !!id;
}
$("teamMode").onchange = () => localSet("team_mode", $("teamMode").checked ? "1" : "0");

function setAwaiting(awaiting) {
  state.awaiting = awaiting;
  $("input").placeholder = t(awaiting ? "answerPlaceholder" : "placeholder");
}

function markPending(el) {
  el.classList.add("pending");
  const note = document.createElement("div");
  note.className = "pending-note";
  note.dataset.i18n = "waitingForYou";
  note.textContent = t("waitingForYou");
  el.append(note);
  setAwaiting(true);
}

function agentBadge(route, step = null) {
  const badge = document.createElement("span");
  badge.className = "agent-badge";
  badge.dataset.agent = route;
  if (step) badge.dataset.step = step;
  badge.textContent = badgeLabel(badge);
  return badge;
}

function i18nSpan(tag, key) {
  const node = document.createElement(tag);
  node.dataset.i18n = key;
  node.textContent = t(key);
  return node;
}

const el = (tag, cls, text) => Object.assign(document.createElement(tag), { className: cls, textContent: text ?? "" });

// ---------- status line and activity popup ----------
// Each reply has a status line (spinner and badges while it runs, a summary after) and an Activity button.
// `act` is what the popup shows, kept in memory per reply: {team, plan: [{id, agent, goal, depends_on, origin}],
// waves: [{wave, kind, tasks, agents, answers: [{task, agent, text, tools: [{name, args, result}]}], review}],
// drawn: [same answers, tasks outside every wave], asked: [same answers without a task: specialists the orchestrator asked], synthesis, tools: [], question}. A reload builds it
// from `message.team` (the task graph's workspace or the older rounds/boards) or the tool messages.
function newTurn(team = false) {
  const turn = {
    el: el("div", "status-line"), badges: [], running: true, step: {}, opener: null,
    act: { team, plan: [], waves: [], tools: [], drawn: [], asked: [], synthesis: null, question: null },
  };
  turn.el.turn = turn;
  turn.head = el("span");
  turn.names = el("span", "status-names");
  turn.tail = el("span");
  turn.spin = el("span", "spinner");
  turn.btn = el("button", "ghost activity-btn");
  turn.btn.type = "button";
  turn.btn.setAttribute("aria-haspopup", "dialog");
  turn.btn.onclick = () => openActivity(turn);
  turn.el.append(turn.spin, turn.head, turn.names, turn.tail, turn.btn);
  $("messages").append(turn.el);
  renderStatus(turn);
  return turn;
}

const stepCount = (act) => act.waves.reduce((n, r) => n + r.answers.reduce((m, a) => m + 1 + a.tools.length, 0), 0)
  + [...act.drawn, ...act.asked].reduce((n, d) => n + 1 + d.tools.length, 0) + act.tools.length + (act.question ? 1 : 0);

const waveTitle = (w) => `${t("waveN").replace("{n}", w.wave)} · ${t({ conflict: "kindConflict", challenge: "kindChallenge" }[w.kind] || "kindAnswer")}`;

// Waves the plan needs so far: its longest dependency chain, at least the waves already run.
function waveTotal(act) {
  const depth = {};
  const of = (task, seen = []) => depth[task.id] ??= 1 + Math.max(0, ...task.depends_on.map((d) => {
    const dep = act.plan.find((x) => x.id === d);
    return dep && !seen.includes(d) ? of(dep, [...seen, task.id]) : 0;
  }));
  return Math.max(act.waves.length, ...act.plan.filter((x) => !x.depends_on.includes("answer")).map((x) => of(x)));
}

const planAgent = (act, id) => agentLabel(act.plan.find((x) => x.id === id)?.agent ?? id);

// One line for a review: who questions whom, and the follow-up tasks it adds.
function reviewNote(act, review) {
  return [...(review.challenges || []).map((c) => t("questions").replace("{by}", planAgent(act, c.by_task)).replace("{to}", planAgent(act, c.to_task))),
    ...(review.follow_ups || []).map((f) => `+ ${agentLabel(f.agent)}: ${f.goal}`)].join(" · ");
}
function renderStatus(turn) {
  const { act, step } = turn;
  let head = "";
  let tail = "";
  let names = turn.badges;
  if (turn.running) {
    if (step.wave) {
      head = t("waveOf").replace("{n}", step.wave.wave).replace("{max}", waveTotal(act));
      names = step.wave.agents.map((agent) => ({ agent }));
    } else if (step.note) head = step.note;
    if (step.writing) head = t("writing");
    else if (step.tool) head = t("usingTool").replace("{name}", step.tool);
  } else if (act.team && act.waves.length) {
    const n = act.waves.length;
    head = n === 1 ? t("teamSummaryOne") : t("teamSummary").replace("{n}", n);
    const resolved = act.waves.reduce((m, w) => m + (w.review?.outcomes?.length || 0), 0);
    const diffs = act.waves.reduce((m, w) => m + (w.review?.differences.length || 0), 0);
    if (resolved) tail = t(resolved === 1 ? "resolvedOne" : "resolvedMany").replace("{n}", resolved);
    else if (diffs) tail = t(diffs === 1 ? "differenceOne" : "differenceMany").replace("{n}", diffs);
  }
  turn.head.textContent = head;
  turn.tail.textContent = tail;
  turn.names.replaceChildren(...names.map((b) => agentBadge(b.agent, b.step)));
  turn.spin.hidden = !turn.running;
  turn.el.hidden = !turn.running && !head && !tail && !names.length;
  const n = stepCount(act);
  turn.btn.hidden = !n;
  turn.btn.textContent = `${t("activity")} (${n})`;
}

let activityTurn = null; // the reply the popup shows
let activityFrame = 0;

// Call after every change to a reply's activity: the status line now, the popup (if it shows this reply) next frame.
function touch(turn) {
  renderStatus(turn);
  if (activityTurn !== turn || !$("activityDialog").open || activityFrame) return;
  activityFrame = requestAnimationFrame(() => { activityFrame = 0; renderActivity(); });
}

// The reply is over: the spinner goes and the Activity button moves below the answer.
function finishTurn(turn) {
  turn.running = false;
  turn.step = {};
  touch(turn);
  if (!turn.btn.hidden) $("messages").append(turn.btn);
}

// A tool result closes the oldest call of that name still waiting for one.
function closeTool(tools, name, content) {
  const tool = tools.find((x) => x.name === name && x.result === null) || tools[tools.push({ name, args: {}, result: null }) - 1];
  tool.result = content;
  return tool;
}

function toolBlock(parent, { name, args, result }) {
  const details = document.createElement("details");
  details.className = "tool";
  const summary = document.createElement("summary");
  const shown = args.query ?? args.sql ?? args.title ?? args.urls ?? args.name ?? args.fact ?? JSON.stringify(args);
  summary.textContent = `${t("usedTool")}: ${name} — ${String(shown).slice(0, 120)}`;
  const pre = document.createElement("pre");
  pre.textContent = typeof args.query === "string" ? args.query : JSON.stringify(args, null, 2);
  details.append(summary, pre);
  parent.append(details);
  if (result === null) return;
  const out = document.createElement("details");
  out.className = "tool";
  const body = document.createElement("pre");
  body.textContent = result;
  out.append(el("summary", "", `${t("result")}: ${name}`), body);
  parent.append(out);
}

function boardBlock(b, act) {
  const div = el("div", "act-board");
  div.append(el("strong", "", t("boardTitle")));
  if (b.forced) div.append(i18nSpan("em", "forcedStop"));
  const routes = (list) => list.map(agentLabel).join(", ");
  const section = (title, lines) => {
    if (!lines.length) return;
    const ul = document.createElement("ul");
    for (const line of lines) ul.append(el("li", "", line));
    div.append(el("div", "act-label", title), ul);
  };
  section(t("agreements"), b.agreements);
  section(t("differences"), b.differences.map((d) => `${routes(d.routes)}: ${d.explanation}`
    + (d.sides || []).map((x) => ` [${agentLabel(x.route)}: ${x.value}, ${x.source}, ${x.as_of}]`).join("")));
  section(t("challenges"), (b.challenges || []).map((c) => `${planAgent(act, c.by_task)} -> ${planAgent(act, c.to_task)}: ${c.question}`));
  section(t("outcomes"), (b.outcomes || []).map((o) => `${planAgent(act, o.task)}: ${t(o.revised ? "revised" : "stands")}`));
  section(t("followUps"), (b.follow_ups || []).map((f) => `${agentLabel(f.agent)}: ${f.goal}`));
  section(t("conflicts"), b.conflicts.map((c) => `${routes(c.routes)}: ${c.claim}${c.issue ? ` — ${c.issue}` : ""}`
    + (c.fixable_by.length ? ` (${t("fixableBy")} ${routes(c.fixable_by)})` : "")));
  return div;
}

function renderActivity() {
  const act = activityTurn.act;
  const body = $("activityBody");
  body.replaceChildren();
  if (act.plan.length) {
    const sec = el("section", "act-round");
    sec.append(el("h3", "", t("plan")));
    const ul = document.createElement("ul");
    for (const x of act.plan) {
      const mark = { follow_up: t("followUpMark"), reply: t("replyMark") }[x.origin];
      ul.append(el("li", "", `${x.id} ${agentLabel(x.agent)}: ${x.goal}${x.depends_on.length ? ` ← ${x.depends_on.join(", ")}` : ""}${mark ? ` (${mark})` : ""}`));
    }
    sec.append(ul);
    body.append(sec);
  }
  for (const r of act.waves) {
    const sec = el("section", "act-round");
    sec.append(el("h3", "", waveTitle(r)));
    for (const a of r.answers) {
      const div = el("div", "act-agent");
      div.append(agentBadge(a.agent));
      if (a.text) {
        const text = el("div", "act-text");
        text.innerHTML = renderMarkdown(a.text);
        div.append(text);
      }
      for (const tool of a.tools) toolBlock(div, tool);
      sec.append(div);
    }
    if (r.review) sec.append(boardBlock(r.review, act));
    body.append(sec);
  }
  if (act.drawn.length) {
    const sec = el("section", "act-round");
    sec.append(el("h3", "", t("drawing")));
    for (const d of act.drawn) {
      const div = el("div", "act-agent");
      div.append(agentBadge(d.agent));
      if (d.text) {
        const text = el("div", "act-text");
        text.innerHTML = renderMarkdown(d.text);
        div.append(text);
      }
      for (const tool of d.tools) toolBlock(div, tool);
      sec.append(div);
    }
    body.append(sec);
  }
  if (act.asked.length) {
    const sec = el("section", "act-round");
    for (const a of act.asked) {
      const div = el("div", "act-agent");
      div.append(agentBadge(a.agent));
      for (const tool of a.tools) toolBlock(div, tool);
      sec.append(div);
    }
    body.append(sec);
  }
  if (act.tools.length) {
    const sec = el("section", "act-round");
    if (act.waves.length) sec.append(el("h3", "", t("otherSteps")));
    for (const tool of act.tools) toolBlock(sec, tool);
    body.append(sec);
  }
  const open = act.synthesis ? [...act.synthesis.open_points, ...act.synthesis.not_done] : [];
  if (open.length) {
    const sec = el("section", "act-round");
    const ul = document.createElement("ul");
    for (const line of open) ul.append(el("li", "", typeof line === "string" ? line : JSON.stringify(line)));
    sec.append(el("h3", "", t("openPoints")), ul);
    body.append(sec);
  }
  if (act.question) {
    const sec = el("section", "act-round");
    sec.append(el("h3", "", t("yourQuestion")), el("p", "", act.question));
    body.append(sec);
  }
}

function openActivity(turn) {
  activityTurn = turn;
  turn.opener = turn.btn;
  renderActivity();
  $("activityDialog").showModal();
}
$("activityDialog").addEventListener("close", () => activityTurn?.opener?.focus());

// A finished team reply's transcript (message.team) as popup data. The task graph's workspace keeps
// `history` (waves, task results, reviews in order); the older round table kept `rounds` and `boards`.
function teamActivity(team) {
  const act = { team: true, plan: [], waves: [], tools: [], drawn: [], asked: [], synthesis: team.synthesis || null, question: null };
  if (!team.history) {
    act.drawn = team.drawn || [];
    act.waves = (team.rounds || []).map((r) => ({
      wave: r.round, kind: r.kind, tasks: [], agents: r.answers.map((a) => a.agent), answers: r.answers,
      review: (team.boards || []).find((b) => b.round === r.round) || null,
    }));
    return act;
  }
  act.plan = (team.tasks || []).map(({ id, agent, goal, depends_on, origin }) => ({ id, agent, goal, depends_on, origin }));
  for (const e of team.history) {
    const last = act.waves.at(-1);
    if (e.type === "wave") act.waves.push({ ...e, answers: [], review: null });
    else if (e.type === "review" && last) last.review = e;
    else if (e.type === "result") {
      const answer = { task: e.task, agent: e.agent, text: e.text, tools: e.tools || [] };
      (last?.tasks.includes(e.task) ? last.answers : act.drawn).push(answer);
    }
  }
  return act;
}

// ---------- readme ----------
$("readmeBtn").onclick = () => {
  loadMarketStatus();
  $("readmeDialog").showModal();
};

// ---------- usage ----------
// Tokens and cost of all this account's chats (/api/usage), in the sidebar's bottom box.
async function loadUsage() {
  const usage = await api("/usage").catch(() => null);
  if (!usage) return;
  const locale = state.lang === "vi" ? "vi-VN" : "en-US";
  const n = (x) => x.toLocaleString(locale);
  $("usageTokens").textContent = t("usageTokens").replace("{n}", n(usage.input_tokens + usage.output_tokens));
  $("usageTokens").title = t("usageDetail").replace("{input}", n(usage.input_tokens)).replace("{output}", n(usage.output_tokens));
  $("usageCost").textContent = t("usageCost").replace("{cost}", usage.cost.toFixed(usage.cost < 0.01 ? 6 : 4)); // one call costs ~$0.000005
}

// Readme examples go into the message box, ready to send or edit.
$("readmeDialog").addEventListener("click", (e) => {
  const example = e.target.closest(".example");
  if (!example) return;
  $("readmeDialog").close();
  $("input").value = example.textContent;
  autoSize();
  $("input").focus();
});

async function loadMarketStatus() {
  const status = await api("/market/status").catch(() => null);
  let text = !status ? "" : status.last_trade_date
    ? t("marketStatus").replace("{stocks}", status.stocks).replace("{date}", status.last_trade_date)
    : t("marketEmpty");
  if (status?.updated_at) {
    const when = new Date(status.updated_at).toLocaleString(state.lang === "vi" ? "vi-VN" : "en-US", {
      dateStyle: "medium", timeStyle: "short",
    });
    text += " " + t("marketUpdated").replace("{time}", when);
  }
  document.querySelectorAll(".market-status").forEach((el) => (el.textContent = text));
}

// Per user, so someone else logging in on this browser doesn't reopen your chat.
const conversationKey = () => `conversation_${KIND}_${state.me?.user_id}`;

function setConversation(id) {
  state.conversationId = id;
  if (state.me) localSet(conversationKey(), id || "");
  syncTeamSwitch();
}

function setBusy(busy) {
  state.busy = busy;
  $("sendBtn").disabled = busy;
  $("newChatBtn").disabled = busy;
}

// ---------- conversations ----------
async function loadConversations() {
  const list = $("conversationList");
  const items = await api("/conversations").catch(() => []);
  state.kinds = Object.fromEntries(items.map((c) => [c.id, c.kind]));
  syncTeamSwitch();
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = `<li class="muted">${t("noConversations")}</li>`;
    return;
  }
  for (const c of items) {
    const li = document.createElement("li");
    li.classList.toggle("active", c.id === state.conversationId);
    const title = document.createElement("span");
    title.className = "title";
    title.textContent = c.title;
    title.title = c.title;
    const del = document.createElement("button");
    del.className = "icon-btn";
    del.textContent = "×";
    del.title = t("delete");
    del.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(t("confirmDelete"))) return;
      await api(`/conversations/${c.id}`, { method: "DELETE" });
      if (c.id === state.conversationId) newChat();
      loadConversations();
    };
    if (c.kind === "team") {
      const icon = document.createElement("span");
      icon.textContent = "\u{1F465}";
      icon.title = t("teamMode");
      li.append(icon);
    }
    li.append(title, del);
    li.onclick = () => openConversation(c.id);
    list.append(li);
  }
}

async function openConversation(id) {
  if (state.busy) return;
  setConversation(id);
  setAwaiting(false);
  $("messages").innerHTML = "";
  let messages;
  try {
    messages = await api(`/conversations/${id}/messages`);
  } catch (err) {
    if (state.conversationId !== id) return;
    if (err.message === "Conversation not found") return newChat();
    addError(`${t("loadFailed")}: ${err.message}`);
    loadConversations();
    return;
  }
  if (state.conversationId !== id) return; // user switched away while loading
  let agent = null; // agent badge already shown for the current turn
  let turn = null; // the current turn's status line, created with its first reply
  const end = () => { if (turn) finishTurn(turn); turn = null; };
  for (const m of messages) {
    if (m.role === "user") { end(); addUserMessage(m.content); agent = null; continue; }
    turn ??= newTurn(!!m.team);
    if (m.role === "assistant") {
      if (m.team) {
        turn.act = teamActivity(m.team);
        const agents = m.team.participants || (m.team.tasks || []).map((x) => x.agent);
        turn.badges = [...new Set(agents)].map((a) => ({ agent: a }));
      } else if (m.agent && m.agent !== agent) turn.badges.push({ agent: (agent = m.agent) });
      for (const call of m.tool_calls || []) turn.act.tools.push({ name: call.name, args: call.args, result: null });
      addCharts(m.charts); // an orchestrator answer's charts, shown above it as when it streamed
      if (m.content) {
        const msg = addAssistantMessage();
        msg.update(m.content);
        if (m.pending) { markPending(msg.el); turn.act.question = m.content; }
      }
    } else if (m.role === "tool") {
      closeTool(turn.act.tools, m.name, m.content);
      addCharts(m.charts);
    }
  }
  end();
  loadConversations();
}

function newChat() {
  setConversation(null);
  setAwaiting(false);
  showWelcome();
  loadConversations();
}
$("newChatBtn").onclick = () => { if (!state.busy) newChat(); };

function showWelcome() {
  $("messages").innerHTML = `<p class="welcome">${t("welcome")}</p>`;
}

// ---------- messages ----------
function scrollToBottom() {
  const box = $("messages");
  box.scrollTop = box.scrollHeight;
}

function addUserMessage(text) {
  $("messages").querySelector(".welcome")?.remove();
  const div = document.createElement("div");
  div.className = "msg user";
  div.textContent = text;
  $("messages").append(div);
  scrollToBottom();
}

function addAssistantMessage() {
  const div = document.createElement("div");
  div.className = "msg assistant";
  $("messages").append(div);
  let text = "";
  const render = () => { div.innerHTML = renderMarkdown(text); linkCitations(div); scrollToBottom(); };
  return {
    append(chunk) { text += chunk; render(); },
    update(full) { text = full; render(); },
    isEmpty: () => !text,
    remove: () => div.remove(),
    el: div,
  };
}

// Charts a tool drew (live, or replayed from history), as cards in the message flow.
function addCharts(charts, before = null) {
  for (const spec of charts || []) {
    const card = chartCard(spec);
    if (card && before) before.before(card);
    else if (card) $("messages").append(card);
  }
  scrollToBottom();
}

function addError(message, code) {
  const div = document.createElement("div");
  div.className = "msg error";
  div.textContent = `⚠ ${message}`;
  if (code === "no_model") {
    const open = document.createElement("button");
    open.type = "button";
    open.className = "link-btn";
    open.textContent = t("openSettings");
    open.onclick = openSettings;
    div.append(" ", open);
  }
  $("messages").append(div);
  scrollToBottom();
}

// Removes the assistant's text replies since the latest user message.
function dropReplies() {
  let el = [...$("messages").querySelectorAll(".msg.user")].pop()?.nextElementSibling;
  while (el) {
    const next = el.nextElementSibling;
    if (el.matches(".msg.assistant")) el.remove();
    el = next;
  }
}

// ---------- chat ----------
async function sendMessage(text) {
  setBusy(true);
  setAwaiting(false);
  const kind = chatKind();
  addUserMessage(text);
  const turn = newTurn(kind === "team");
  const { act } = turn;
  let current = addAssistantMessage();
  current.update(`_${t("thinking")}_`);
  let started = false;
  let finished = false;
  let waiting = false; // showing "thinking" after a fallback
  let synthesized = false; // the team's write-up started
  // A task's answer: in the latest wave that ran it, else (a chart task after the write-up) in `drawn`.
  const answerOf = (agent, task) => {
    const wave = task && act.waves.findLast((w) => w.tasks.includes(task));
    const answers = wave ? wave.answers : task ? act.drawn : act.asked; // no task: a specialist the orchestrator asked
    return answers.find((a) => a.task === task && a.agent === agent) || answers[answers.push({ task, agent, text: "", tools: [] }) - 1];
  };
  const addBadges = (agents) => {
    for (const agent of agents) if (!turn.badges.some((b) => b.agent === agent)) turn.badges.push({ agent });
  };

  try {
    const res = await fetch(`/api/chat/${kind}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, conversation_id: state.conversationId }),
    });
    if (res.status === 401) return showLogin();
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop();
      for (const raw of events) {
        if (!raw.startsWith("data: ")) continue;
        try { // one bad event is skipped; a network or reader error still ends the stream below
        const event = JSON.parse(raw.slice(6));
        switch (event.type) {
          case "conversation":
            state.kinds[event.id] = kind;
            setConversation(event.id);
            break;
          case "agent":
            if (act.team || event.name === "team") break; // the plan names its own agents
            // The orchestrator handed off before showing anything: relabel its badge, as a reload would show it.
            if (turn.badges.length && !started) turn.badges[turn.badges.length - 1] = { agent: event.name };
            else turn.badges.push({ agent: event.name });
            touch(turn);
            break;
          case "step": {
            const next = { agent: event.agent, step: event.name };
            if (stepLabel(next.agent, next.step) !== agentLabel(turn.badges.at(-1)?.agent)) turn.badges.push(next);
            touch(turn);
            break;
          }
          case "fallback":
            // The agent found no answer: drop its reply (its tool calls stay) while the assistant takes over.
            dropReplies();
            current = addAssistantMessage();
            current.update(`_${t("thinking")}_`);
            started = waiting = true; // the next `agent` event adds the assistant's own badge
            break;
          case "plan":
            act.plan = event.tasks;
            addBadges(event.tasks.map((x) => x.agent));
            touch(turn);
            break;
          case "plan_updated":
            act.plan.push(...event.tasks);
            addBadges(event.tasks.map((x) => x.agent));
            touch(turn);
            break;
          case "wave": {
            const wave = { wave: event.wave, kind: event.kind, tasks: event.tasks, agents: event.agents, answers: [], review: null };
            act.waves.push(wave);
            turn.step = { wave };
            addBadges(event.agents);
            touch(turn);
            break;
          }
          case "review": {
            const wave = act.waves.at(-1);
            if (wave) wave.review = event;
            turn.step = { note: reviewNote(act, event) };
            touch(turn);
            break;
          }
          case "spoke": { // the task's final entry: what a reload shows
            const mine = answerOf(event.agent, event.task);
            mine.text = event.text;
            mine.tools = event.tools.map((x, i) => ({ ...x, charts: mine.tools?.[i]?.name === x.name ? mine.tools[i].charts : undefined }));
            touch(turn);
            break;
          }
          case "synthesize":
            synthesized = true;
            turn.step = { writing: true };
            touch(turn);
            break;
          case "charts": // the team's final answer charts, drawn after the write-up; deduped by chart id
            addCharts(event.charts, current.el);
            break;
          case "needs_human":
            if (!started || waiting) { current.update(""); started = true; waiting = false; }
            current.update(event.question);
            markPending(current.el);
            act.question = event.question;
            touch(turn);
            break;
          case "done":
            finished = true;
            if (act.team && !started && !synthesized) {
              // One participant and no conflict: its round answer is the reply, so no write-up came.
              const only = act.waves[0]?.answers[0];
              if (only?.text) {
                addCharts(only.tools.flatMap((x) => x.charts || []), current.el);
                current.update(only.text);
                started = true;
              }
            }
            break;
          case "token":
            if (event.agent) { answerOf(event.agent, event.task).text += event.text; touch(turn); break; } // a task's draft: popup only
            if (!started || waiting) { current.update(""); started = true; waiting = false; }
            current.append(event.text);
            break;
          case "tool_call":
            if (event.agent) { answerOf(event.agent, event.task).tools.push({ name: event.name, args: event.args, result: null }); touch(turn); break; }
            if (!started || waiting) current.remove();
            waiting = false;
            act.tools.push({ name: event.name, args: event.args, result: null });
            turn.step = { tool: event.name };
            touch(turn);
            current = addAssistantMessage();
            started = true;
            break;
          case "tool_result":
            if (event.agent) { // its charts stay out of the answer
              const { tools } = answerOf(event.agent, event.task);
              closeTool(tools, event.name, event.content).charts = event.charts;
              touch(turn);
              break;
            }
            closeTool(act.tools, event.name, event.content);
            turn.step = {};
            touch(turn);
            addCharts(event.charts);
            current = addAssistantMessage();
            break;
          case "error":
            addError(event.message, event.code);
            break;
        }
        } catch (err) {
          console.warn("Skipped a bad stream event", raw, err);
        }
      }
    }
    if (!finished) addError(t("streamCut"));
  } catch (err) {
    addError(err.message);
  } finally {
    if (!started || waiting) current.remove();
    finishTurn(turn);
    document.querySelectorAll(".msg.assistant:empty").forEach((el) => el.remove());
    setBusy(false);
    loadConversations();
    loadUsage();
  }
}

$("chatForm").onsubmit = (e) => {
  e.preventDefault();
  const text = $("input").value.trim();
  if (!text || state.busy) return;
  $("input").value = "";
  autoSize();
  sendMessage(text);
};

$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("chatForm").requestSubmit();
  }
});

function autoSize() {
  const el = $("input");
  el.style.height = "auto";
  el.style.height = `${el.scrollHeight}px`;
}
$("input").addEventListener("input", autoSize);

// ---------- documents ----------
async function loadDocuments() {
  const docs = await api("/documents").catch(() => []);
  const list = $("documentList");
  list.innerHTML = docs.length ? "" : `<li class="muted" style="border:none">${t("noDocuments")}</li>`;
  for (const d of docs) {
    const li = document.createElement("li");
    li.title = d.error || "";
    const name = document.createElement("span");
    name.textContent = d.filename;
    const badge = document.createElement("span");
    badge.className = `badge ${d.status}`;
    badge.textContent = t(`status_${d.status}`) + (d.status === "ready" ? ` · ${d.chunk_count} ${t("chunks")}` : "");
    const del = document.createElement("button");
    del.className = "icon-btn";
    del.textContent = "×";
    del.disabled = d.status === "processing";
    del.onclick = async () => {
      if (!confirm(t("confirmDelete"))) return;
      await api(`/documents/${d.id}`, { method: "DELETE" }).catch((err) => alert(err.message));
      loadDocuments();
    };
    li.append(name, badge, del);
    list.append(li);
  }
  clearTimeout(state.docsPoll);
  if (docs.some((d) => d.status === "processing")) state.docsPoll = setTimeout(loadDocuments, 2000);
}

$("fileInput").onchange = async (e) => {
  const files = [...e.target.files];
  if (!files.length) return;
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  await api("/documents", { method: "POST", body: form }).catch((err) => alert(err.message));
  e.target.value = "";
  loadDocuments();
};

// ---------- accounts (admin) ----------
async function loadAccounts() {
  const list = $("accountList");
  const items = await api("/admin/accounts").catch((err) => { alert(err.message); return []; });
  const used = items.filter((a) => !a.is_admin).length;
  $("accountCount").textContent = t("accountCount").replace("{count}", used).replace("{max}", state.me.max_accounts);
  list.innerHTML = "";
  for (const a of items) {
    const li = document.createElement("li");
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = `${a.name || a.username} (@${a.username})` + (a.is_admin ? ` · ${t("adminBadge")}` : "");
    li.append(who);
    if (!a.is_admin) {
      const actions = document.createElement("span");
      actions.className = "row-actions";
      const reset = document.createElement("button");
      reset.className = "ghost";
      reset.textContent = t("resetPassword");
      reset.onclick = async () => {
        const password = prompt(t("newPassword").replace("{user}", a.username));
        if (password === null) return;
        if (password.length < 8) return alert(t("passwordHint"));
        try {
          await api(`/admin/accounts/${a.id}/password`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password }),
          });
          alert(t("passwordChanged"));
        } catch (err) {
          alert(err.message);
        }
      };
      const del = document.createElement("button");
      del.className = "ghost";
      del.textContent = t("delete");
      del.onclick = async () => {
        if (!confirm(t("confirmDeleteAccount").replace("{user}", a.username))) return;
        await api(`/admin/accounts/${a.id}`, { method: "DELETE" }).catch((err) => alert(err.message));
        loadAccounts();
      };
      actions.append(reset, del);
      li.append(actions);
    }
    list.append(li);
  }
}

$("adminBtn").onclick = () => {
  loadAccounts();
  $("adminDialog").showModal();
};

// ---------- model settings ----------
function setSettingsStatus(text) {
  $("settingsStatus").textContent = text;
}

async function loadSettings() {
  const s = await api("/model-settings").catch(() => null); // 404 = nothing saved yet
  $("settingsKey").value = "";
  $("settingsKey").placeholder = s?.key_hint || "";
  $("settingsUrl").value = s?.base_url || "";
  $("settingsModel").value = s?.model || "";
  setSettingsStatus(s ? t("settingsKeySaved") : t("settingsNone"));
}

function openSettings() {
  loadSettings();
  $("settingsDialog").showModal();
}

async function saveSettings() {
  const key = $("settingsKey").value.trim();
  const base_url = $("settingsUrl").value.trim();
  const model = $("settingsModel").value.trim();
  if (!base_url || !model || (!key && !$("settingsKey").placeholder)) return;
  try {
    const res = await api("/model-settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ base_url, model, api_key: key || null }),
    });
    await loadSettings();
    setSettingsStatus(res.warning ? `${t("saved")}. ${res.warning}` : t("saved"));
  } catch (err) {
    setSettingsStatus(err.message);
  }
}

["settingsKey", "settingsUrl", "settingsModel"].forEach((id) => $(id).addEventListener("change", saveSettings));
$("settingsBtn").onclick = openSettings;
$("settingsClose").onclick = () => $("settingsDialog").close();
$("settingsClear").onclick = async () => {
  if (!confirm(t("confirmClearSettings"))) return;
  try {
    await api("/model-settings", { method: "DELETE" });
    await loadSettings();
  } catch (err) {
    setSettingsStatus(err.message);
  }
};

// ---------- login ----------
function showLogin() {
  state.me = null;
  state.conversationId = null;
  clearTimeout(state.docsPoll);
  ["messages", "conversationList", "documentList"].forEach((id) => ($(id).innerHTML = ""));
  document.querySelectorAll("dialog[open]").forEach((d) => d.close());
  document.body.classList.add("logged-out");
  $("appView").hidden = true;
  $("logoutBtn").hidden = true;
  $("adminBtn").hidden = true;
  $("userName").hidden = true;
  $("loginView").hidden = false;
  showLoginForm(false);
}

function showLoginForm(signup) {
  $("loginForm").hidden = signup;
  $("signupForm").hidden = !signup;
  $("loginError").hidden = true;
  $("signupError").hidden = true;
  (signup ? $("signupName") : $("loginUsername")).focus();
}
$("showSignupBtn").onclick = () => showLoginForm(true);
$("showLoginBtn").onclick = () => showLoginForm(false);

function showApp() {
  document.body.classList.remove("logged-out");
  $("loginView").hidden = true;
  $("appView").hidden = false;
  $("logoutBtn").hidden = false;
  $("adminBtn").hidden = !state.me.is_admin;
  $("userName").hidden = false;
  $("userName").textContent = state.me.name;
  $("userName").title = `@${state.me.username}`;
  // Reopen the last chat instead of starting over.
  const saved = localGet(conversationKey());
  if (saved) openConversation(saved);
  else newChat();
  loadDocuments();
  loadAgents();
  loadUsage();
}

function postJson(path, body) {
  return fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

$("loginForm").onsubmit = async (e) => {
  e.preventDefault();
  $("loginError").hidden = true;
  const res = await postJson("/login", { username: $("loginUsername").value, password: $("password").value });
  if (!res.ok) {
    $("loginError").hidden = false;
    return;
  }
  $("password").value = "";
  start();
};

$("signupForm").onsubmit = async (e) => {
  e.preventDefault();
  $("signupError").hidden = true;
  const res = await postJson("/signup", {
    name: $("signupName").value,
    username: $("signupUsername").value,
    password: $("signupPassword").value,
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail;
    $("signupError").textContent = signupError(res.status, detail);
    $("signupError").hidden = false;
    return;
  }
  $("signupForm").reset();
  start();
};

function signupError(status, detail) {
  if (status === 409) return t("err_taken");
  if (status === 403) return t("err_limit");
  if (Array.isArray(detail)) { // validation errors from the server
    const field = detail[0]?.loc?.at(-1);
    if (field === "username") return String(detail[0].msg).includes("reserved") ? t("err_reserved") : t("usernameHint");
    if (field === "password") return t("passwordHint");
    return detail[0]?.msg || "Error";
  }
  return detail || "Error";
}

$("logoutBtn").onclick = async () => {
  if (state.busy) return;
  await fetch("/api/logout", { method: "POST" });
  showLogin();
};

// ---------- init ----------
async function start() {
  applyLanguage();
  const res = await fetch("/api/me");
  if (res.status === 401) return showLogin();
  state.me = await res.json();
  showApp();
}

start();
