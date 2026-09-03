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
    lip: "192.168.1.146", wip: "172.24.156.88", wmode: "Mirrored",
    psHttp: "http://127.0.0.1:7897",
    wslHttp: "未设置", wslGit: "未设置", gitWinVal: "未设置",
    npmVal: "未设置", pipVal: "未设置", npmWslVal: "未设置", pipWslVal: "未设置",
    alive: true, iface: "以太网",
  };
  const delay = (ms) => new Promise(res => setTimeout(res, ms));
  async function mock(name, ...args) {
    await delay(name === "refresh_item" && args[0] === "wsl" ? 1200 : 150);
    switch (name) {
      case "get_config": return { host: mockState.host, port: mockState.port,
        scheme: mockState.scheme || "http", user: mockState.user || "",
        password: mockState.password || "", lang: mockState.lang || "zh",
        mode: mockState.mode, pin: mockState.pin, ports: mockState.ports,
        autostart: mockState.autostart, bg_path: "", bg_dim: 0.45, card_alpha: 0.92,
        speed_full: 5, quit_clear: false, wsl_all_distros: true, close_hint_done: false };
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
        if (w === "sys") return mockState.ps ? mockState.psHttp : "未设置";
        if (w === "npmwsl") return mockState.npmWslVal;
        if (w === "pipwsl") return mockState.pipWslVal;
        if (w === "ip") return mockState.lip;
        if (w === "mode") return mockState.wmode;
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
        if (target === "ps") { mockState.ps = on; mockState.psHttp = on ? mockUrl() : "未设置"; }
        if (target === "sys") { mockState.sys = on; }
        if (target === "wsl") { mockState.wsl = on; mockState.wslHttp = on ? mockUrl() : "未设置"; }
        if (target === "gitwin") { mockState.gitwin = on; mockState.gitWinVal = on ? mockUrl() : "未设置"; }
        if (target === "gitwsl") { mockState.gitwsl = on; mockState.wslGit = on ? mockUrl() : "未设置"; }
        if (target === "npm") { mockState.npm = on; mockState.npmVal = on ? mockUrl() : "未设置"; }
        if (target === "pip") { mockState.pip = on; mockState.pipVal = on ? mockUrl() : "未设置"; }
        if (target === "npmwsl") { mockState.npmwsl = on; mockState.npmWslVal = on ? mockUrl() : "未设置"; }
        if (target === "pipwsl") { mockState.pipwsl = on; mockState.pipWslVal = on ? mockUrl() : "未设置"; }
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
        return { ok: true, applied: ["ps", "sys", "wsl", "gitwin", "gitwsl", "npm", "pip", "npmwsl", "pipwsl"].filter(t => mockState[t]) };
      }
      case "get_speed": {
        const n = Math.random() * 3;
        return { up: `${(n * 0.3).toFixed(1)} MB/s`, dn: `${n.toFixed(1)} MB/s`,
                 dn_num: n * 1024 * 1024, iface: mockState.iface, alive: mockState.alive };
      }
      case "restart_wsl": await delay(1500); return { ok: true };
      case "window_control": {
        if (args[0] === "maximize") mockState._max = !mockState._max;
        return { ok: true, maximized: !!mockState._max, pinned: args[0] === "pin" ? !!args[1] : false };
      }
      case "pick_background": await delay(600); return { ok: true, name: "wallpaper.png" };
      case "get_background": return { data: "mock-bg" };
      case "clear_background": return true;
      case "set_autostart": mockState.autostart = !!args[0]; return { ok: true };
      case "copy": return true;
      default: return null;
    }
  }
  return { call };
})();

/* ═══════════════ Helpers ═══════════════ */
const $ = (id) => document.getElementById(id);
const UNSET = new Set(["未设置", "未检测到", "—", "", "Not set", "Not detected"]);

function toast(msg, kind = "") {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show " + kind;
  clearTimeout(t._h);
  // Errors get 8s (multi-endpoint failures concatenate into long text);
  // clicking anywhere on a toast dismisses it immediately.
  t._h = setTimeout(() => t.classList.remove("show"), kind === "err" ? 8000 : 2600);
}

/* Tooltip engine */
(() => {
  const tip = $("tooltip");
  let cur = null;
  document.addEventListener("mouseover", (e) => {
    const el = e.target.closest("[data-tip]");
    if (el === cur) return;
    cur = el;
    tip.classList.remove("show");
    if (!el) return;
    tip.textContent = el.dataset.tip;
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
  });
})();

/* Confirm dialog (Promise-based, replaces native messagebox). Requests are
   queued: overlapping confirmations would otherwise swallow the first. */
const _dlgQueue = [];
let _dlgOpen = false;
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
  $("dlgText").textContent = text;
  $("dlgOk").textContent = okText;
  $("overlay").classList.add("show");
  const done = (v) => {
    $("overlay").classList.remove("show");
    _dlgOpen = false;
    resolve(v);
    _pumpDlg();
  };
  $("dlgOk").onclick = () => done(true);
  $("dlgCancel").onclick = () => done(false);
}

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
const cfg = { ports: [], bg_path: "", bg_dim: 0.45, card_alpha: 0.92, close_hint_done: false, quit_clear: false, wsl_all_distros: true };
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
    <div class="tgl ${on ? "on" : ""}" id="sw-${d.id}" data-ep="${d.id}" data-tip="${on ? t("clickOff") : t("clickOn")}"></div>
  </div>`;
}
function renderEndpoints() {
  const host = $("epList");
  host.innerHTML = EP_DEFS.filter((d) => d.lane === curLane).map(epCard).join("");
  // Click handlers are delegated on #epList (see Wiring section) so they
  // survive innerHTML replacement during refreshAll without stale closures.
  $("epCap").textContent = curLane === "win"
    ? t("epCapWin")
    : (cfg.wsl_all_distros !== false ? t("epCapWslAll") : t("epCapWslDef"));
  $("epTitle").textContent = curLane === "win" ? t("epTitleWin") : t("epTitleWsl");
  updateHero();
}

/* Page switch: endpoints + info cards + hero accent all follow the lane. */
function setLane(lane) {
  if (curLane === lane) return;
  curLane = lane;
  $("tabWin").classList.toggle("active", lane === "win");
  $("tabWsl").classList.toggle("active", lane === "wsl");
  $("netWin").style.display = lane === "win" ? "" : "none";
  $("netWsl").style.display = lane === "wsl" ? "" : "none";
  $("varsWin").style.display = lane === "win" ? "" : "none";
  $("varsWsl").style.display = lane === "wsl" ? "" : "none";
  // 重启 WSL lives on the status bar but only concerns the WSL page.
  $("btnWslRestart").style.display = lane === "wsl" ? "" : "none";
  renderEndpoints();  // updateHero refreshes sbGit/sbNpm/sbPip for the lane
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
      toast(pre.error_key ? t(pre.error_key) : pre.error, "err"); return;
    }
  }
  setBusy(ep, true);
  try {
    const r = await api.call("do_toggle", ep, on, a.host, a.port, a.scheme, a.user, a.password);
    if (r && r.error) toast(r.error_key ? t(r.error_key) : r.error, "err");
    else {
      // Flip the row immediately; a refresh corrects it. A disable that
      // restored a stashed proxy actually leaves the endpoint ON.
      state[ep] = on;
      if (r && (r.restored || (r.note && r.note.includes("已恢复覆盖前的代理")))) {
        state[ep] = true;
        toast(r.note || t("restored"), "ok");
      } else if (r && r.note) {
        toast(r.note, "ok");
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
  const chips = cfg.ports.map((p) =>
    `<button class="pchip ${String(p) === cur ? "active" : ""}" data-port="${p}" data-tip="${t("tipSwitchPort", { port: p })}">${p}</button>`).join("");
  $("portChips").innerHTML = chips + `<button class="pchip add" id="portManage" data-tip="${t("tipManagePorts")}">＋</button>`;
  $("portChips").querySelectorAll(".pchip[data-port]").forEach((el) => {
    el.onclick = () => quickSwitchPort(el.dataset.port);
  });
  $("portManage").onclick = (e) => { e.stopPropagation(); renderPortPop(); $("portPop").classList.toggle("show"); };
}

function renderPortPop() {
  const cur = $("inPort").value.trim();
  $("portPopList").innerHTML = cfg.ports.map((p) =>
    `<div class="row"><span class="pv">${p}${String(p) === cur ? ' <span style="color:var(--accent);font-size:10.5px">' + t("current") + '</span>' : ""}</span>
     <span class="del" data-del="${p}" data-tip="${t("removePreset")}">×</span></div>`).join("")
    || `<div style="font-size:11.5px;color:var(--text3);padding:2px 0">${t("noPresets")}</div>`;
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
function expectedUrl(kind) {
  const a = snapshotAddr();
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
  return `${a.scheme}://${auth}${a.host}:${a.port}`;
}
function confirmMessage(pre) {
  if (!pre) return "";
  if (pre.confirm === "port") return t("confirmPort", { host: pre.host || "", port: pre.port || "" });
  if (pre.confirm === "shell") return t("confirmShell", { shell: pre.shell || "" });
  if (pre.confirm === "npm") return t("confirmNpm");
  return pre.message || "";
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
  const enabled = EP_DEFS.map((d) => d.id).filter((k) => state[k]);
  const shown = `${next.scheme}://${next.host}:${next.port}`;
  if (enabled.length > 0) {
    const go = await confirmDlg(t("applyAddr", { addr: shown }), t("apply"));
    if (!go) {
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
  }
  setBusyAll(true);
  try {
    const r = await api.call("switch_port", next.port, next.host, next.scheme, next.user, next.password);
    if (r && r.error) {
      const n = (r.applied || []).length;
      toast(n ? t("switchedPartial", { addr: shown, n, err: r.error }) : r.error, "err");
      if (!(r.applied && r.applied.length)) {
        if (prev) {
          $("inHost").value = prev.host;
          $("inPort").value = prev.port;
        }
      } else {
        lastApplied = Object.assign({}, next);
      }
    } else {
      lastApplied = Object.assign({}, next);
      const n = (r && r.applied || []).length;
      toast(n ? t("switched", { addr: shown, n }) : t("addrSet", { addr: shown }), "ok");
    }
  } catch (e) {
    toast(t("switchFail", { err: e }), "err");
  } finally { setBusyAll(false); }
  renderPorts();
  refreshAll();
}

async function quickSwitchPort(p) {
  const prev = snapshotAddr();
  if (String(p) === prev.port) return;
  $("inPort").value = String(p);
  renderPorts();
  await applyAddress(snapshotAddr(), prev);
}

function setBusyAll(b) {
  busyAll = b;
  for (const rid of ["vtRailOn", "vtRailOff"]) {
    const rail = $(rid);
    if (rail) {
      rail.style.opacity = b ? ".55" : "";
      rail.style.pointerEvents = b ? "none" : "";
    }
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
  const track = (p) => { pending++; p.finally(settle); };
  const set = (id, v) => {
    const el = $(id); if (!el) return;
    el.textContent = displayStatus(v);
    el.classList.toggle("unset", UNSET.has(String(v)));
  };
  let anyOk = false;
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
    renderEndpoints();
  }).catch(() => {}));
  if (lane === "win") track(api.call("refresh_item", "sys").then((v) => {
    anyOk = true;
    const expect = expectedUrl("sys");
    detected.sys = v;
    state.sys = String(v) === expect;
    set("vSys", v);
    renderEndpoints();
  }).catch(() => {}));
  if (lane === "wsl") track(api.call("refresh_item", "wsl").then((r) => {
    anyOk = true;
    wslProbed = true;  // the –/4 badge becomes real counts after first probe
    state.wsl = !!r.on;
    detected.gitwsl = r.git;
    state.gitwsl = !UNSET.has(r.git); // mirror the actually detected state
    detected.wsl_url = state.wsl ? r.vars.HTTP_PROXY : null;
    set("vWip", r.ip);
    set("vWslHttp", r.vars.HTTP_PROXY); set("vWslHttps", r.vars.HTTPS_PROXY);
    if (r.vars.ALL_PROXY) set("vWslAll", r.vars.ALL_PROXY);
    set("vWslGit", r.git);
    renderEndpoints();
  }).catch(() => {}));
  if (lane === "win") track(api.call("refresh_item", "gitwin").then((v) => {
    anyOk = true;
    detected.gitwin = v;
    state.gitwin = !UNSET.has(v);
    set("vPsGit", v);
    renderEndpoints();
  }).catch(() => {}));
  if (lane === "win") track(api.call("refresh_item", "npm").then((v) => {
    anyOk = true;
    detected.npm = v;
    state.npm = !UNSET.has(v);
    set("vNpm", v);
    renderEndpoints();
  }).catch(() => {}));
  if (lane === "win") track(api.call("refresh_item", "pip").then((v) => {
    anyOk = true;
    detected.pip = v;
    state.pip = !UNSET.has(v);
    set("vPip", v);
    renderEndpoints();
  }).catch(() => {}));
  if (lane === "wsl") track(api.call("refresh_item", "npmwsl").then((v) => {
    anyOk = true;
    detected.npmwsl = v;
    state.npmwsl = !UNSET.has(v);
    set("vNpmWsl", v);
    renderEndpoints();
  }).catch(() => {}));
  if (lane === "wsl") track(api.call("refresh_item", "pipwsl").then((v) => {
    anyOk = true;
    detected.pipwsl = v;
    state.pipwsl = !UNSET.has(v);
    set("vPipWsl", v);
    renderEndpoints();
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
      if (ok === false) toast(t("saveFail"), "err");
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
          toast(pre.error_key ? t(pre.error_key) : pre.error, "err"); hadError = true; break;
        }
      }
      setBusy(d.id, true);
      try {
        const r = await api.call("do_toggle", d.id, on, a.host, a.port, a.scheme, a.user, a.password);
        if (r && r.error) { toast(r.error_key ? t(r.error_key) : r.error, "err"); hadError = true; break; }
        state[d.id] = on;
        if (r && (r.restored || (r.note && r.note.includes("已恢复覆盖前的代理")))) state[d.id] = true;
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
  if (num < 1000) return [num.toFixed(0), "B/s"];
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
const MbpsToBps = (m) => m * 1000 * 1000 / 8;
async function tickSpeed() {
  try {
    const s = await api.call("get_speed");
    if (s) {
      const [dn, unit] = fmtMB(s.dn_num ?? 0);
      $("spdDn").textContent = dn;
      document.querySelector(".metric .unit").textContent = unit;
      $("spdUp").textContent = s.up;
      const full = Math.max(1, MbpsToBps(speedFullMbps ?? 40));
      $("sparkBar").style.width = Math.min(100, (s.dn_num ?? 0) / full * 100) + "%";
      const sb = $("sbPort");
      // The backend probes its cached address — show THAT port, not whatever
      // is sitting in the (possibly just-edited) field.
      sb.innerHTML = s.alive == null
        ? `<span class="dot n"></span>${t("detecting")}`
        : s.alive ? `<span class="dot g"></span>${t("online")} · ` + esc_(s.port || $("inPort").value || "7897")
                  : `<span class="dot r"></span>${t("noResponse")}`;
      if (s.iface) $("speedCap").textContent = t("speedCapIface", { iface: s.iface });
      else $("speedCap").textContent = t("speedCap");
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
  $("icoMoon").style.display = mode === "dark" ? "none" : "";
  $("icoSun").style.display = mode === "dark" ? "" : "none";
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
  $("icoMax").style.display = ui.maximized ? "none" : "";
  $("icoRestore").style.display = ui.maximized ? "" : "none";
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
function debounce(fn, ms) {
  let h; return (...args) => { clearTimeout(h); h = setTimeout(() => fn(...args), ms); };
}
const saveSettings = debounce(() => {
  api.call("save_config", { bg_path: cfg.bg_path, bg_dim: cfg.bg_dim, card_alpha: cfg.card_alpha });
}, 350);

async function openSettings() {
  const c = await api.call("get_config");
  cfg.bg_dim = c.bg_dim ?? 0.45; cfg.card_alpha = c.card_alpha ?? 0.92;
  $("rgDim").value = Math.round(cfg.bg_dim * 100);
  $("rgDimVal").textContent = $("rgDim").value + "%";
  $("rgAlpha").value = Math.round(cfg.card_alpha * 100);
  $("rgAlphaVal").textContent = $("rgAlpha").value + "%";
  $("tglAuto").classList.toggle("on", !!c.autostart);
  $("tglQuitClear").classList.toggle("on", !!c.quit_clear);
  $("tglWslAll").classList.toggle("on", c.wsl_all_distros !== false);
  if (c.bg_path) {
    const bg = await api.call("get_background");
    if (bg && bg.error) toast(bg.error, "err");
    applyBg(c.bg_path, bg && bg.data, cfg.bg_dim);
  } else applyBg("", null, cfg.bg_dim);
  $("settingsOv").classList.add("show");
}
function closeSettings() {
  $("settingsOv").classList.remove("show");
  saveSettings();
}
$("settingsOv").addEventListener("mousedown", (e) => {
  if (e.target === $("settingsOv")) closeSettings();
});

/* ═══════════════ Copy on click ═══════════════ */
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
    /* Flush the current address: the rewrite after restart uses it. */
    await api.call("save_config", snapshotAddr());
    const r = await api.call("restart_wsl");
    if (r && r.error) toast(t("restartFail", { err: r.error }), "err");
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
document.addEventListener("keydown", (e) => {
  if (!_dlgOpen) return;
  if (e.key === "Escape") $("dlgCancel").click();
  // Enter confirms; skipped while the settings dialog is open (its inputs
  // have their own Enter handling).
  else if (e.key === "Enter" && !$("settingsOv").classList.contains("show")) {
    e.preventDefault();
    $("dlgOk").click();
  }
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".ports-wrap")) $("portPop").classList.remove("show");
});

/* Settings wiring */
$("bgPick").onclick = async () => {
  const r = await api.call("pick_background");
  if (r && r.error) { toast(t("pickFail", { err: r.error }), "err"); return; }
  if (r && r.ok) {
    cfg.bg_path = r.path || cfg.bg_path; // Python persisted the new path; mirror it
    const bg = await api.call("get_background");
    if (bg && bg.error) toast(bg.error, "err");
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
  if (r && r.ok) { $("tglAuto").classList.toggle("on", on); toast(on ? t("autoOn") : t("autoOff"), "ok"); }
  else toast(t("setFail", { err: (r && r.error) || t("unknown") }), "err");
};
$("tglQuitClear").onclick = () => {
  const on = !$("tglQuitClear").classList.contains("on");
  $("tglQuitClear").classList.toggle("on", on);
  cfg.quit_clear = on;
  api.call("save_config", { quit_clear: on });
  toast(on ? t("quitClearOn") : t("quitClearOff"), "ok");
};
$("tglWslAll").onclick = () => {
  const on = !$("tglWslAll").classList.contains("on");
  $("tglWslAll").classList.toggle("on", on);
  cfg.wsl_all_distros = on;
  api.call("save_config", { wsl_all_distros: on });
  toast(on ? t("wslAllOn") : t("wslAllOff"), "ok");
  if (curLane === "wsl") renderEndpoints();
};
if ($("inLang")) {
  $("inLang").addEventListener("change", () => {
    setLang($("inLang").value);
    api.call("save_config", { lang });
    toast(t("langSet"), "ok");
  });
}

function esc_(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

/* ═══════════════ Boot ═══════════════ */
let booted = false;
async function boot() {
  if (booted) return; // pywebviewready and the browser fallback can both fire
  booted = true;
  const c = await api.call("get_config");
  const vEl = document.getElementById("appVersion");
  if (vEl && c.version) vEl.textContent = "v" + c.version;
  $("inHost").value = c.host; $("inPort").value = c.port;
  if ($("inScheme")) $("inScheme").value = c.scheme || "http";
  if ($("inUser")) $("inUser").value = c.user || "";
  if ($("inPass")) $("inPass").value = c.password || "";
  lastApplied = {
    host: String(c.host || ""), port: String(c.port || ""),
    scheme: c.scheme || "http", user: c.user || "", password: c.password || "",
  };
  setLang(c.lang || ((navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en"));
  if ($("inLang")) $("inLang").value = lang;
  applyTheme(c.mode || "light");
  applyPin(!!c.pin);
  cfg.ports = (c.ports || []).map(String);
  cfg.close_hint_done = !!c.close_hint_done;
  cfg.quit_clear = !!c.quit_clear;
  cfg.wsl_all_distros = c.wsl_all_distros !== false;
  applyAlpha(c.card_alpha ?? 0.92);
  speedFullMbps = Math.max(0.1, +c.speed_full || 40);
  cfg.bg_dim = c.bg_dim ?? 0.45;
  if (c.bg_path) {
    const bg = await api.call("get_background");
    if (bg && bg.error) toast(bg.error, "err");
    applyBg(c.bg_path, bg && bg.data, cfg.bg_dim);
  } else applyBg("", null, cfg.bg_dim);  // initializes the dim-slider state
  /* Sync the maximize affordance with reality (a renderer reload resets it
     while the window may still be maximized). */
  try {
    const w = await api.call("window_control", "query");
    if (w && w.maximized) applyMaximized(true);
  } catch (_) { /* cosmetic only */ }
  renderPorts();
  renderEndpoints();
  refreshAll();
  tickSpeed();
}

if (HAS_BRIDGE()) boot();
else window.addEventListener("pywebviewready", boot);
/* Browser fallback: if no bridge ever arrives, boot with mock after a beat. */
setTimeout(() => { if (!HAS_BRIDGE() && !booted) boot(); }, 350);
