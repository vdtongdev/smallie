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
  document.querySelectorAll(".team-discussion").forEach(labelTeamSummary);
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

function labelTeamSummary(details) {
  const n = Number(details.dataset.rounds);
  details.firstChild.textContent = n === 1 ? t("teamDiscussionOne") : t("teamDiscussion").replace("{n}", n);
}

function agentBadge(route) {
  const badge = document.createElement("span");
  badge.className = "agent-badge";
  badge.dataset.agent = route;
  badge.textContent = badgeLabel(badge);
  return badge;
}

function i18nSpan(tag, key) {
  const el = document.createElement(tag);
  el.dataset.i18n = key;
  el.textContent = t(key);
  return el;
}

// The collapsible discussion above a team reply: `entry` adds a phase block, `verdict` a line between blocks.
// Placed before `before`, or at the end of the chat.
function addTeamDiscussion(before = null) {
  const details = document.createElement("details");
  details.className = "team-discussion";
  details.open = true;
  details.dataset.rounds = 0;
  details.append(document.createElement("summary"));
  if (before) before.before(details);
  else $("messages").append(details);
  labelTeamSummary(details);
  return {
    el: details,
    rounds(n) { details.dataset.rounds = n; labelTeamSummary(details); },
    entry(agent, phase) {
      const div = document.createElement("div");
      div.className = "team-entry";
      const head = document.createElement("div");
      head.className = "team-head";
      head.append(agentBadge(agent), i18nSpan("span", "phase" + phase[0].toUpperCase() + phase.slice(1)));
      const body = document.createElement("div");
      body.className = "team-text";
      div.append(head, body);
      details.append(div);
      let text = "";
      return {
        append(chunk) { text += chunk; body.innerHTML = renderMarkdown(text); },
        update(full) { text = ""; this.append(full); },
      };
    },
    verdict(v) {
      const div = document.createElement("div");
      div.className = "team-verdict";
      const key = "verdict" + v.action.split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join("");
      div.append(i18nSpan("strong", key));
      if (v.critic) div.append(agentBadge(v.critic));
      if (v.forced) div.append(i18nSpan("em", "forcedStop"));
      if (v.issues?.length) {
        const ul = document.createElement("ul");
        for (const issue of v.issues) ul.append(Object.assign(document.createElement("li"), { textContent: issue }));
        div.append(ul);
      }
      details.append(div);
    },
  };
}

// Rebuilds a finished discussion from a message's `team` transcript (it keeps the final draft only).
function addTeamTranscript(team) {
  const section = addTeamDiscussion();
  section.entry(team.author, "delegate").update(team.draft || "");
  const rows = Math.max(team.verdicts.length, team.critiques.length);
  for (let i = 0; i < rows; i++) {
    if (team.verdicts[i]) section.verdict(team.verdicts[i]);
    const c = team.critiques[i];
    if (c) section.entry(c.critic, "critique").update(c.text);
  }
  section.rounds(team.turns);
  section.el.open = false;
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
  $("usageBox").hidden = false;
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
  for (const m of messages) {
    if (m.role === "user") { addUserMessage(m.content); agent = null; }
    else if (m.role === "assistant") {
      if (m.agent && m.agent !== agent) addAgentBadge((agent = m.agent));
      if (m.team) addTeamTranscript(m.team);
      for (const call of m.tool_calls || []) addToolCall(call.name, call.args);
      addCharts(m.charts); // an orchestrator answer's charts, shown above it as when it streamed
      if (m.content) {
        const msg = addAssistantMessage();
        msg.update(m.content);
        if (m.pending) markPending(msg.el);
      }
    } else if (m.role === "tool") addToolResult(m.name, m.content, m.charts);
  }
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

// `step`: a workflow handed over to another of its agents. Returns the badge (if one was added).
function addAgentBadge(name, before = null, step = null) {
  const div = document.createElement("div");
  div.className = "agent-badge";
  div.dataset.agent = name;
  if (step) div.dataset.step = step;
  div.textContent = badgeLabel(div);
  // A workflow's first agent usually has the same label as the turn's badge; don't repeat it.
  const last = [...$("messages").querySelectorAll(".agent-badge")].pop();
  if (step && last?.textContent === div.textContent) return null;
  if (before) before.before(div);
  else $("messages").append(div);
  return div;
}

function addToolCall(name, args) {
  const details = document.createElement("details");
  details.className = "tool";
  const summary = document.createElement("summary");
  const shown = args.query ?? args.sql ?? args.title ?? args.urls ?? args.name ?? args.fact ?? JSON.stringify(args);
  summary.textContent = `${t("usedTool")}: ${name} — ${String(shown).slice(0, 120)}`;
  const pre = document.createElement("pre");
  pre.textContent = typeof args.query === "string" ? args.query : JSON.stringify(args, null, 2);
  details.append(summary, pre);
  $("messages").append(details);
  scrollToBottom();
}

// Charts a tool drew (live, or replayed from history), as cards in the message flow.
function addCharts(charts) {
  for (const spec of charts || []) {
    const card = chartCard(spec);
    if (card) $("messages").append(card);
  }
  scrollToBottom();
}

function addToolResult(name, content, charts = []) {
  if (charts.length) return addCharts(charts); // the chart is the result; its summary is for the model
  const details = document.createElement("details");
  details.className = "tool";
  const summary = document.createElement("summary");
  summary.textContent = `${t("result")}: ${name}`;
  const pre = document.createElement("pre");
  pre.textContent = content;
  details.append(summary, pre);
  $("messages").append(details);
  scrollToBottom();
}

function addError(message) {
  const div = document.createElement("div");
  div.className = "msg error";
  div.textContent = `⚠ ${message}`;
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
  let current = addAssistantMessage();
  current.update(`_${t("thinking")}_`);
  let started = false;
  let finished = false;
  let badge = null; // this turn's agent badge
  let waiting = false; // showing "thinking" after a fallback
  let discussion = null; // this reply's team discussion section
  let entry = null; // the discussion block receiving tokens (null once the final answer starts)

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
        const event = JSON.parse(raw.slice(6));
        switch (event.type) {
          case "conversation":
            state.kinds[event.id] = kind;
            setConversation(event.id);
            break;
          case "agent":
            if (event.name === "team") break; // the team's phases name their own agents
            // The orchestrator handed off before showing anything: relabel its badge, as a reload would show it.
            if (badge && !started) { badge.dataset.agent = event.name; badge.textContent = badgeLabel(badge); }
            else badge = addAgentBadge(event.name, current.el);
            break;
          case "step":
            addAgentBadge(event.agent, current.el, event.name);
            break;
          case "fallback":
            // The agent found no answer: drop its reply (its tool calls stay) while the assistant takes over.
            dropReplies();
            current = addAssistantMessage();
            current.update(`_${t("thinking")}_`);
            started = waiting = true; // the next `agent` event adds the assistant's own badge
            break;
          case "team":
            if (event.phase === "synthesize") {
              entry = null;
              if (discussion) discussion.el.open = false;
              break;
            }
            discussion ??= addTeamDiscussion(current.el);
            entry = discussion.entry(event.agent, event.phase);
            discussion.rounds(event.phase === "delegate" ? 0 : event.turn);
            break;
          case "verdict":
            discussion ??= addTeamDiscussion(current.el);
            discussion.verdict(event);
            break;
          case "needs_human":
            if (!started || waiting) { current.update(""); started = true; waiting = false; }
            current.update(event.question);
            markPending(current.el);
            break;
          case "done":
            finished = true;
            break;
          case "token":
            if (entry) { entry.append(event.text); scrollToBottom(); break; }
            if (!started || waiting) { current.update(""); started = true; waiting = false; }
            current.append(event.text);
            break;
          case "tool_call":
            if (!started || waiting) current.remove();
            waiting = false;
            addToolCall(event.name, event.args);
            current = addAssistantMessage();
            started = true;
            break;
          case "tool_result":
            addToolResult(event.name, event.content, event.charts);
            current = addAssistantMessage();
            break;
          case "error":
            addError(event.message);
            break;
        }
      }
    }
    if (!finished) addError(t("streamCut"));
  } catch (err) {
    addError(err.message);
  } finally {
    if (!started || waiting) current.remove();
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
