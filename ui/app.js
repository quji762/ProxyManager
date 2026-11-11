"use strict";
/* ═══════════════ API bridge: real pywebview or browser mock ═══════════════ */
const HAS_BRIDGE = () => !!(window.pywebview && window.pywebview.api);
const api = (() => {
  const call = (name, ...args) => {
    if (HAS_BRIDGE()) return window.pywebview.api[name](...args);
    return mock(name, ...args);
  };

  /* Browser mock so the UI can be designed/tested standalone. */
  let mockState = {
    host: "127.0.0.1", port: "7897", scheme: "http", user: "", password: "", lang: "zh",
    mode: "light", pin: false,
    ps: true, wsl: false, gitwin: false, gitwsl: false, npm: false, pip: false,
    npmwsl: false, pipwsl: false, sys: false,
    ports: [7890, 7892, 7897], autostart: false,
    presets: [], backups: [],
    lip: "192.168.1.146", wip: "172.24.156.88", wmode: "Mirrored",
    psHttp: "http://127.0.0.1:7897",
    sysVal: "未设置",
    wslHttp: "未设置", wslGit: "未设置", gitWinVal: "未设置",
    npmVal: "未设置", pipVal: "未设置", npmWslVal: "未设置", pipWslVal: "未设置",
    alive: true, iface: "以太网",
    bg_path: "", bg_dim: 0.45, card_alpha: 0.92,
    // Mirrors the backend applied record (last values actually written) and
    // the WSL "last written URL" map, so boot()/badges behave like the real UI.
    appliedHost: "127.0.0.1", appliedPort: "7897", appliedScheme: "http",
    appliedUser: "", appliedPassword: "", wslWritten: {},
  };
  const delay = (ms) => new Promise(res => setTimeout(res, ms));
  async function mock(name, ...args) {
    await delay(name === "refresh_item" && args[0] === "wsl" ? 1200 : 150);
    switch (name) {
      case "get_config": return { host: mockState.host, port: mockState.port,
        scheme: mockState.scheme || "http", user: mockState.user || "",
        password: mockState.password || "", lang: mockState.lang || "zh",
        mode: mockState.mode, pin: mockState.pin, ports: mockState.ports,
        autostart: mockState.autostart, bg_path: mockState.bg_path, bg_dim: mockState.bg_dim, card_alpha: mockState.card_alpha,
        speed_full: 5, quit_clear: false, wsl_all_distros: true, close_hint_done: false,
        version: "1.4.0", config_load: "ok",
        applied_host: mockState.appliedHost, applied_port: mockState.appliedPort,
        applied_scheme: mockState.appliedScheme, applied_user: mockState.appliedUser,
        applied_password: mockState.appliedPassword, wsl_written: mockState.wslWritten };
      case "save_config": Object.assign(mockState, args[0]); return true;
      case "refresh_item": {
        const w = args[0];
        if (w === "ps") return { status: mockState.ps ? mockState.psHttp : null,
          vars: { HTTP_PROXY: mockState.psHttp, HTTPS_PROXY: mockState.psHttp } };
        if (w === "wsl") return { ip: mockState.wip, on: mockState.wsl,
          vars: { HTTP_PROXY: mockState.wslHttp, HTTPS_PROXY: mockState.wslHttp === "未设置" ? "未设置" : mockState.wslHttp },
          git: mockState.wslGit };
        if (w === "gitwin") return mockState.gitWinVal;
        if (w === "npm") return mockState.npmVal;
        if (w === "pip") return mockState.pipVal;
        if (w === "sys") return mockState.sysVal;
        if (w === "npmwsl") return mockState.npmWslVal;
        if (w === "pipwsl") return mockState.pipWslVal;
        if (w === "ip") return mockState.lip;
        if (w === "mode") return mockState.wmode;
        if (w === "pac") return { url: mockState.ps ? mockState.psHttp : "",
          autoconfig_url: "", pac: false };
        return null;
      }
      case "prepare_toggle": return { ok: true };
      case "test_channel": {
        await delay(600);
        return Math.random() > 0.25 ? { ok: true, ms: 120 + Math.floor(Math.random() * 300) }
                                    : { ok: false, error: "连接超时" };
      }
      case "do_toggle": {
        const [target, on] = args;
        const mockUrl = () => `${mockState.scheme || "http"}://${mockState.host}:${mockState.port}`;
        const wslSide = ["wsl", "gitwsl", "npmwsl", "pipwsl"];
        if (target === "ps") { mockState.ps = on; mockState.psHttp = on ? mockUrl() : "未设置"; }
        if (target === "sys") { mockState.sys = on; mockState.sysVal = on ? mockUrl() : "未设置"; }
        if (target === "wsl") { mockState.wsl = on; mockState.wslHttp = on ? mockUrl() : "未设置"; }
        if (target === "gitwin") { mockState.gitwin = on; mockState.gitWinVal = on ? mockUrl() : "未设置"; }
        if (target === "gitwsl") { mockState.gitwsl = on; mockState.wslGit = on ? mockUrl() : "未设置"; }
        if (target === "npm") { mockState.npm = on; mockState.npmVal = on ? mockUrl() : "未设置"; }
        if (target === "pip") { mockState.pip = on; mockState.pipVal = on ? mockUrl() : "未设置"; }
        if (target === "npmwsl") { mockState.npmwsl = on; mockState.npmWslVal = on ? mockUrl() : "未设置"; }
        if (target === "pipwsl") { mockState.pipwsl = on; mockState.pipWslVal = on ? mockUrl() : "未设置"; }
        if (on) {
          // Record the applied address (last values written) like the backend.
          mockState.appliedHost = mockState.host; mockState.appliedPort = String(mockState.port);
          mockState.appliedScheme = mockState.scheme || "http";
          mockState.appliedUser = mockState.user || ""; mockState.appliedPassword = mockState.password || "";
          if (wslSide.includes(target)) mockState.wslWritten[target] = mockUrl();
        } else if (wslSide.includes(target)) {
          delete mockState.wslWritten[target];
        }
        return { ok: true };
      }
      case "switch_port": {
        const p = String(args[0]);
        mockState.port = p;
        if (args[1]) mockState.host = String(args[1]);
        if (args[2]) mockState.scheme = String(args[2]);
        if (args[3] != null) mockState.user = String(args[3]);
        if (args[4] != null) mockState.password = String(args[4]);
        const u = `${mockState.scheme || "http"}://${mockState.host}:${p}`;
        if (mockState.ps) mockState.psHttp = u;
        if (mockState.wsl) mockState.wslHttp = u;
        if (mockState.gitwin) mockState.gitWinVal = u;
        if (mockState.gitwsl) mockState.wslGit = u;
        if (mockState.npm) mockState.npmVal = u;
        if (mockState.pip) mockState.pipVal = u;
        if (mockState.npmwsl) mockState.npmWslVal = u;
        if (mockState.pipwsl) mockState.pipWslVal = u;
        if (mockState.sys) mockState.sysVal = u;
        mockState.appliedHost = mockState.host; mockState.appliedPort = p;
        mockState.appliedScheme = mockState.scheme || "http";
        mockState.appliedUser = mockState.user || ""; mockState.appliedPassword = mockState.password || "";
        ["wsl", "gitwsl", "npmwsl", "pipwsl"].forEach((k) => { if (mockState[k]) mockState.wslWritten[k] = u; });
        return { ok: true, applied: ["ps", "sys", "wsl", "gitwin", "gitwsl", "npm", "pip", "npmwsl", "pipwsl"].filter(t => mockState[t]) };
      }
      case "get_speed": {
        const n = Math.random() * 3;
        return { up: `${(n * 0.3).toFixed(1)} MB/s`, dn: `${n.toFixed(1)} MB/s`,
                 dn_num: n * 1024 * 1024, iface: mockState.iface, alive: mockState.alive,
                 port: String(mockState.appliedPort || mockState.port) };
      }
      case "restart_wsl": await delay(1500); return { ok: true };
      case "apply_form_back": return true;
      case "window_control": {
        if (args[0] === "maximize") mockState._max = !mockState._max;
        return { ok: true, maximized: !!mockState._max, pinned: args[0] === "pin" ? !!args[1] : false };
      }
      case "pick_background": await delay(600); mockState.bg_path = "C:/mock/wallpaper.png"; return { ok: true, path: mockState.bg_path, name: "wallpaper.png" };
      case "get_background": return { data: "mock-bg" };
      case "clear_background": mockState.bg_path = ""; return true;
      case "set_autostart": mockState.autostart = !!args[0]; return { ok: true };
      case "copy": return true;
      case "check_update": return { status: "latest" };
      case "open_release_page": return true;
      case "list_presets": return mockState.presets.map(p => ({
        name: p.name, host: p.host, port: p.port, scheme: p.scheme,
        user: p.user, has_password: !!p.password }));
      case "get_preset": {
        const p = mockState.presets.find(x => x.name === String(args[0]).trim());
        return p ? { ok: true, ...p } : { ok: false, error_key: "presetMissing" };
      }
      case "save_preset": {
        const name = String(args[0] || "").trim();
        if (!name || name.length > 40) return { ok: false, error_key: "presetBadName" };
        const a = snapshotAddr();
        const host = args[1] != null ? String(args[1]) : a.host;
        const port = args[2] != null ? String(args[2]) : a.port;
        if (!host || !/^\d+$/.test(port)) return { ok: false, error_key: "errBadAddr" };
        const rec = { name, host, port, scheme: args[3] || a.scheme || "http",
          user: args[4] != null ? String(args[4]) : a.user,
          password: args[5] != null ? String(args[5]) : a.password };
        mockState.presets = mockState.presets.filter(x => x.name !== name);
        mockState.presets.push(rec);
        return { ok: true, name };
      }
      case "delete_preset":
        mockState.presets = mockState.presets.filter(x => x.name !== String(args[0]).trim());
        return { ok: true };
      case "backup_config": {
        const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 15);
        const name = `config-${stamp.slice(0, 8)}-${stamp.slice(8, 14)}.json`;
        mockState.backups.unshift({ name, mtime: Date.now() / 1000,
          size: 512, form: { host: mockState.host, port: mockState.port,
            scheme: mockState.scheme, user: mockState.user, password: mockState.password } });
        return { ok: true, name, mtime: Date.now() / 1000, size: 512 };
      }
      case "list_backups": return mockState.backups.map(b => ({ name: b.name, mtime: b.mtime, size: b.size }));
      case "restore_backup": {
        const b = mockState.backups.find(x => x.name === args[0]);
        if (!b) return { ok: false, error_key: "backupMissing" };
        Object.assign(mockState, b.form);
        return { ok: true };
      }
      case "delete_backup":
        mockState.backups = mockState.backups.filter(x => x.name !== args[0]);
        return { ok: true };
      default: return null;
    }
  }
  return { call };
})();

/* ═══════════════ Helpers ═══════════════ */
const $ = (id) => document.getElementById(id);
const UNSET = new Set(["未设置", "未检测到", "—", "", "Not set", "Not detected"]);

function toast(msg, kind = "") {
  const t2 = $("toast");
  t2.textContent = msg;
  t2.className = "toast show " + kind;
  clearTimeout(t2._h);
  // Errors get 8s (multi-endpoint failures concatenate into long text);
  // clicking anywhere on a toast dismisses it immediately.
  t2._h = setTimeout(() => t2.classList.remove("show"), kind === "err" ? 8000 : 2600);
}

/* Render a backend message in the UI language. Three payload shapes exist:
   - {error_parts|note_parts: [{key, params}|{text}...]} — bilingual parts
     (proxy_manager's LStr/LJoin + js_api note assembly)
   - {error_key, error_params} — single key + interpolation params
   - plain string — backend legacy/OS text, shown as-is. */
function msgText(m) {
  if (m == null) return "";
  if (typeof m === "string") return m;
  const parts = m.note_parts || m.error_parts;
  if (Array.isArray(parts) && parts.length) {
    const s = parts.map((p) => p && p.key ? t(p.key, p.params || {}) : (p && p.text) || "").join("；");
    // 双保险：parts 全部无 text 时（如历史 label-only 部件）回退 m.error，
    // 避免用户只看到空串或孤立标签、真正原因被丢（契约C1）。
    if (s) return s;
  }
  const key = m.note_key && !m.error ? m.note_key : m.error_key;
  if (key) return t(key, m.error_params || {});
  return m.error || m.note || "";
}

/* Tooltip engine. Hover keeps the CSS 350ms delay; keyboard focus shows the
   tip instantly (.fast zeroes the delay) — different intents, different timing. */
(() => {
  const tip = $("tooltip");
  let cur = null;
  const show = (el, instant) => {
    tip.textContent = el.dataset.tip;
    tip.classList.toggle("fast", instant);
    const r = el.getBoundingClientRect();
    requestAnimationFrame(() => {
      tip.style.visibility = "hidden"; tip.classList.add("show");
      const tw = tip.offsetWidth, th = tip.offsetHeight;
      let x = r.left + r.width / 2 - tw / 2;
      x = Math.max(8, Math.min(x, innerWidth - tw - 8));
      let y = r.bottom + 7;
      if (y + th > innerHeight - 4) y = r.top - th - 7;
      tip.style.left = x + "px"; tip.style.top = y + "px"; tip.style.visibility = "";
    });
  };
  document.addEventListener("mouseover", (e) => {
    const el = e.target.closest("[data-tip]");
    if (el === cur) return;
    cur = el;
    tip.classList.remove("show");
    if (el) show(el, false);
  });
  document.addEventListener("focusin", (e) => {
    const el = e.target.closest("[data-tip]");
    if (!el || el === cur) return;
    cur = el;
    tip.classList.remove("show");
    show(el, true);
  });
  document.addEventListener("focusout", (e) => {
    if (cur && e.target.closest("[data-tip]") === cur) {
      tip.classList.remove("show");
      cur = null;
    }
  });
})();

/* ═══════════════ Drop guard ═══════════════ */
/* WebView2 navigates when a FILE is dropped onto the window, and pywebview
   injects its JS bridge into the document that ends up loaded — so a dropped
   .html file would run with full Api access (registry env writes, WSL rc
   files, autostart). Only file drags are canceled; dragging plain text into
   the host/port inputs keeps its native drop behavior. */
const _isFileDrag = (e) => !!e.dataTransfer &&
  Array.prototype.includes.call(e.dataTransfer.types || [], "Files");
window.addEventListener("dragover", (e) => { if (_isFileDrag(e)) e.preventDefault(); });
window.addEventListener("drop", (e) => { if (_isFileDrag(e)) e.preventDefault(); });

/* Confirm dialog (Promise-based, replaces native messagebox). Requests are
   queued: overlapping confirmations would otherwise swallow the first. */
const _dlgQueue = [];
let _dlgOpen = false;
let _settingsPrevFocus = null;
function confirmDlg(text, okText) {
  okText = okText || t("continue");
  return new Promise((resolve) => {
    _dlgQueue.push({ text, okText, resolve });
    _pumpDlg();
  });
}
function _pumpDlg() {
  if (_dlgOpen || !_dlgQueue.length) return;
  const { text, okText, resolve } = _dlgQueue.shift();
  _dlgOpen = true;
  const prevFocus = document.activeElement;
  $("dlgText").textContent = text;
  $("dlgOk").textContent = okText;
  $("overlay").classList.add("show");
  $("dlgOk").focus();
  const done = (v) => {
    $("overlay").classList.remove("show");
    _dlgOpen = false;
    if (prevFocus && prevFocus.isConnected) prevFocus.focus();
    resolve(v);
    _pumpDlg();
  };
  $("dlgOk").onclick = () => done(true);
  $("dlgCancel").onclick = () => done(false);
}

/* Modal focus trap: keep Tab cycling inside the open dialog so keyboard users
   cannot reach the obscured background. The confirm dialog takes priority over
   settings because it can be opened on top of it (e.g. backup restore). */
const _FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
function _trapFocus(e) {
  if (e.key !== "Tab") return;
  const ov = _dlgOpen ? $("overlay")
    : ($("settingsOv").classList.contains("show") ? $("settingsOv") : null);
  if (!ov) return;
  const items = [...ov.querySelectorAll(_FOCUSABLE)]
    .filter((el) => !el.disabled && el.offsetParent !== null);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  const active = document.activeElement;
  if (e.shiftKey && (active === first || !ov.contains(active))) {
    e.preventDefault(); last.focus();
  } else if (!e.shiftKey && (active === last || !ov.contains(active))) {
    e.preventDefault(); first.focus();
  }
}
document.addEventListener("keydown", _trapFocus);

/* ═══════════════ State ═══════════════ */
const ICONS = {
  ps: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 8l4 4-4 4"/><path d="M12 17h7"/></svg>',
  wsl: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M7 9l2.5 2.5L7 14M12 14h5"/></svg>',
  gitwin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="8" r="2.5"/><path d="M6 8.5v7M18 10.5c0 3-2.5 4.5-6 4.5"/></svg>',
  gitwsl: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="8" r="2.5"/><path d="M6 8.5v7"/><path d="M18 10.5c0 3-2.5 4.5-6 4.5" stroke-dasharray="2.5 2.5"/></svg>',
  npm: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5h18v7h-9v1.5h-4.5v-1.5H3z" stroke-dasharray="0"/><path d="M7 11v2M12 11v2M17 11v2"/></svg>',
  pip: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3c4 3.4 6.5 6.6 6.5 10a6.5 6.5 0 1 1-13 0c0-3.4 2.5-6.6 6.5-10z"/><path d="M9 14a3 3 0 0 0 3 3"/></svg>',
  sys: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>',
};
const EP_DEFS = [
  { id: "ps",     lane: "win",  nameKey: "ep.ps",     descKey: "ep.psDesc" },
  { id: "sys",    lane: "win",  nameKey: "ep.sys",    descKey: "ep.sysDesc" },
  { id: "gitwin", lane: "win",  nameKey: "ep.gitwin", descKey: "ep.gitwinDesc" },
  { id: "npm",    lane: "win",  nameKey: "ep.npm",    descKey: "ep.npmDesc" },
  { id: "pip",    lane: "win",  nameKey: "ep.pip",    descKey: "ep.pipDesc" },
  { id: "wsl",    lane: "wsl",  nameKey: "ep.wsl",    descKey: "ep.wslDesc" },
  { id: "gitwsl", lane: "wsl",  nameKey: "ep.gitwsl", descKey: "ep.gitwslDesc" },
  { id: "npmwsl", lane: "wsl",  nameKey: "ep.npmwsl", descKey: "ep.npmwslDesc" },
  { id: "pipwsl", lane: "wsl",  nameKey: "ep.pipwsl", descKey: "ep.pipwslDesc" },
];
const state = { ps: false, sys: false, wsl: false, gitwin: false, gitwsl: false, npm: false, pip: false, npmwsl: false, pipwsl: false, busy: {} };
const cfg = { ports: [], bg_path: "", bg_dim: 0.45, card_alpha: 0.92, close_hint_done: false, quit_clear: false, wsl_all_distros: true, wsl_written: {} };
const ui = { pinned: false, maximized: false };

/* ═══════════════ Endpoints render (two channel lanes) ═══════════════ */
function epCard(d) {
  const on = state[d.id];
  const badge = on ? `<span class="badge ok"><span class="dot"></span>${t("enabled")}</span>`
                   : `<span class="badge idle"><span class="dot"></span>${t("disabled")}</span>`;
  const val = displayStatus(epValue(d.id));
  return `<div class="card ep ${state.busy[d.id] ? "busy" : ""}" id="ep-${d.id}">
    <div class="chip">${ICONS[d.id] || ICONS[d.id.replace(/wsl$/, "")] || ""}</div>
    <div class="meta">
      <div class="name-row"><span class="name">${t(d.nameKey)}</span>${badge}</div>
      <div class="desc">${t(d.descKey)} · <code id="epv-${d.id}">${esc_(val)}</code></div>
    </div>
    <button type="button" class="tgl ${on ? "on" : ""}" id="sw-${d.id}" data-ep="${d.id}" role="switch" aria-checked="${on}" aria-label="${t(d.nameKey)}" data-tip="${on ? t("clickOff") : t("clickOn")}"></button>
  </div>`;
}
function renderEndpoints() {
  const host = $("epList");
  // innerHTML replacement drops keyboard focus; remember a focused switch so
  // a keyboard user toggling an endpoint stays on it after the re-render.
  const active = document.activeElement;
  const focusId = active && host.contains(active) && active.id ? active.id : null;
  host.innerHTML = EP_DEFS.filter((d) => d.lane === curLane).map(epCard).join("");
  if (focusId) {
    const back = $(focusId);
    if (back && back !== document.activeElement) back.focus();
  }
  // Click handlers are delegated on #epList (see Wiring section) so they
  // survive innerHTML replacement during refreshAll without stale closures.
  $("epCap").textContent = curLane === "win"
    ? t("epCapWin")
    : (cfg.wsl_all_distros !== false ? t("epCapWslAll") : t("epCapWslDef"));
  $("epTitle").textContent = curLane === "win" ? t("epTitleWin") : t("epTitleWsl");
  host.setAttribute("aria-labelledby", curLane === "win" ? "tabWin" : "tabWsl");
  updateHero();
}

/* Page switch: endpoints + info cards + hero accent all follow the lane. */
function setLane(lane) {
  if (curLane === lane) return;
  curLane = lane;
  $("tabWin").classList.toggle("active", lane === "win");
  $("tabWsl").classList.toggle("active", lane === "wsl");
  $("tabWin").setAttribute("aria-selected", String(lane === "win"));
  $("tabWsl").setAttribute("aria-selected", String(lane === "wsl"));
  // Roving tabindex: only the active tab is in the tab order (tablist pattern).
  $("tabWin").tabIndex = lane === "win" ? 0 : -1;
  $("tabWsl").tabIndex = lane === "wsl" ? 0 : -1;
  $("netWin").hidden = lane !== "win";
  $("netWsl").hidden = lane !== "wsl";
  $("varsWin").hidden = lane !== "win";
  $("varsWsl").hidden = lane !== "wsl";
  // 重启 WSL lives on the status bar but only concerns the WSL page.
  $("btnWslRestart").hidden = lane !== "wsl";
  renderEndpoints();  // updateHero refreshes sbGit/sbNpm/sbPip for the lane
  renderHistory();    // the result history is lane-scoped too
  refreshAll();       // probe the newly visible lane (states may be stale)
}

/* Per-endpoint current value: git rows show what was actually detected in
   each git config; ps/wsl rows show the detected proxy URL. */
let detected = { gitwin: "未设置", gitwsl: "未设置", ps: null, sys: "未设置", wsl_url: null,
                 npm: "未设置", pip: "未设置", npmwsl: "未设置", pipwsl: "未设置" };
function epValue(id) {
  if (id === "gitwin" || id === "gitwsl") return detected[id];
  if (id === "ps") return detected.ps || "未设置";
  if (id === "sys") return detected.sys || "未设置";
  if (id === "wsl") return state.wsl ? (detected.wsl_url || "未设置") : "未设置";
  if (id === "npm" || id === "pip" || id === "npmwsl" || id === "pipwsl") return detected[id];
  return "未设置";
}

const EP_TOTAL = EP_DEFS.length;
/* Channel pages: the status-bar tabs switch the whole page between the
   Windows and WSL views; each tool appears once per page. */
let curLane = "win";
const LANE_EPS = {
  win: EP_DEFS.filter((d) => d.lane === "win").map((d) => d.id),
  wsl: EP_DEFS.filter((d) => d.lane === "wsl").map((d) => d.id),
};
/* WSL states stay unknown until the first WSL probe — a boot-time probe was
   removed because it cold-starts the whole WSL VM just for the badge. */
let wslProbed = false;
function updateHero() {
  const nWin = LANE_EPS.win.reduce((a, k) => a + Number(state[k]), 0);
  const nWsl = LANE_EPS.wsl.reduce((a, k) => a + Number(state[k]), 0);
  const n = nWin + (wslProbed ? nWsl : 0);
  $("cntWin").textContent = `${nWin}/${LANE_EPS.win.length}`;
  $("cntWsl").textContent = wslProbed
    ? `${nWsl}/${LANE_EPS.wsl.length}` : `–/${LANE_EPS.wsl.length}`;
  updateVtRail();
  const badge = $("heroBadge");
  if (n === EP_TOTAL) { badge.className = "badge ok"; badge.innerHTML = `<span class="dot"></span>${t("allOn")}`; }
  else if (n > 0) { badge.className = "badge warn"; badge.innerHTML = `<span class="dot"></span>${t("partialOn")}`; }
  else { badge.className = "badge idle"; badge.innerHTML = `<span class="dot"></span>${t("noneOn")}`; }
  $("heroLine").innerHTML = `<b>${n} / ${EP_TOTAL}</b> <span class="dim">${t("endpointsOn")}</span>`;
  const fmt = (v) => UNSET.has(v) ? t("unset") : String(v).replace(/^(https?|socks5h?|socks4):\/\//, "");
  const gk = curLane === "win" ? "gitwin" : "gitwsl";
  const nk = curLane === "win" ? "npm" : "npmwsl";
  const pk = curLane === "win" ? "pip" : "pipwsl";
  $("sbGit").textContent = fmt(detected[gk]);
  $("sbNpm").textContent = fmt(detected[nk]);
  $("sbPip").textContent = fmt(detected[pk]);
}

/* Vertical page-level toggles: two rails (全部开启 / 全部关闭). Each rail's
   knob sits at the top while its action is the current state, else at the
   bottom — 全部开启's rail reads "on" when the whole lane is on; 全部关闭's
   rail reads "off" when the whole lane is off. Click runs the batch action. */
function _vtKnobPos(rail, active) {
  const min = 10, max = Math.max(min, rail.clientHeight - 36);
  return (active ? min : max) + "px";
}
function updateVtRail() {
  const laneIds = LANE_EPS[curLane];
  const n = laneIds.reduce((a, k) => a + Number(state[k]), 0);
  const allOn = n === laneIds.length, allOff = n === 0;
  const rOn = $("vtRailOn"), rOff = $("vtRailOff");
  if (!rOn || !rOff) return;
  rOn.classList.toggle("on", allOn);
  rOn.classList.toggle("partial", !allOn && !allOff);
  rOn.dataset.tip = allOn ? t("tipRailOnDone") : t("tipRailOn");
  rOff.classList.toggle("off", allOff);
  rOff.dataset.tip = allOff ? t("tipRailOffDone") : t("tipRailOff");
  rOn.querySelector(".vt-knob").style.top = _vtKnobPos(rOn, allOn);
  rOff.querySelector(".vt-knob").style.top = _vtKnobPos(rOff, allOff);
}
async function toggleVtRail(which) {
  const laneIds = LANE_EPS[curLane];
  const allOn = laneIds.every((k) => state[k]);
  // 全部开启 rail is inert while the lane is already fully on; same for off.
  if (which === "on" && !allOn) await allEndpoints(true, curLane);
  if (which === "off" && !allOff(laneIds)) await allEndpoints(false, curLane);
}
const allOff = (ids) => ids.every((k) => !state[k]);
$("vtRailOn").addEventListener("click", () => toggleVtRail("on"));
$("vtRailOff").addEventListener("click", () => toggleVtRail("off"));

/* ═══════════════ Toggle flow (validation + confirm + apply) ═══════════════ */
let busyAll = false;
function setBusy(ep, b) { state.busy[ep] = b; renderEndpoints(); }

async function toggleEndpoint(ep, on) {
  if (state.busy[ep] || busyAll) return;
  const a = snapshotAddr();
  if (on) {
    if (!validateFields(true)) return;
    const pre = await api.call("prepare_toggle", ep, true, a.host, a.port);
    if (pre && pre.confirm) {
      if (!(await confirmDlg(confirmMessage(pre)))) return;
    } else if (pre && pre.error) {
      toast(msgText(pre), "err"); return;
    }
  }
  setBusy(ep, true);
  try {
    const r = await api.call("do_toggle", ep, on, a.host, a.port, a.scheme, a.user, a.password);
    if (r && r.error) toast(msgText(r), "err");
    else {
      // Flip the row immediately; a refresh corrects it. A disable that
      // restored a stashed proxy actually leaves the endpoint ON.
      const restored = !!(r && r.restored);
      state[ep] = on;
      if (on && !restored) lastApplied = Object.assign({}, a);
      if (restored) {
        state[ep] = true;
        toast(t("restored"), "ok");
      } else if (r && r.note) {
        toast(msgText(r), "ok");
      } else {
        let msg = on ? t("enabledMsg", { name: epName(ep) }) : t("disabledMsg", { name: epName(ep) });
        if (on && (ep === "ps" || ep === "wsl")) msg += t("needNewTerm");
        toast(msg, "ok");
      }
      renderEndpoints();
    }
  } finally {
    setBusy(ep, false);
    refreshAll();
  }
}

function epName(ep) { return t((EP_DEFS.find((d) => d.id === ep) || {}).nameKey || ep); }

/* Field validation, mirroring the Python-side rules. */
function validateFields(mark) {
  const h = $("inHost"), p = $("inPort"), u = $("inUser");
  const host = h.value.trim(), port = p.value.trim(), user = u ? u.value.trim() : "";
  h.classList.remove("bad"); p.classList.remove("bad");
  if (u) u.classList.remove("bad");
  const bad = (el, msg) => { if (mark) el.classList.add("bad"); toast(msg, "err"); return false; };
  if (!host) return bad(h, t("needHost"));
  if (/\s/.test(host)) return bad(h, t("hostSpace"));
  if (!/^[A-Za-z0-9._-]+$/.test(host))
    return bad(h, host.includes(":") ? t("hostColon") : t("hostChars"));
  if (host.includes("://")) return bad(h, t("hostScheme"));
  if (!/^\d+$/.test(port) || !(+port >= 1 && +port <= 65535)) return bad(p, t("badPort"));
  if (user && /[^A-Za-z0-9._-]/.test(user)) return bad(u, t("badUser"));
  const pw = $("inPass") ? $("inPass").value : "";
  if (pw && /[^A-Za-z0-9._-]/.test(pw)) return bad($("inPass") || u, t("badUser"));
  return true;
}

/* ═══════════════ Port presets: chips + quick switch + manage ═══════════════ */
function renderPorts() {
  const cur = $("inPort").value.trim();
  const chips = cfg.ports.map((p, i) =>
    `<button class="pchip ${String(p) === cur ? "active" : ""}" data-port="${esc_(p)}" data-tip="${esc_(t("tipSwitchPort", { port: p }))}${i < 9 ? " (" + (i + 1) + ")" : ""}">${esc_(p)}</button>`).join("");
  $("portChips").innerHTML = chips + `<button class="pchip add" id="portManage" data-tip="${t("tipManagePorts")}">＋</button>`;
  $("portChips").querySelectorAll(".pchip[data-port]").forEach((el) => {
    el.onclick = () => quickSwitchPort(el.dataset.port);
  });
  $("portManage").onclick = (e) => { e.stopPropagation(); $("profilePop").classList.remove("show"); renderPortPop(); $("portPop").classList.toggle("show"); };
}

function renderPortPop() {
  const cur = $("inPort").value.trim();
  $("portPopList").innerHTML = cfg.ports.map((p) =>
    `<div class="row"><span class="pv">${esc_(p)}${String(p) === cur ? ' <span class="cur-tag">' + t("current") + '</span>' : ""}</span>
     <button type="button" class="del" data-del="${esc_(p)}" data-tip="${t("removePreset")}">×</button></div>`).join("")
    || `<div class="empty-note">${t("noPresets")}</div>`;
  $("portPopList").querySelectorAll("[data-del]").forEach((el) => {
    el.onclick = () => {
      cfg.ports = cfg.ports.filter((x) => String(x) !== el.dataset.del);
      renderPorts(); renderPortPop(); persistSoon();
    };
  });
}

let lastApplied = { host: "", port: "", scheme: "http", user: "", password: "" };
let pendingApply = null;

function snapshotAddr() {
  return {
    host: $("inHost").value.trim(),
    port: $("inPort").value.trim(),
    scheme: $("inScheme") ? $("inScheme").value : "http",
    user: $("inUser") ? $("inUser").value.trim() : "",
    password: $("inPass") ? $("inPass").value : "",
  };
}
function addrKey(a) {
  return [a.scheme, a.user, a.password, a.host, a.port].join("\0");
}
function appliedAddr() {
  // Switches compare against the address last written to endpoints, not the
  // in-progress form. Typing a new port then hitting refresh used to flip
  // PowerShell/system-proxy OFF because expectedUrl() read the input box.
  if (lastApplied && lastApplied.host && lastApplied.port) return lastApplied;
  return snapshotAddr();
}
function expectedUrl(kind, addr) {
  const a = addr || appliedAddr();
  // WinINET writes host:port or socks=host:port — no userinfo, and SOCKS5h
  // comes back as socks5://. Compare against that reconstructed URL or the
  // system-proxy switch flips off right after enable.
  if (kind === "sys") {
    const sch = (a.scheme || "http").startsWith("socks") ? "socks5" : "http";
    return `${sch}://${a.host}:${a.port}`;
  }
  const auth = a.user
    ? encodeURIComponent(a.user) + (a.password ? ":" + encodeURIComponent(a.password) : "") + "@"
    : "";
  return `${(a.scheme || "http")}://${auth}${a.host}:${a.port}`;
}
/* Switch is ON only for THIS tool's URL (same contract as PowerShell /
   system proxy). `allowNat` endpoints additionally accept the exact URL
   this tool last wrote (`writtenUrl` — the NAT gateway rewrite form kept
   in the backend's wsl_written record). A foreign git/npm/pip proxy must
   show OFF so a click enables (and stashes) instead of deleting it. */
function urlLooksOurs(v, kind, allowNat, writtenUrl) {
  if (v == null || UNSET.has(String(v))) return false;
  const s = String(v);
  if (s === expectedUrl(kind)) return true;
  if (kind === "sys" || !allowNat) return false;
  // 精确比较，不做"同端口/协议/凭据 + 主机不同"的启发式：那会把用户手工
  // 配置的同端口 LAN 代理误标为已开启，点击关闭一次即删——与后端
  // _is_managed_wsl_url 的"无记录一律当用户状态"契约对齐
  return !!writtenUrl && s === String(writtenUrl);
}
function confirmMessage(pre) {
  if (!pre) return "";
  if (pre.confirm === "port") return t("confirmPort", { host: pre.host || "", port: pre.port || "" });
  if (pre.confirm === "shell") return t("confirmShell", { shell: pre.shell || "" });
  if (pre.confirm === "npm") return t("confirmNpm");
  return pre.message || "";
}

/* Port-switch failure detail. msgText() alone drops the backend's detail
   lines whenever error_key is set (it returns the localized label), and a
   bare r.error would lose that label plus any bilingual per-endpoint parts
   — so combine them: localized head + backend detail. Only the port-switch
   path uses this; other callers keep msgText()'s "key wins" behavior. */
function switchErrText(r) {
  const key = r && r.error_key;
  const body = (r && (r.error_parts || r.note_parts)) ? msgText(r) : (r && r.error);
  if (!key) return msgText(r);
  if (!body || body === t(key)) return t(key);
  return `${t(key)}\n${body}`;
}

async function applyAddress(next, prev) {
  next = next || snapshotAddr();
  next.host = String(next.host || "").trim();
  next.port = String(next.port || "").trim();
  next.scheme = next.scheme || "http";
  next.user = next.user || "";
  next.password = next.password || "";
  if (addrKey(next) === addrKey(lastApplied)) return;
  if (busyAll || Object.values(state.busy).some(Boolean) || refreshing) {
    pendingApply = { next, prev };
    toast(t("pendingApply"), "");
    return;
  }
  $("inHost").value = next.host;
  $("inPort").value = next.port;
  if ($("inScheme")) $("inScheme").value = next.scheme;
  if ($("inUser")) $("inUser").value = next.user;
  if ($("inPass")) $("inPass").value = next.password;
  $("inHost").classList.remove("bad");
  $("inPort").classList.remove("bad");
  if (!validateFields(true)) {
    if (prev) {
      $("inHost").value = prev.host;
      $("inPort").value = prev.port;
      if ($("inScheme")) $("inScheme").value = prev.scheme || "http";
      if ($("inUser")) $("inUser").value = prev.user || "";
      if ($("inPass")) $("inPass").value = prev.password || "";
    }
    renderPorts();
    return;
  }
  /* Switching is trivially reversible (switch back), so no confirm dialog:
     apply immediately and let the toast report the result — including partial
     failures, which switchedPartial spells out per endpoint. */
  const shown = `${next.scheme}://${next.host}:${next.port}`;
  setBusyAll(true);
  try {
    const r = await api.call("switch_port", next.port, next.host, next.scheme, next.user, next.password);
    if (r && r.error) {
      const n = (r.applied || []).length;
      toast(n ? t("switchedPartial", { addr: shown, n, err: switchErrText(r) }) : switchErrText(r), "err");
      if (!(r.applied && r.applied.length)) {
        if (prev) {
          $("inHost").value = prev.host;
          $("inPort").value = prev.port;
          if ($("inScheme")) $("inScheme").value = prev.scheme || "http";
          if ($("inUser")) $("inUser").value = prev.user || "";
          if ($("inPass")) $("inPass").value = prev.password || "";
        }
      } else {
        lastApplied = Object.assign({}, next);
      }
    } else {
      const n = (r && r.applied || []).length;
      // applied 为空＝没有任何端点被写入（契约A2）：不把表单地址提升为
      // lastApplied，否则 appliedAddr() 会把用户手工代理误判为已应用。
      if (n) lastApplied = Object.assign({}, next);
      // Backend notes (NAT gateway rewrite, PAC, per-machine policy) are the
      // honest report of what actually happened — same contract as
      // toggleEndpoint's note branch above.
      const note = r && (r.note || r.note_parts) ? msgText(r) : "";
      toast(note || (n ? t("switched", { addr: shown, n }) : t("addrSet", { addr: shown })), "ok");
    }
  } catch (e) {
    toast(t("switchFail", { err: e }), "err");
  } finally { setBusyAll(false); }
  renderPorts();
  renderProfiles();
  refreshAll();
}

async function quickSwitchPort(p) {
  const prev = snapshotAddr();
  if (String(p) === prev.port) return;
  $("inPort").value = String(p);
  renderPorts();
  await applyAddress(snapshotAddr(), prev);
}

/* ═══════════════ Named address presets ═══════════════ */
/* A preset is a saved FORM address (host/port/scheme/user/password). Applying
   one fetches the decrypted address via get_preset then reuses applyAddress →
   switch_port, so the applied/restore ownership semantics are identical to a
   port switch: only currently-enabled managed endpoints are re-applied. */
let presets = [];   // metadata from list_presets — never the plaintext password

async function loadPresets() {
  try { presets = (await api.call("list_presets")) || []; }
  catch (_) { presets = []; }
  renderProfiles();
}

function presetMatchesForm(p) {
  const a = snapshotAddr();
  return String(p.port) === a.port && p.host === a.host
    && (p.scheme || "http") === a.scheme && (p.user || "") === a.user
    && !!p.has_password === !!a.password;
}

function renderProfiles() {
  const wrap = $("profileChips");
  if (!wrap) return;
  const chips = presets.map((p) => {
    const addr = `${p.scheme}://${p.host}:${p.port}`;
    return `<button class="pchip ${presetMatchesForm(p) ? "active" : ""}" data-preset="${esc_(p.name)}" data-tip="${esc_(t("tipApplyPreset", { name: p.name, addr }))}">${esc_(p.name)}</button>`;
  }).join("");
  wrap.innerHTML = chips + `<button class="pchip add" id="profileManage" data-tip="${t("tipManagePresets")}">＋</button>`;
  wrap.querySelectorAll(".pchip[data-preset]").forEach((el) => {
    el.onclick = () => applyPreset(el.dataset.preset);
  });
  $("profileManage").onclick = (e) => {
    e.stopPropagation();
    $("portPop").classList.remove("show");
    renderProfilePop();
    $("profilePop").classList.toggle("show");
  };
}

function renderProfilePop() {
  const list = $("profilePopList");
  if (!list) return;
  list.innerHTML = presets.map((p) =>
    `<div class="row" data-apply="${esc_(p.name)}">
       <span class="pv">${esc_(p.name)} <span class="paddr">${esc_(p.scheme)}://${esc_(p.host)}:${esc_(p.port)}</span>${presetMatchesForm(p) ? ' <span class="cur-tag">' + t("current") + '</span>' : ""}</span>
       <button type="button" class="del" data-del="${esc_(p.name)}" data-tip="${t("removePreset")}">×</button>
     </div>`).join("")
    || `<div class="empty-note">${t("noPresets")}</div>`;
  list.querySelectorAll("[data-apply]").forEach((el) => {
    el.onclick = (e) => {
      if (e.target.closest("[data-del]")) return;
      $("profilePop").classList.remove("show");
      applyPreset(el.dataset.apply);
    };
  });
  list.querySelectorAll("[data-del]").forEach((el) => {
    el.onclick = async (e) => {
      e.stopPropagation();
      const name = el.dataset.del;
      const r = await api.call("delete_preset", name);
      if (r && r.ok) { await loadPresets(); renderProfilePop(); toast(t("presetDeleted", { name }), "ok"); }
      else toast(msgText(r), "err");
    };
  });
}

async function applyPreset(name) {
  const prev = snapshotAddr();
  let p;
  try { p = await api.call("get_preset", name); }
  catch (e) { toast(t("switchFail", { err: e }), "err"); return; }
  if (!p || !p.ok) {
    toast(msgText(p || { error_key: "presetMissing" }), "err");
    await loadPresets();
    return;
  }
  const next = { host: p.host, port: String(p.port), scheme: p.scheme || "http",
                 user: p.user || "", password: p.password || "" };
  await applyAddress(next, prev);
}

function setBusyAll(b) {
  busyAll = b;
  for (const rid of ["vtRailOn", "vtRailOff"]) {
    const rail = $(rid);
    // Real buttons: disabled blocks pointer AND keyboard activation.
    if (rail) { rail.disabled = b; rail.style.opacity = b ? ".55" : ""; }
  }
  const btn = $("btnWslRestart");
  if (btn) btn.disabled = b;
  // Batch toggles / port switches / WSL restarts can run for tens of seconds
  // (each WSL endpoint spawns several wsl.exe calls) — the status-bar
  // spinner is the only global "still working" indicator.
  const spin = $("sbRefreshIco"), rb = $("sbRefresh");
  if (spin) spin.classList.toggle("spin", b);
  if (rb) rb.disabled = b;
}

/* ═══════════════ Refresh (parallel, lane-aware) ═══════════════ */
/* Coalesce the endpoint repaints that a refresh's parallel probes each
   trigger: eight probes settling at once would redraw the list eight times.
   A 30ms timer is deliberate — awaiting Promise.all instead would hold the
   first frame hostage to the slowest probe (a WSL slice can take ~15s), and
   progressive results must stay visible. The callback reads the same
   state/detected the probe callbacks already updated, so the final frame is
   identical to today's; re-entrant refreshes (refreshQueued) simply share
   the one pending timer. */
const _RENDER_BATCH_MS = 30;
let renderTimer = null;
function scheduleRender() {
  if (renderTimer) return;
  renderTimer = setTimeout(() => { renderTimer = null; renderEndpoints(); }, _RENDER_BATCH_MS);
}
/* PAC (AutoConfigURL) hint under the WinINET row. The URL is registry data:
   it goes through textContent, never innerHTML. Lives inside #varsWin, so
   the WSL lane hides it with the whole card. */
let pacInfo = null;
function renderPacHint() {
  const el = $("pacHint");
  if (!el) return;
  const r = pacInfo;
  if (r && r.pac && r.autoconfig_url) {
    el.textContent = t("pacActive", { url: r.autoconfig_url });
    el.hidden = false;
  } else {
    el.hidden = true;
    el.textContent = "";
  }
}
let refreshing = false;
let refreshQueued = false;
async function refreshAll() {
  if (refreshing) { refreshQueued = true; return; }
  refreshing = true;
  refreshQueued = false;
  const spin = $("sbRefreshIco");
  spin.classList.add("spin");
  $("sbRefresh").disabled = true;
  let pending = 0;
  const settle = () => {
    if (--pending === 0) {
      const needAgain = (curLane !== lane) || refreshQueued;
      refreshQueued = false;
      refreshing = false;
      spin.classList.remove("spin");
      // A batch operation may have grabbed the spinner while we settled.
      $("sbRefresh").disabled = busyAll;
      if (anyOk) persistSoon();
      if (pendingApply) {
        const p = pendingApply; pendingApply = null;
        applyAddress(p.next, p.prev);
      } else if (needAgain) refreshAll();
    }
  };
  const track = (p) => {
    pending++;
    // p 正常恒为 Promise（api.call 与 .then/.catch 链）。此处兜底：万一不是
    // thenable 或 .finally 同步抛错，也要 settle，否则 refreshing 永久锁死、
    // 之后所有 refreshAll 都被 refreshQueued 短路（契约low6）。
    try { Promise.resolve(p).finally(settle); }
    catch (_) { settle(); }
  };
  const set = (id, v) => {
    const el = $(id); if (!el) return;
    el.textContent = displayStatus(v);
    el.classList.toggle("unset", UNSET.has(String(v)));
  };
  let anyOk = false;
  // wsl_written 记录随每次刷新先重取：do_toggle/switch_port 会改写"最后写入
  // URL"记录，若只在 boot 拉一次，NAT 模式下启用成功后徽章会拿着过期记录
  // 错翻 OFF，且该端点经 UI 关不掉。先于各端点探测完成（本机 IPC，代价可
  // 忽略），保证下方 state 计算用到的记录是新值
  try {
    const c = await api.call("get_config");
    if (c) cfg.wsl_written = c.wsl_written || {};
  } catch (_) { /* 配置拉取失败沿用旧记录，与既有降级一致 */ }
  // Only the visible lane is probed: WSL slices cost a wsl.exe round-trip
  // each, and the hidden lane's states are refreshed when its tab opens.
  const lane = curLane;
  if (lane === "win") track(api.call("refresh_item", "ps").then((r) => {
    anyOk = true;
    const expect = expectedUrl();
    state.ps = !!r.status && String(r.status) === expect;
    detected.ps = r.status ? r.vars.HTTP_PROXY : null;
    set("vPsHttp", r.vars.HTTP_PROXY); set("vPsHttps", r.vars.HTTPS_PROXY);
    if (r.vars.ALL_PROXY) set("vPsAll", r.vars.ALL_PROXY);
    scheduleRender();
  }).catch(() => {}));
  if (lane === "win") track(api.call("refresh_item", "sys").then((v) => {
    anyOk = true;
    const expect = expectedUrl("sys");
    detected.sys = v;
    state.sys = String(v) === expect;
    set("vSys", v);
    scheduleRender();
  }).catch(() => {}));
  // PAC awareness is a value-only probe (like ip/mode): it fills one hint
  // node, never repaints the endpoint list, so the 8-probe render batch is
  // unchanged.
  if (lane === "win") track(api.call("refresh_item", "pac").then((r) => {
    pacInfo = r || null;
    renderPacHint();
  }).catch(() => {}));
  if (lane === "wsl") track(api.call("refresh_item", "wsl").then((r) => {
    anyOk = true;
    wslProbed = true;  // the –/4 badge becomes real counts after first probe
    state.wsl = !!r.on;
    detected.gitwsl = r.git;
    state.gitwsl = urlLooksOurs(r.git, null, true, cfg.wsl_written.gitwsl);
    detected.wsl_url = state.wsl ? r.vars.HTTP_PROXY : null;
    set("vWip", r.ip);
    set("vWslHttp", r.vars.HTTP_PROXY); set("vWslHttps", r.vars.HTTPS_PROXY);
    if (r.vars.ALL_PROXY) set("vWslAll", r.vars.ALL_PROXY);
    set("vWslGit", r.git);
    scheduleRender();
  }).catch(() => {}));
  if (lane === "win") track(api.call("refresh_item", "gitwin").then((v) => {
    anyOk = true;
    detected.gitwin = v;
    state.gitwin = urlLooksOurs(v);
    set("vPsGit", v);
    scheduleRender();
  }).catch(() => {}));
  if (lane === "win") track(api.call("refresh_item", "npm").then((v) => {
    anyOk = true;
    detected.npm = v;
    state.npm = urlLooksOurs(v);
    set("vNpm", v);
    scheduleRender();
  }).catch(() => {}));
  if (lane === "win") track(api.call("refresh_item", "pip").then((v) => {
    anyOk = true;
    detected.pip = v;
    state.pip = urlLooksOurs(v);
    set("vPip", v);
    scheduleRender();
  }).catch(() => {}));
  if (lane === "wsl") track(api.call("refresh_item", "npmwsl").then((v) => {
    anyOk = true;
    detected.npmwsl = v;
    state.npmwsl = urlLooksOurs(v, null, true, cfg.wsl_written.npmwsl);
    set("vNpmWsl", v);
    scheduleRender();
  }).catch(() => {}));
  if (lane === "wsl") track(api.call("refresh_item", "pipwsl").then((v) => {
    anyOk = true;
    detected.pipwsl = v;
    state.pipwsl = urlLooksOurs(v, null, true, cfg.wsl_written.pipwsl);
    set("vPipWsl", v);
    scheduleRender();
  }).catch(() => {}));
  if (lane === "win") track(api.call("refresh_item", "ip").then((v) => { set("vLip", v); }).catch(() => {}));
  if (lane === "wsl") track(api.call("refresh_item", "mode").then((v) => {
    set("vMode2", v);
    // The NAT note only applies to NAT mode; mirrored keeps loopback.
    const hint = $("natHint");
    if (hint) {
      const mirrored = /^mirrored$/i.test(String(v).trim());
      hint.textContent = mirrored ? t("natHintMirrored") : t("natHint");
    }
  }).catch(() => {}));
  renderEndpoints();
}

/* Persist host/port/states silently (debounced), like save_config on success. */
let persistTimer = null;
function persistSoon() {
  clearTimeout(persistTimer);
  persistTimer = setTimeout(() => {
    const a = snapshotAddr();
    const payload = {
      host: a.host, port: a.port, scheme: a.scheme, user: a.user, password: a.password,
      lang,
      powershell: state.ps, wsl: state.wsl, git_win: state.gitwin, git_wsl: state.gitwsl,
      npm: state.npm, pip: state.pip, npmwsl: state.npmwsl, pipwsl: state.pipwsl,
      pin: ui.pinned, mode: document.documentElement.dataset.theme,
      ports: cfg.ports, bg_dim: cfg.bg_dim, card_alpha: cfg.card_alpha,
      quit_clear: cfg.quit_clear, wsl_all_distros: cfg.wsl_all_distros,
      close_hint_done: cfg.close_hint_done,
    };
    // Skip speed_full until boot() has loaded the saved value — the pre-boot
    // default (null) would otherwise overwrite the user's config.
    if (speedFullMbps != null) payload.speed_full = speedFullMbps;
    api.call("save_config", payload).then((ok) => {
      if (ok && ok.error) toast(msgText(ok), "err");
      else if (ok === false) toast(t("saveFail"), "err");
    }).catch(() => {});
  }, 400);
}

/* ═══════════════ All on / all off ═══════════════ */
/* Batch-toggle endpoints. With `lane` set (the vertical page toggle), only
   that channel's endpoints are touched; without it (全部关闭), all of them. */
async function allEndpoints(on, lane = null) {
  if (busyAll || Object.values(state.busy).some(Boolean)) return;
  const a = snapshotAddr();
  if (on && !validateFields(true)) return;
  setBusyAll(true);
  let hadError = false, cancelled = false;
  try {
    for (const d of EP_DEFS) {
      if (lane && d.lane !== lane) continue;
      if (state[d.id] === on) continue;
      if (on) {
        // Per-endpoint pre-flight (shell/git/WSL checks), deduping identical
        // confirmations so the user isn't asked the same question 3 times.
        const pre = await api.call("prepare_toggle", d.id, true, a.host, a.port);
        if (pre && pre.confirm) {
          if (!allEndpoints.confirmed?.has(pre.confirm)) {
            if (!(await confirmDlg(confirmMessage(pre)))) { cancelled = true; break; }
            (allEndpoints.confirmed ??= new Set()).add(pre.confirm);
          }
        } else if (pre && pre.error) {
          toast(msgText(pre), "err"); hadError = true; break;
        }
      }
      setBusy(d.id, true);
      try {
        const r = await api.call("do_toggle", d.id, on, a.host, a.port, a.scheme, a.user, a.password);
        if (r && r.error) { toast(msgText(r), "err"); hadError = true; break; }
        const restored = !!(r && r.restored);
        state[d.id] = on;
        if (on && !restored) lastApplied = Object.assign({}, a);
        if (restored) state[d.id] = true;
      } finally {
        setBusy(d.id, false);
      }
    }
    if (!hadError && !cancelled) {
      const scope = lane ? (lane === "win" ? t("scopeWin") : t("scopeWsl")) : t("scopeAll");
      toast(on ? t("laneOn", { scope }) : t("laneOff", { scope }), "ok");
    }
  } finally {
    allEndpoints.confirmed = null;
    setBusyAll(false);
    refreshAll();
  }
}

/* ═══════════════ Speed ticker ═══════════════ */
function fmtMB(num) {
  // 999.6 would round to "1000 B/s" — promote at the DISPLAY boundary
  // (same threshold as Python's format_speed).
  if (num < 999.5) return [num.toFixed(0), "B/s"];
  const units = [["KB", 1e3], ["MB", 1e6], ["GB", 1e9]];
  let v = num, lbl = "KB/s";
  for (const [u, f] of units) if (num >= f) { v = num / f; lbl = u + "/s"; } else break;
  // Promote when rounding produces "1000" (e.g. 999,999 B/s → 999.999 KB
  // → rounds to 1000 KB → should be 1.0 MB).
  const display = v >= 100 ? Math.round(v) : Math.round(v * 10) / 10;
  if (display >= 1000 && lbl === "KB/s") { v /= 1e3; lbl = "MB/s"; }
  else if (display >= 1000 && lbl === "MB/s") { v /= 1e3; lbl = "GB/s"; }
  return [v >= 100 ? v.toFixed(0) : v.toFixed(1), lbl];
}
/* Spark full-scale in bytes/sec, from the user's Mbps setting (default 40,
   i.e. 5 MB/s to match the popover's MB/s input).
   Starts as null until boot() loads the saved value; persistSoon skips it
   while null so a pre-boot debounce never overwrites the config. */
let speedFullMbps = null;
/* Last interface name the speed caption was rendered with, so a language
   switch can repaint it immediately instead of waiting up to 1 s for the next
   speed tick. */
let speedIface = "";
function renderSpeedCap() {
  const el = $("speedCap");
  if (!el) return;
  el.textContent = speedIface
    ? t("speedCapIface", { iface: speedIface })
    : t("speedCap");
}
const MbpsToBps = (m) => m * 1000 * 1000 / 8;
let speedLoopStarted = false;
function startSpeedLoop() {
  // tickSpeed self-reschedules via setTimeout, so re-booting (pywebviewready
  // superseding a mock boot) must not start a second loop (契约B3).
  if (speedLoopStarted) return;
  speedLoopStarted = true;
  tickSpeed();
}
async function tickSpeed() {
  try {
    const s = await api.call("get_speed");
    if (s) {
      const [dn, unit] = fmtMB(s.dn_num ?? 0);
      $("spdDn").textContent = dn;
      document.querySelector(".metric .unit").textContent = unit;
      $("spdUp").textContent = s.up;
      const full = Math.max(1, MbpsToBps(speedFullMbps ?? 40));
      $("sparkBar").style.transform = `scaleX(${Math.min(1, (s.dn_num ?? 0) / full)})`;
      const sb = $("sbPort");
      // The backend probes its cached address — show THAT port, not whatever
      // is sitting in the (possibly just-edited) field.
      sb.innerHTML = s.alive == null
        ? `<span class="dot n"></span>${t("detecting")}`
        : s.alive ? `<span class="dot g"></span>${t("online")} · ` + esc_(s.port || $("inPort").value || "7897")
                  : `<span class="dot r"></span>${t("noResponse")}`;
      speedIface = s.iface || "";
      renderSpeedCap();
    }
  } catch (e) { /* keep last values */ }
  setTimeout(tickSpeed, 1000);
}

/* Speed full-scale popover (anchored to the hero-right block) */
function mbpsToMBps(m) { return (Number(m) || 0) / 8; }
function mBpsToMbps(v) { return v * 8; }
function renderSpeedPop() { $("speedFullIn").value = String(Math.round(mbpsToMBps(speedFullMbps ?? 40) * 10) / 10); }
$("btnSpeedFull").onclick = (e) => {
  e.stopPropagation();
  renderSpeedPop();
  const pop = $("speedPop");
  pop.classList.toggle("show");
  if (pop.classList.contains("show")) $("speedFullIn").focus();
};
$("speedFullOk").onclick = () => {
  const v = parseFloat($("speedFullIn").value);
  if (!isFinite(v) || v <= 0 || v > 12500) { toast(t("speedFullBad"), "err"); return; }
  speedFullMbps = Math.round(mBpsToMbps(v) * 10) / 10;
  $("speedPop").classList.remove("show");
  persistSoon();
  toast(t("speedFullSet", { v: Math.round(v * 10) / 10 }), "ok");
};
$("speedFullIn").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("speedFullOk").click();
  if (e.key === "Escape") $("speedPop").classList.remove("show");
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".speed-pop") && !e.target.closest("#btnSpeedFull"))
    $("speedPop").classList.remove("show");
});

/* ═══════════════ Theme / pin / maximize / window controls ═══════════════ */
function applyTheme(mode) {
  document.documentElement.dataset.theme = mode;
  $("icoMoon").hidden = mode === "dark";
  $("icoSun").hidden = mode !== "dark";
}
async function toggleTheme() {
  const mode = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(mode);
  // Re-paint the native form ring (9px resize padding) to match the theme,
  // otherwise a light-launched window keeps a bright ring in dark mode.
  try { api.call("apply_form_back", mode); } catch (_) {}
  persistSoon();
}
function applyPin(on) {
  ui.pinned = !!on;
  $("btnPin").style.color = ui.pinned ? "var(--accent)" : "";
  $("btnPin").style.background = ui.pinned ? "var(--accent-soft)" : "";
}
async function togglePin() {
  applyPin(!ui.pinned);
  api.call("window_control", "pin", ui.pinned);
  persistSoon();
}
function applyMaximized(m) {
  ui.maximized = !!m;
  document.documentElement.classList.toggle("maximized", ui.maximized);
  $("icoMax").hidden = ui.maximized;
  $("icoRestore").hidden = !ui.maximized;
  $("btnMax").dataset.tip = ui.maximized ? t("tipRestore") : t("tipMax");
}
async function toggleMaximize() {
  const r = await api.call("window_control", "maximize");
  if (r && typeof r.maximized === "boolean") applyMaximized(r.maximized);
}

/* ═══════════════ Settings: background / opacity / autostart ═══════════════ */
function applyBg(path, dataUrl, dim) {
  cfg.bg_path = path || "";
  if (path && dataUrl && dataUrl !== "mock-bg") {
    $("bgLayer").style.backgroundImage = `url("${dataUrl}")`;
    document.body.classList.add("has-bg");
    $("bgName").textContent = path.split(/[\\/]/).pop();
  } else if (path && dataUrl === "mock-bg") {
    $("bgLayer").style.backgroundImage = "linear-gradient(135deg,#31427e,#0f1226)";
    document.body.classList.add("has-bg");
    $("bgName").textContent = "wallpaper.png";
  } else {
    document.body.classList.remove("has-bg");
    $("bgName").textContent = t("bgNone");
  }
  $("bgDim").style.opacity = dim ?? cfg.bg_dim;
  // The dim overlay is invisible without a background image — disable the
  // slider so the row doesn't read as broken.
  $("rgDim").disabled = !document.body.classList.contains("has-bg");
}
function applyAlpha(a) {
  cfg.card_alpha = a;
  document.documentElement.style.setProperty("--card-alpha", a);
}
/* Settings toggles are <button role="switch">: class and ARIA move together. */
function setTgl(el, on) {
  el.classList.toggle("on", on);
  el.setAttribute("aria-checked", String(!!on));
}
function debounce(fn, ms) {
  let h; return (...args) => { clearTimeout(h); h = setTimeout(() => fn(...args), ms); };
}
const saveSettings = debounce(() => {
  api.call("save_config", { bg_path: cfg.bg_path, bg_dim: cfg.bg_dim, card_alpha: cfg.card_alpha });
}, 350);

async function openSettings() {
  _settingsPrevFocus = document.activeElement;
  const c = await api.call("get_config");
  cfg.bg_dim = c.bg_dim ?? 0.45; cfg.card_alpha = c.card_alpha ?? 0.92;
  $("rgDim").value = Math.round(cfg.bg_dim * 100);
  $("rgDimVal").textContent = $("rgDim").value + "%";
  $("rgAlpha").value = Math.round(cfg.card_alpha * 100);
  $("rgAlphaVal").textContent = $("rgAlpha").value + "%";
  setTgl($("tglAuto"), !!c.autostart);
  setTgl($("tglQuitClear"), !!c.quit_clear);
  setTgl($("tglWslAll"), c.wsl_all_distros !== false);
  renderUpdateStatus();
  renderBackupList();
  if (c.bg_path) {
    const bg = await api.call("get_background");
    if (bg && bg.error) toast(msgText(bg), "err");
    applyBg(c.bg_path, bg && bg.data, cfg.bg_dim);
  } else applyBg("", null, cfg.bg_dim);
  $("settingsOv").classList.add("show");
  $("settingsClose").focus();
}
function closeSettings() {
  $("settingsOv").classList.remove("show");
  saveSettings();
  if (_settingsPrevFocus && _settingsPrevFocus.isConnected) _settingsPrevFocus.focus();
}
$("settingsOv").addEventListener("mousedown", (e) => {
  if (e.target === $("settingsOv")) closeSettings();
});

/* ═══════════════ Config backup / one-click restore ═══════════════ */
/* Backups live in a managed folder; the backend validates names. Restore
   routes through save_config, which never accepts ownership records from
   caller data — so a restore brings back the form + preferences but cannot
   resurrect a stale applied/restore record. Endpoints are left as-is; the
   user re-enables them, so syncFormAfterRestore must NOT call applyAddress. */
function fmtBackupTime(mtime) {
  const d = new Date(mtime * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

async function renderBackupList() {
  const el = $("backupList");
  if (!el) return;
  let list = [];
  try { list = (await api.call("list_backups")) || []; } catch (_) { list = []; }
  if (!list.length) { el.innerHTML = `<div class="empty-note">${t("noBackups")}</div>`; return; }
  el.innerHTML = list.map((b) =>
    `<div class="backup-item">
       <span class="bv">${esc_(fmtBackupTime(b.mtime))}<span class="bsz">${(b.size / 1024).toFixed(1)} KB</span></span>
       <span class="bacts">
         <button class="btn tiny outline" data-restore="${esc_(b.name)}">${t("restore")}</button>
         <button class="btn tiny subtle" data-delbackup="${esc_(b.name)}" data-tip="${t("delete")}">×</button>
       </span>
     </div>`).join("");
  el.querySelectorAll("[data-restore]").forEach((btn) => {
    btn.onclick = () => restoreBackup(btn.dataset.restore);
  });
  el.querySelectorAll("[data-delbackup]").forEach((btn) => {
    btn.onclick = async () => {
      const r = await api.call("delete_backup", btn.dataset.delbackup);
      if (r && r.ok) { renderBackupList(); toast(t("backupDeleted"), "ok"); }
      else toast(t("backupFail"), "err");
    };
  });
}

async function restoreBackup(name) {
  if (!(await confirmDlg(t("confirmRestore"), t("restore")))) return;
  let r;
  try { r = await api.call("restore_backup", name); }
  catch (e) { toast(t("switchFail", { err: e }), "err"); return; }
  if (!r || !r.ok) { toast(msgText(r || { error_key: "backupMissing" }), "err"); return; }
  await syncFormAfterRestore();
  toast(t("restoreDone"), "ok");
}

/* Refill the form + preferences from the restored config WITHOUT migrating
   endpoints: lastApplied stays the live applied record, so the switches keep
   reflecting what is really on the system. No applyAddress here. */
async function syncFormAfterRestore() {
  const c = await api.call("get_config");
  $("inHost").value = c.host; $("inPort").value = c.port;
  if ($("inScheme")) $("inScheme").value = c.scheme || "http";
  if ($("inUser")) $("inUser").value = c.user || "";
  if ($("inPass")) $("inPass").value = c.password || "";
  lastApplied = {
    host: String(c.applied_host || ""), port: String(c.applied_port || ""),
    scheme: c.applied_scheme || "http",
    user: c.applied_user || "", password: c.applied_password || "",
  };
  cfg.ports = (c.ports || []).map(String);
  cfg.quit_clear = !!c.quit_clear;
  cfg.wsl_all_distros = c.wsl_all_distros !== false;
  cfg.wsl_written = c.wsl_written || {};
  setTgl($("tglQuitClear"), cfg.quit_clear);
  setTgl($("tglWslAll"), cfg.wsl_all_distros);
  renderPorts(); renderProfiles(); renderEndpoints(); updateHero();
  refreshAll();
}

$("btnBackup").onclick = async () => {
  const btn = $("btnBackup");
  if (btn.disabled) return;
  btn.disabled = true;
  try {
    const r = await api.call("backup_config");
    if (r && r.ok) { toast(t("backupDone"), "ok"); renderBackupList(); }
    else toast(msgText(r), "err");
  } catch (_) { toast(t("backupFail"), "err"); }
  finally { btn.disabled = false; }
};

/* ═══════════════ Copy on click (and keyboard: Enter/Space on the value) ═══════════════ */
document.addEventListener("click", async (e) => {
  const el = e.target.closest("[data-copy]");
  if (!el) return;
  const text = el.textContent.trim();
  if (!text || /\s/.test(text) || !/\d/.test(text)) return;
  try {
    if (navigator.clipboard && location.protocol !== "file:") await navigator.clipboard.writeText(text);
    else await api.call("copy", text);
  } catch (_) { await api.call("copy", text); }
  el.classList.add("copied");
  const old = el.textContent;
  el.textContent = old + t("copied");
  setTimeout(() => {
    if (!el.textContent.includes("✓")) return;
    el.classList.remove("copied"); el.textContent = old;
  }, 1200);
});
document.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") &&
      e.target instanceof HTMLElement && e.target.hasAttribute("data-copy")) {
    e.preventDefault();
    e.target.click();
  }
});

/* Optional proxy-auth disclosure under the hero config row */
$("authToggle").onclick = () => {
  const row = $("authRow");
  const open = row.hidden;
  row.hidden = !open;
  $("authToggle").setAttribute("aria-expanded", String(open));
  if (open) $("inUser").focus();
};

/* ═══════════════ Wiring ═══════════════ */
/* Endpoint toggle: delegated on #epList so clicks survive innerHTML
   replacement during refreshAll (no stale closures on detached nodes). */
$("epList").addEventListener("click", (e) => {
  const el = e.target.closest(".tgl[data-ep]");
  if (el) toggleEndpoint(el.dataset.ep, !state[el.dataset.ep]);
});
/* 全部关闭 lives on the vertical rail (vtRailOff) now. */
$("btnTheme").onclick = toggleTheme;
$("btnPin").onclick = togglePin;
$("btnSettings").onclick = openSettings;
$("settingsClose").onclick = closeSettings;
$("btnUpdateCheck").onclick = doUpdateCheck;
$("btnUpdateDl").onclick = () => { api.call("open_release_page").catch(() => {}); };
$("btnMin").onclick = () => api.call("window_control", "minimize");
$("btnMax").onclick = toggleMaximize;
$("btnClose").onclick = async () => {
  if (!cfg.close_hint_done) {
    const go = await confirmDlg(t("closeHint"), t("hideToTray"));
    if (!go) return;
    cfg.close_hint_done = true;
    api.call("save_config", { close_hint_done: true });
  }
  api.call("window_control", "close");
};
$("btnWslRestart").onclick = async () => {
  if (busyAll || Object.values(state.busy).some(Boolean)) return;
  const go = await confirmDlg(t("restartConfirm"), t("restartWsl"));
  if (!go) return;
  setBusyAll(true);
  try {
    /* Rewrite uses applied_* on the Python side, not the in-progress form. */
    const r = await api.call("restart_wsl");
    if (r && r.error) toast(t("restartFail", { err: msgText(r) }), "err");
    else { toast(t("restartOk"), "ok"); refreshAll(); }
  } catch (e) {
    toast(t("restartFail", { err: e }), "err");
  } finally {
    setBusyAll(false);
  }
};
$("sbRefresh").onclick = () => refreshAll();
/* Click anywhere on a toast to dismiss it (long error text gets 8s, but no
   one should have to wait it out). */
$("toast").onclick = () => $("toast").classList.remove("show");
$("inHost").addEventListener("input", () => { $("inHost").classList.remove("bad"); renderEndpoints(); });
$("inPort").addEventListener("input", () => { $("inPort").classList.remove("bad"); renderPorts(); renderEndpoints(); });
["inHost", "inPort", "inScheme", "inUser", "inPass"].forEach((id) => {
  const el = $(id);
  if (!el) return;
  el.addEventListener("change", () => applyAddress(snapshotAddr(), lastApplied));
});

/* Channel page tabs (status bar) */
$("tabWin").onclick = () => setLane("win");
$("tabWsl").onclick = () => setLane("wsl");
/* Roving between the two tabs with arrow keys, per the tablist pattern. */
$("laneTabs").addEventListener("keydown", (e) => {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  e.preventDefault();
  setLane(curLane === "win" ? "wsl" : "win");
  $(curLane === "win" ? "tabWin" : "tabWsl").focus();
});

/* Per-variable connectivity test chips: click -> test_channel(which); the
   chip shows 测试中… while probing, then ✓ latency (green) or ✗ reason (red),
   reverting to 测试 after 3s. Same-source chips (e.g. HTTP/HTTPS_PROXY rows)
   share `data-test`, so a result is mirrored onto all of them. */
document.addEventListener("click", async (e) => {
  const chip = e.target.closest(".test-chip");
  if (!chip || chip.classList.contains("testing")) return;
  const which = chip.dataset.test;
  const chips = [...document.querySelectorAll(`.test-chip[data-test="${which}"]`)];
  chips.forEach((c) => { c.classList.remove("ok", "fail"); c.classList.add("testing"); c.textContent = t("testing"); });
  let r;
  try { r = await api.call("test_channel", which); }
  catch (err) { r = { ok: false, error: String(err) }; }
  chips.forEach((c) => {
    c.classList.remove("testing");
    if (r && r.ok) { c.classList.add("ok"); c.textContent = `✓ ${r.ms}ms`; c.title = ""; }
    else { c.classList.add("fail"); c.textContent = r && r.error ? "✗ " + r.error.slice(0, 40) : "✗"; c.title = (r && r.error) || ""; }
    setTimeout(() => { c.classList.remove("ok", "fail"); c.textContent = t("test"); }, 3000);
  });
});

/* ── Test all channels of the visible lane ── */
/* The per-variable chips map 1:1 onto js_api.test_channel's sources
   (Windows) + wsl_sources (WSL): every data-test in ui/index.html is one
   entry here, and tests/test_ui_test_all.py asserts that equality. */
const TEST_CHANNELS = {
  win: ["ps", "ps_https", "ps_all", "sys", "gitwin", "npm", "pip"],
  wsl: ["wsl", "wsl_https", "wsl_all", "gitwsl", "npmwsl", "pipwsl"],
};
/* Worst case per probe, measured on the backend side: the Windows curl probe
   walks 4 targets at --max-time 5 with an 8s subprocess timeout (~32s; the
   urllib fallback 4×5s), and the WSL probe runs 4×5s inside a 25s subprocess
   timeout. A serial sweep of 13 channels would therefore take 260-400s, so
   the probes run concurrently — and even then a hung proxy pins the batch at
   25-32s, hence the watchdog. js_api has NO cancel channel: an in-flight
   probe cannot be aborted, so this only guarantees the UI stops waiting and
   marks whatever is still pending as timed out. */
const _TEST_ALL_TIMEOUT_MS = 45000;
const TEST_HISTORY_MAX = 10;
/* Memory only: latencies and failure reasons never enter config.json (a
   sensitive file), never reach persistSoon's payload and are never sent to
   save_config — a reload clears the list. */
let testHistory = [];

function testChannelName(which) {
  const chip = document.querySelector(`.test-chip[data-test="${which}"]`);
  const row = chip && chip.closest(".kv");
  const k = row && row.querySelector(".k");
  return (k && k.textContent.trim()) || which;
}

/* Same ✓/✗ rendering as the single-chip handler, minus the 3s revert: a
   batch run's results stay readable until the next run. */
function paintTestChip(which, ok, ms, error) {
  document.querySelectorAll(`.test-chip[data-test="${which}"]`).forEach((c) => {
    c.classList.remove("testing");
    if (ok) { c.classList.add("ok"); c.textContent = `✓ ${ms}ms`; c.title = ""; }
    else { c.classList.add("fail"); c.textContent = error ? "✗ " + String(error).slice(0, 40) : "✗"; c.title = error || ""; }
  });
}

function pushTestHistory(which, ok, ms, error) {
  testHistory.unshift({ which, ok: !!ok, ms: ms || 0, error: error ? String(error) : "", at: Date.now() });
  if (testHistory.length > TEST_HISTORY_MAX) testHistory.length = TEST_HISTORY_MAX;
}

function renderHistory() {
  const ul = $("testHistory"), cap = $("testHistoryTitle"), clr = $("btnHistoryClear");
  if (!ul) return;
  // Lane-scoped: the WSL page never shows Windows-lane results and vice versa.
  const inLane = TEST_CHANNELS[curLane] || [];
  const rows = testHistory.filter((h) => inLane.includes(h.which));
  if (cap) cap.hidden = !rows.length;
  if (clr) clr.hidden = !rows.length;
  ul.hidden = !rows.length;
  const pad = (v) => String(v).padStart(2, "0");
  ul.innerHTML = rows.map((h) => {
    const d = new Date(h.at);
    const res = h.ok ? `✓ ${h.ms}ms` : `✗ ${esc_(h.error || t("unknown"))}`;
    return `<li class="${h.ok ? "ok" : "fail"}"><span class="th-ch">${esc_(testChannelName(h.which))}</span>`
      + `<span class="th-res">${res}</span><span class="th-at">${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}</span></li>`;
  }).join("");
}

async function testAllChannels() {
  const btn = $("btnTestAll");
  // busyAll covers batch toggles / port switches / WSL restarts; the button's
  // own testing class guards a double-click mid-run.
  if (busyAll || !btn || btn.classList.contains("testing")) return;
  const channels = TEST_CHANNELS[curLane] || [];
  if (!channels.length) return;
  setBusyAll(true);
  btn.disabled = true;
  btn.classList.add("testing");
  btn.textContent = t("testAllBusy");
  const done = new Set();
  let okCount = 0;
  // Per-channel settle: paints the chip and the history row the moment THAT
  // probe answers — no waiting for the whole batch, and one channel's throw
  // or {ok:false} never blocks the others.
  const settle = (which, ok, ms, error) => {
    if (done.has(which)) return;   // first result wins; the watchdog fills the rest
    done.add(which);
    if (ok) okCount++;
    paintTestChip(which, ok, ms, error);
    pushTestHistory(which, ok, ms, error);
    renderHistory();
  };
  const all = channels.map((which) => {
    document.querySelectorAll(`.test-chip[data-test="${which}"]`).forEach((c) => {
      c.classList.remove("ok", "fail");
      c.classList.add("testing");
      c.textContent = t("testing");
    });
    return api.call("test_channel", which).then((r) => {
      settle(which, !!(r && r.ok), r && r.ms, (r && r.error) || "");
    }).catch((err) => settle(which, false, 0, String(err)));
  });
  let timer = null;
  const watchdog = new Promise((res) => { timer = setTimeout(() => res("timeout"), _TEST_ALL_TIMEOUT_MS); });
  try {
    await Promise.race([Promise.all(all), watchdog]);
    for (const which of channels) {
      if (!done.has(which)) settle(which, false, 0, t("testAllTimeout"));
    }
    // Overall verdict so the user need not scan every chip: counts reflect the
    // final settled state, watchdog timeouts included as failures.
    const total = channels.length, fail = total - okCount;
    toast(fail ? t("testSummaryFail", { ok: okCount, total, fail })
               : t("testSummaryOk", { ok: okCount, total }),
          fail ? "err" : "ok");
  } finally {
    clearTimeout(timer);
    setBusyAll(false);
    btn.disabled = false;
    btn.classList.remove("testing");
    btn.textContent = t("testAll");
    renderEndpoints();
    renderHistory();
    // 批处理期间被 busyAll 挡下的地址切换在这里收尾（与 refreshAll 的 settle
    // 同口径）：否则要等下一次 refreshAll 才消费，"完成后自动应用" 就是空话。
    if (pendingApply) {
      const p = pendingApply; pendingApply = null;
      applyAddress(p.next, p.prev);
    }
  }
}
$("btnTestAll").onclick = () => testAllChannels();
/* Clear the (memory-only) result history for both lanes. Ephemeral and local,
   so no confirm: nothing persisted is lost. */
$("btnHistoryClear").onclick = () => { testHistory = []; renderHistory(); };

/* Title-bar drag + double-click: native-first. Python enables WebView2's
   IsNonClientRegionSupportEnabled and #dragRegion carries app-region: drag,
   so the browser hit-tests a press here as HTCAPTION and the OS runs its
   move loop in the very mousedown message — 1:1 tracking, and the OS itself
   decides double-click maximizes and edge-snapping (same channel as
   IssueCapture's Tauri native title bar). Presses claimed as non-client
   never reach the DOM, so this handler only fires when the native path is
   not in effect — it then injects drag_move as the fallback (kept because
   the two paths fight over nothing: whichever claims the press first owns
   the gesture). */
let _tbLastDown = 0;
$("dragRegion").addEventListener("mousedown", (e) => {
  if (e.button !== 0 || e.target.closest(".tb-btn, .app-icon")) return;
  const now = Date.now();
  if (now - _tbLastDown < 500) {
    _tbLastDown = 0;   // second click of a double-click: the OS will post
    return;            // WM_NCLBUTTONDBLCLK; do not restart the move loop
  }
  _tbLastDown = now;
  api.call("window_control", "drag_move");
});

/* Port preset popover: add / close-on-outside-click */
$("portAddBtn").onclick = () => {
  const v = $("portAddIn").value.trim();
  if (!/^\d+$/.test(v) || !(+v >= 1 && +v <= 65535)) { toast(t("badPort"), "err"); return; }
  if (cfg.ports.map(String).includes(v)) { toast(t("portExists"), "err"); return; }
  if (cfg.ports.length >= 12) { toast(t("portMax"), "err"); return; }
  cfg.ports.push(+v);
  $("portAddIn").value = "";
  renderPorts(); renderPortPop(); persistSoon();
};
$("portAddIn").addEventListener("keydown", (e) => { if (e.key === "Enter") $("portAddBtn").click(); });

/* Address preset popover: save the current form as a named preset */
$("profileAddBtn").onclick = async () => {
  const name = $("profileAddIn").value.trim();
  if (!name) { toast(t("presetBadName"), "err"); return; }
  const a = snapshotAddr();
  const r = await api.call("save_preset", name, a.host, a.port, a.scheme, a.user, a.password);
  if (r && r.ok) {
    $("profileAddIn").value = "";
    await loadPresets(); renderProfilePop();
    toast(t("presetSaved", { name }), "ok");
  } else toast(msgText(r), "err");
};
$("profileAddIn").addEventListener("keydown", (e) => { if (e.key === "Enter") $("profileAddBtn").click(); });
document.addEventListener("keydown", (e) => {
  if (!_dlgOpen) {
    if (e.key === "Escape" && $("settingsOv").classList.contains("show")) closeSettings();
    return;
  }
  if (e.key === "Escape") $("dlgCancel").click();
  // Enter confirms; skipped while the settings dialog is open (its inputs
  // have their own Enter handling).
  else if (e.key === "Enter" && !$("settingsOv").classList.contains("show")) {
    e.preventDefault();
    $("dlgOk").click();
  }
});
/* Global shortcuts: digits 1-9 pick the matching port preset (the chip
   tooltip shows its number), Esc closes the port/preset popovers. Ignored
   while typing or while a dialog/settings pane owns the keyboard, so it never
   fights normal text entry. */
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const tgt = e.target;
  if (tgt instanceof HTMLElement &&
      tgt.closest("input, textarea, select, [contenteditable='true']")) return;
  if (e.key === "Escape") {
    $("portPop").classList.remove("show");
    $("profilePop").classList.remove("show");
    return;
  }
  if (_dlgOpen || $("settingsOv").classList.contains("show")) return;
  if (!/^[1-9]$/.test(e.key)) return;
  const ports = cfg.ports || [];
  const idx = Number(e.key) - 1;
  if (idx < ports.length) {
    e.preventDefault();
    quickSwitchPort(ports[idx]);
  }
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".ports-wrap")) $("portPop").classList.remove("show");
  if (!e.target.closest(".profiles-wrap")) $("profilePop").classList.remove("show");
});

/* Settings wiring */
$("bgPick").onclick = async () => {
  const r = await api.call("pick_background");
  if (r && r.error) { toast(t("pickFail", { err: msgText(r) }), "err"); return; }
  if (r && r.ok) {
    cfg.bg_path = r.path || cfg.bg_path; // Python persisted the new path; mirror it
    const bg = await api.call("get_background");
    if (bg && bg.error) toast(msgText(bg), "err");
    applyBg(cfg.bg_path, bg && bg.data, cfg.bg_dim);
    saveSettings();
  }
};
$("bgClear").onclick = () => {
  cfg.bg_path = "";
  api.call("clear_background");
  applyBg("", null, cfg.bg_dim);
  saveSettings();
};
$("rgDim").addEventListener("input", (e) => {
  cfg.bg_dim = (+e.target.value) / 100;
  $("rgDimVal").textContent = e.target.value + "%";
  $("bgDim").style.opacity = cfg.bg_dim;
  saveSettings();
});
$("rgAlpha").addEventListener("input", (e) => {
  applyAlpha((+e.target.value) / 100);
  $("rgAlphaVal").textContent = e.target.value + "%";
  saveSettings();
});
$("tglAuto").onclick = async () => {
  const on = !$("tglAuto").classList.contains("on");
  const r = await api.call("set_autostart", on);
  if (r && r.ok) { setTgl($("tglAuto"), on); toast(on ? t("autoOn") : t("autoOff"), "ok"); }
  else toast(t("setFail", { err: (r && r.error) || t("unknown") }), "err");
};
$("tglQuitClear").onclick = () => {
  const on = !$("tglQuitClear").classList.contains("on");
  setTgl($("tglQuitClear"), on);
  cfg.quit_clear = on;
  api.call("save_config", { quit_clear: on });
  toast(on ? t("quitClearOn") : t("quitClearOff"), "ok");
};
$("tglWslAll").onclick = async () => {
  const on = !$("tglWslAll").classList.contains("on");
  setTgl($("tglWslAll"), on);
  cfg.wsl_all_distros = on;
  // The scope transition also cleans THIS TOOL's proxy config out of the
  // distros that just left scope (A1 contract): {attempted, left, errors}.
  // Say so — the user asked for a narrower scope, and silently leaving our
  // block behind in the other distros would be a lie by omission.
  const r = await api.call("save_config", { wsl_all_distros: on });
  const cu = r && r.wsl_cleanup;
  if (cu && (cu.attempted || []).length && !(cu.left || []).length && !(cu.errors || []).length) {
    toast(t("wslScopeCleaned"), "ok");
  } else if (cu && ((cu.left || []).length || (cu.errors || []).length)) {
    const errs = cu.errors || [];
    toast(t("setFail", { err: (errs.length ? msgText(errs[0]) : (cu.left || []).join("、")) || t("unknown") }), "err");
  } else {
    toast(on ? t("wslAllOn") : t("wslAllOff"), "ok");
  }
  if (curLane === "wsl") renderEndpoints();
};
if ($("inLang")) {
  $("inLang").addEventListener("change", () => {
    setLang($("inLang").value);
    renderUpdateStatus();
    renderPacHint();
    api.call("save_config", { lang });
    toast(t("langSet"), "ok");
  });
}

function esc_(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

/* ═══════════════ Boot ═══════════════ */
let booted = false;
let bootedMock = false;  // true when the current boot ran against the browser mock
async function boot() {
  if (booted) return; // pywebviewready and the browser fallback can both fire
  booted = true;
  bootedMock = !HAS_BRIDGE();
  const c = await api.call("get_config");
  const vEl = document.getElementById("appVersion");
  if (vEl && c.version) vEl.textContent = "v" + c.version;
  $("inHost").value = c.host; $("inPort").value = c.port;
  if ($("inScheme")) $("inScheme").value = c.scheme || "http";
  if ($("inUser")) $("inUser").value = c.user || "";
  if ($("inPass")) $("inPass").value = c.password || "";
  // The optional auth row stays expanded when the saved config actually uses it.
  if (c.user) {
    $("authRow").hidden = false;
    $("authToggle").setAttribute("aria-expanded", "true");
  }
  // 契约A2：无 applied 记录＝本工具从未写入端点，绝不能用表单地址回填
  // lastApplied（否则 appliedAddr() 把用户手工配置的同地址代理误判为已应用，
  // 点击关闭即删）。与 syncFormAfterRestore 同口径；表单与 applied 的差异由
  // 下方 applyAddress 迁移分支处理（switch_port 无记录时不认领任何端点）。
  lastApplied = {
    host: String(c.applied_host || ""),
    port: String(c.applied_port || ""),
    scheme: c.applied_scheme || "http",
    user: c.applied_user || "", password: c.applied_password || "",
  };
  setLang(c.lang || ((navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en"));
  if ($("inLang")) $("inLang").value = lang;
  if (c.config_load === "bak") toast(t("configRecovered"), "");
  else if (c.config_load === "corrupt") toast(t("configCorrupt"), "err");
  applyTheme(c.mode || "light");
  applyPin(!!c.pin);
  cfg.ports = (c.ports || []).map(String);
  cfg.close_hint_done = !!c.close_hint_done;
  cfg.quit_clear = !!c.quit_clear;
  cfg.wsl_all_distros = c.wsl_all_distros !== false;
  cfg.wsl_written = c.wsl_written || {};
  applyAlpha(c.card_alpha ?? 0.92);
  speedFullMbps = Math.max(0.1, +c.speed_full || 40);
  cfg.bg_dim = c.bg_dim ?? 0.45;
  if (c.bg_path) {
    const bg = await api.call("get_background");
    if (bg && bg.error) toast(msgText(bg), "err");
    applyBg(c.bg_path, bg && bg.data, cfg.bg_dim);
  } else applyBg("", null, cfg.bg_dim);  // initializes the dim-slider state
  /* Sync the maximize affordance with reality (a renderer reload resets it
     while the window may still be maximized). */
  try {
    const w = await api.call("window_control", "query");
    if (w && w.maximized) applyMaximized(true);
  } catch (_) { /* cosmetic only */ }
  renderPorts();
  loadPresets();
  renderEndpoints();
  renderHistory();
  // Form fields may have been persistSoon'd without switch_port. If they
  // differ from the last applied address, finish that migration now so a
  // restart cannot leave endpoints on the old port.
  const formAddr = snapshotAddr();
  if (addrKey(formAddr) !== addrKey(lastApplied)) {
    applyAddress(formAddr, lastApplied);
  } else {
    refreshAll();
  }
  // Win lane paints first; WSL counts fill in without blocking (and without
  // forcing the user onto the WSL tab). A cold WSL start still costs time,
  // but the dash (`–/4`) is only a brief placeholder.
  probeWslCounts();
  startSpeedLoop();
  checkUpdateOnce();
}

/* One update check per UI session, fired after boot. The backend call has a
   5s timeout and the result is cached there for 10 min; offline users see
   nothing (the "error" status is silent by design). The result is also kept
   in memory for the settings page's persistent status row — never persisted
   to config.json. */
let updateChecked = false;
let updateInfo = null;
function checkUpdateOnce() {
  if (updateChecked) return;
  updateChecked = true;
  api.call("check_update").then((r) => {
    updateInfo = r || null;
    renderUpdateStatus();
    if (r && r.status === "update" && r.latest)
      toast(t("updateAvail", { latest: r.latest }), "");
  }).catch(() => {});
}
function renderUpdateStatus() {
  const el = $("updateStatus"), dl = $("btnUpdateDl");
  if (!el) return;
  const r = updateInfo;
  if (!r) el.textContent = t("updateUnknown");
  else if (r.status === "latest") el.textContent = t("updateLatest");
  else if (r.status === "update") el.textContent = t("updateAvailStatus", { latest: r.latest || "" });
  else el.textContent = t("updateError");
  if (dl) dl.hidden = !(r && r.status === "update");
}
async function doUpdateCheck() {
  const btn = $("btnUpdateCheck"), el = $("updateStatus");
  if (btn && btn.disabled) return;
  if (btn) btn.disabled = true;
  if (el) el.textContent = t("updateChecking");
  try {
    updateInfo = await api.call("check_update");
  } catch (_) {
    updateInfo = { status: "error" };
  }
  renderUpdateStatus();
  if (btn) btn.disabled = false;
}

function probeWslCounts() {
  const paint = () => {
    updateHero();
    if (curLane === "wsl") renderEndpoints();
  };
  const take = (p, fn) => p.then(fn).catch(() => { wslProbed = true; paint(); });
  // Wait for wsl_written so NAT badges don't flicker OFF on a stale record.
  api.call("get_config").then((c) => {
    if (c) cfg.wsl_written = c.wsl_written || {};
  }).catch(() => {}).finally(() => {
    take(api.call("refresh_item", "wsl"), (r) => {
      wslProbed = true;
      if (r) {
        state.wsl = !!r.on;
        detected.gitwsl = r.git;
        state.gitwsl = urlLooksOurs(r.git, null, true, cfg.wsl_written.gitwsl);
        detected.wsl_url = state.wsl ? r.vars.HTTP_PROXY : null;
      }
      paint();
    });
    take(api.call("refresh_item", "npmwsl"), (v) => {
      detected.npmwsl = v;
      state.npmwsl = urlLooksOurs(v, null, true, cfg.wsl_written.npmwsl);
      wslProbed = true;
      paint();
    });
    take(api.call("refresh_item", "pipwsl"), (v) => {
      detected.pipwsl = v;
      state.pipwsl = urlLooksOurs(v, null, true, cfg.wsl_written.pipwsl);
      wslProbed = true;
      paint();
    });
  });
}

if (HAS_BRIDGE()) boot();
else window.addEventListener("pywebviewready", () => {
  // pywebview injects the bridge and fires this after page load. If the 350ms
  // mock fallback booted first, the real bridge must take over: reset the gate
  // and re-boot against the real api, or the UI stays on fake mock data with
  // every action silently no-opping and zero system writes (契约B3). Only a
  // prior MOCK boot is superseded — a real boot is never re-run.
  if (bootedMock) { booted = false; bootedMock = false; }
  boot();
});
/* Browser fallback: if no bridge ever arrives, boot with mock after a beat. */
setTimeout(() => { if (!HAS_BRIDGE() && !booted) boot(); }, 350);
