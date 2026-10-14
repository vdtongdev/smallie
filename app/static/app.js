// "auto": the server picks one of its agents (GET /api/agents) for each message.
const KIND = "auto";

const state = {
  lang: localGet("lang") || "en",
  me: null, // {user_id, name, username, is_admin, max_accounts}
  conversationId: null,
  busy: false,
  docsPoll: null,
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
  return DOMPurify.sanitize(marked.parse(text || ""));
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
  $("input").placeholder = t("placeholder");
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
  if ($("readmeDialog").open) loadMarketStatus();
  if ($("adminDialog").open) loadAccounts();
  if (!state.conversationId) showWelcome();
};

// ---------- readme ----------
$("readmeBtn").onclick = () => {
  loadMarketStatus();
  $("readmeDialog").showModal();
};

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
    li.append(title, del);
    li.onclick = () => openConversation(c.id);
    list.append(li);
  }
}

async function openConversation(id) {
  if (state.busy) return;
  setConversation(id);
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
      for (const call of m.tool_calls || []) addToolCall(call.name, call.args);
      addCharts(m.charts); // an orchestrator answer's charts, shown above it as when it streamed
      if (m.content) addAssistantMessage().update(m.content);
    } else if (m.role === "tool") addToolResult(m.name, m.content, m.charts);
  }
  loadConversations();
}

function newChat() {
  setConversation(null);
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

// ---------- chat ----------
async function sendMessage(text) {
  setBusy(true);
  addUserMessage(text);
  let current = addAssistantMessage();
  current.update(`_${t("thinking")}_`);
  let started = false;
  let finished = false;
  let badge = null; // this turn's agent badge

  try {
    const res = await fetch(`/api/chat/${KIND}`, {
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
            setConversation(event.id);
            break;
          case "agent":
            // The orchestrator handed off before showing anything: relabel its badge, as a reload would show it.
            if (badge && !started) { badge.dataset.agent = event.name; badge.textContent = badgeLabel(badge); }
            else badge = addAgentBadge(event.name, current.el);
            break;
          case "step":
            addAgentBadge(event.agent, current.el, event.name);
            break;
          case "done":
            finished = true;
            break;
          case "token":
            if (!started) { current.update(""); started = true; }
            current.append(event.text);
            break;
          case "tool_call":
            if (!started) current.remove();
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
    if (!started) current.remove();
    document.querySelectorAll(".msg.assistant:empty").forEach((el) => el.remove());
    setBusy(false);
    loadConversations();
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
