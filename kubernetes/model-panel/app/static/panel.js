// model-panel UI (D9): ~single-purpose vanilla JS, no build step.
// State-driven: one poll loop drives every visible element from
// GET /api/status; the only user actions are POST /api/switch and
// POST /api/repair.

const TOKEN_KEY = "model_panel_token";
const POLL_INTERVAL_MS = 2000;

const el = (id) => document.getElementById(id);

function getToken() {
  return window.localStorage.getItem(TOKEN_KEY) || "";
}

function setToken(token) {
  window.localStorage.setItem(TOKEN_KEY, token);
}

function authHeaders() {
  return { Authorization: `Bearer ${getToken()}` };
}

async function apiGet(path) {
  const resp = await fetch(path, { headers: authHeaders() });
  return { status: resp.status, body: await safeJson(resp) };
}

async function apiPost(path, payload) {
  const resp = await fetch(path, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: payload ? JSON.stringify(payload) : undefined,
  });
  return { status: resp.status, body: await safeJson(resp) };
}

async function safeJson(resp) {
  try {
    return await resp.json();
  } catch (_e) {
    return null;
  }
}

function showTokenGate(show) {
  el("token-gate").hidden = !show;
  el("panel-content").hidden = show;
}

function render(status) {
  const modeBadge = el("mode-badge");
  modeBadge.textContent = `Mode: ${status.mode}${status.profile ? " (" + status.profile + ")" : ""}`;

  const sessionBadge = el("session-badge");
  const sessionState = status.session ? status.session.state : "not_configured";
  sessionBadge.textContent = `Codex session: ${sessionState}`;
  sessionBadge.className = "badge " + sessionStateClass(sessionState);

  const profileSelect = el("profile-select");
  const profilePicker = el("profile-picker");
  const isLocal = status.mode === "local";
  const transitioning = !!status.transitioning;
  profilePicker.hidden = !isLocal; // spec: hidden/disabled in Cloud mode
  if (isLocal && status.profile) {
    profileSelect.value = status.profile;
  }
  // D18/D18a: wired to POST /api/profile — disabled while a switch is in
  // progress or when not Local (picker is meaningless in Cloud mode).
  profileSelect.disabled = !isLocal || transitioning;

  const toggleBtn = el("toggle-btn");
  const sessionBlocksCloud = !["valid", "expiring_soon"].includes(sessionState);
  const wantsCloud = status.mode !== "cloud";
  toggleBtn.textContent = wantsCloud ? "Switch to Cloud" : "Switch to Local";
  toggleBtn.disabled = transitioning || (wantsCloud && sessionBlocksCloud);
  toggleBtn.dataset.target = wantsCloud ? "cloud" : "local";

  el("progress").hidden = !transitioning;

  const errorBanner = el("error-banner");
  if (status.phase === "degraded" && status.error) {
    errorBanner.hidden = false;
    errorBanner.textContent = `Degraded: ${status.error}`;
    el("repair-btn").hidden = false;
  } else {
    errorBanner.hidden = true;
    el("repair-btn").hidden = true;
  }
}

function sessionStateClass(state) {
  if (state === "valid") return "ok";
  if (state === "expiring_soon" || state === "rate_limited") return "warn";
  return "bad";
}

async function poll() {
  const { status, body } = await apiGet("/api/status");
  if (status === 401 || status === 503) {
    showTokenGate(true);
    return;
  }
  showTokenGate(false);
  if (body) render(body);
}

async function onProfileChange() {
  const select = el("profile-select");
  const profile = select.value;
  select.disabled = true;
  await apiPost("/api/profile", { profile });
  await poll();
}

async function onToggle() {
  const target = el("toggle-btn").dataset.target;
  el("toggle-btn").disabled = true;
  await apiPost("/api/switch", { target });
  await poll();
}

async function onRepair() {
  el("repair-btn").disabled = true;
  await apiPost("/api/repair");
  el("repair-btn").disabled = false;
  await poll();
}

function onSaveToken() {
  const value = el("token-input").value.trim();
  if (value) {
    setToken(value);
    el("token-input").value = "";
  }
  poll();
}

function init() {
  el("token-save").addEventListener("click", onSaveToken);
  el("toggle-btn").addEventListener("click", onToggle);
  el("repair-btn").addEventListener("click", onRepair);
  el("profile-select").addEventListener("change", onProfileChange);

  if (!getToken()) {
    showTokenGate(true);
  }
  poll();
  window.setInterval(poll, POLL_INTERVAL_MS);
}

document.addEventListener("DOMContentLoaded", init);
