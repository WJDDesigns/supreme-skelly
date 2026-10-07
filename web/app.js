// Supreme Skelly web UI. No build step: plain ES modules talking to /api.

import { skeletonSVG, updateSkeleton } from "./skeleton.js";

const $ = (sel) => document.querySelector(sel);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};

const state = { device: null, profile: null, look: null, moves: new Set(), light: "all", mode: null, eye: null, scene: null };

// One-tap looks. `effect` is matched to whatever this prop calls its modes.
const SCENES = [
  { key: "toxic", name: "Toxic Green", emoji: "🧪", color: "#00ff3c", effect: "solid" },
  { key: "blood", name: "Blood Moon", emoji: "🩸", color: "#ff0000", effect: "pulse" },
  { key: "pumpkin", name: "Pumpkin", emoji: "🎃", color: "#ff6a00", effect: "solid" },
  { key: "ghost", name: "Ghostly", emoji: "👻", color: "#cfe8ff", effect: "pulse" },
  { key: "witch", name: "Witchy", emoji: "🔮", color: "#8a2be2", effect: "pulse" },
  { key: "frozen", name: "Frozen", emoji: "🧊", color: "#00c8ff", effect: "solid" },
  { key: "storm", name: "Lightning", emoji: "⚡", color: "#ffffff", effect: "strobe" },
  { key: "party", name: "Party", emoji: "🌈", color: "#ff004c", effect: "solid", cycle: true },
];
const EYE_EMOJI = {
  "Blue Eyes": "🔵", "Hazel Eyes": "🟤", "Green Eyes": "🟢", "Orange Eyes": "🟠", "Red Eyes": "🔴",
  "Grey Eyes": "⚪", "Brown Eyes": "🟤", "Yellow Reptile Eye": "🦎", "Orange Reptile Eye": "🐍",
  "Rainbow Swirl": "🌀", "Flames": "🔥", "Gold Star": "⭐", "Skull and Crossbones": "☠️", "Fireworks": "🎆",
  "American Flag": "🇺🇸", "Heart": "❤️", "Four-Leaf Clover": "🍀", "Snowflake": "❄️", "Confetti": "🎉",
  "Ice Eye": "🧊", "Peppermint Swirl": "🍬", "Cyber Eye": "🤖",
};
const EFFECT_LABELS = { solid: ["Static"], pulse: ["Pulsing"], strobe: ["Strobe", "Flickering"], flicker: ["Flickering"] };

// ---------- API ----------
async function api(path, body, method = body === undefined ? "GET" : "POST") {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}

async function run(btn, fn, okMsg) {
  if (btn) btn.disabled = true;
  try {
    const out = await fn();
    if (okMsg) toast(okMsg);
    return out;
  } catch (e) {
    toast(e.message, true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

let toastTimer;
function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", error);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

// ---------- tabs ----------
document.querySelectorAll(".tabs button").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.tab)),
);
function showTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((s) => (s.hidden = s.id !== `tab-${name}`));
  try { localStorage.setItem("tab", name); } catch {}
}
try { showTab(localStorage.getItem("tab") || "device"); } catch { showTab("device"); }
$("#tab-controls").classList.add("disabled-when-offline");
$("#tab-sounds").classList.add("disabled-when-offline");
$("#conn-pill").addEventListener("click", () => showTab("device"));

// ---------- live skeleton ----------
$("#skelly-preview").innerHTML = skeletonSVG();
function renderPreview() {
  const root = $("#skelly-preview");
  updateSkeleton(root, { look: state.look, moves: state.moves, profile: state.profile });
  const cs = getComputedStyle(root);
  $("#lg-chest").style.background = cs.getPropertyValue("--chest");
  $("#lg-chest").style.color = cs.getPropertyValue("--chest");
  $("#lg-mouth").style.background = cs.getPropertyValue("--mouth");
  $("#lg-mouth").style.color = cs.getPropertyValue("--mouth");
  const eye = state.profile?.eyes?.find((e) => e.value === state.look?.eye);
  $("#lg-eye").textContent = eye ? eye.label : "Eyes";
  const fx = root.querySelector(".chest-fx").dataset.effect;
  $("#preview-fx").textContent = fx === "cycle" ? "Party" : fx ? fx[0].toUpperCase() + fx.slice(1) : "Static";
  const parts = [...state.moves].map((k) => state.profile?.movements?.find((m) => m.key === k)?.label).filter(Boolean);
  $("#preview-sub").textContent = parts.length ? `Moving: ${parts.join(", ")}` : "What Skelly looks like right now";
}

// ---------- header ----------
const hour = new Date().getHours();
$("#greeting").textContent = `Good ${hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening"}!`;
$("#foot-host").textContent = location.host;

// ---------- rendering ----------
const STATUS_TEXT = {
  disconnected: "Not connected",
  scanning: "Looking for Skelly…",
  connecting: "Connecting…",
  reconnecting: "Reconnecting…",
};

// Bluetooth signal strength (dBm) in words and 1-4 bars; matches the scan list's labels.
function signal(rssi) {
  if (rssi == null) return null;
  const bars = rssi >= -60 ? 4 : rssi >= -70 ? 3 : rssi >= -80 ? 2 : 1;
  const [word, level] = rssi > -65 ? ["Strong", "good"] : rssi > -80 ? ["Fair", "fair"] : ["Weak", "weak"];
  return { bars, word, level, text: `${word} signal (${rssi} dBm)` };
}

function paintBars(node, s) {
  node.dataset.bars = s ? s.bars : 0;
  node.dataset.level = s ? s.level : "";
}

// Light the first n of a .bars meter's bars for a 0..1 level (at least one when above zero).
function meter(node, level) {
  const bars = node.querySelectorAll("i");
  const v = Math.max(0, Math.min(1, level || 0));
  const n = v > 0 ? Math.max(1, Math.round(v * bars.length)) : 0;
  bars.forEach((b, i) => b.classList.toggle("on", i < n));
}

function renderSignal(d, online) {
  const s = online ? signal(d.rssi) : null;
  const pill = $("#pill-sig");
  pill.hidden = !s;
  paintBars(pill, s);
  $("#conn-pill").title = s ? s.text : "";
  // Dashboard meter: -95 dBm (barely there) to -45 dBm (right next to it) across 8 bars.
  const conn = $("#meter-conn");
  meter(conn, s ? (d.rssi + 95) / 50 : 0);
  conn.dataset.level = s?.level ?? "";
  conn.closest(".stat").title = s ? s.text : "";
  if (s) $("#st-conn-sub").textContent = `${s.word} signal`;
  $("#dev-signal").textContent = s ? s.text : online ? "Checking…" : "–";
}

function renderDevice() {
  const d = state.device;
  if (!d) return;
  const online = d.status === "connected";
  document.body.classList.toggle("online", online);
  $("#conn-pill").dataset.status = d.status;
  $("#conn-text").textContent = online ? d.name || "Connected" : STATUS_TEXT[d.status] || d.status;
  $("#device-card").hidden = !(online || d.status === "reconnecting");
  $("#dev-name").textContent = d.name || "Device";
  $("#dev-model").textContent = state.profile?.name ?? "–";
  $("#dev-version").textContent = d.version ?? "–";
  $("#dev-bt").textContent = d.bt_name ?? "–";
  $("#dev-free").textContent = d.free_kb != null ? `${(d.free_kb / 1024).toFixed(1)} MB` : "–";
  $("#live-btn").lastChild.textContent = d.live_mode ? "Live Mode is on" : "Turn on Live Mode";
  renderStats(d, online);
  renderSignal(d, online);
  $("#live-btn").disabled = !online || d.live_mode;
  if (d.volume != null && document.activeElement !== $("#vol")) {
    $("#vol").value = d.volume;
    $("#vol-out").textContent = d.volume;
  }
  if (d.error) toast(d.error, true);
  renderFiles();
}

function renderStats(d, online) {
  const files = d.files ?? [];
  $("#st-conn").textContent = online ? "Online" : d.status === "disconnected" ? "Offline" : "Waiting";
  $("#st-conn-sub").textContent = online ? d.name || "Connected" : STATUS_TEXT[d.status] || d.status;
  $("#st-vol").textContent = d.volume ?? "–";
  meter($("#meter-vol"), online && d.volume != null ? d.volume / 255 : 0);
  $("#st-sounds").textContent = online ? files.length : "–";
  $("#st-free").textContent = d.free_kb != null ? `${(d.free_kb / 1024).toFixed(1)} MB free` : "on Skelly";
  $("#st-fw").textContent = d.version ?? "–";
  $("#st-model").textContent = state.profile?.name ?? "No device";
  document.querySelectorAll(".stat").forEach((s) => s.classList.toggle("off", !online));
  $("#side-live").innerHTML = d.live_mode
    ? "<b>On.</b> Skelly is a Bluetooth speaker right now."
    : "Turns Skelly into a Bluetooth speaker for talking through him.";
}

function renderProfile() {
  const p = state.profile;
  if (!p) return;
  // movements
  const moves = $("#moves");
  moves.replaceChildren(
    ...p.movements.map((m) => {
      const c = el("button", { className: "chip", textContent: m.label });
      c.setAttribute("aria-pressed", state.moves.has(m.key));
      c.addEventListener("click", () => {
        if (m.key === "all") state.moves = state.moves.has("all") ? new Set() : new Set(["all"]);
        else {
          state.moves.delete("all");
          state.moves.has(m.key) ? state.moves.delete(m.key) : state.moves.add(m.key);
        }
        renderProfile();
        run(null, () => api("/movement", { parts: [...state.moves] }));
      });
      return c;
    }),
  );
  // eyes
  $("#eyes-card").hidden = !p.eyes.length;
  $("#eyes").replaceChildren(
    ...p.eyes.map((e) => {
      const b = el("button", { className: "eye" }, el("span", { className: "num", textContent: EYE_EMOJI[e.label] ?? e.value }), e.label);
      b.setAttribute("aria-pressed", state.eye === e.value);
      b.addEventListener("click", () =>
        run(null, async () => {
          await api("/eye", { eye: e.value });
          state.eye = e.value;
          renderProfile();
        }),
      );
      return b;
    }),
  );
  // light targets and modes
  const targets = [{ key: "all", label: "All" }, ...p.lights];
  $("#light-targets").replaceChildren(
    ...targets.map((t) => segButton(t.label, state.light === t.key, () => { state.light = t.key; renderProfile(); })),
  );
  $("#light-modes").replaceChildren(
    ...p.light_modes.map((m) =>
      segButton(m.label, state.mode === m.value, () => {
        state.mode = m.value;
        state.scene = null;
        renderProfile();
        sendLight({ mode: m.value });
      }),
    ),
  );
  renderScenes();
  renderPreview();
}

function modeFor(effect) {
  const modes = state.profile?.light_modes ?? [];
  const wanted = EFFECT_LABELS[effect] ?? [];
  return (modes.find((m) => wanted.includes(m.label)) ?? modes[0])?.value;
}

function renderScenes() {
  $("#scenes").replaceChildren(
    ...SCENES.map((sc) => {
      const b = el("button", { className: `scene${sc.cycle ? " party" : ""}` },
        el("span", { className: "emoji", textContent: sc.emoji }), sc.name);
      b.style.setProperty("--c", sc.color);
      b.setAttribute("aria-pressed", state.scene === sc.key);
      b.addEventListener("click", () => applyScene(sc));
      return b;
    }),
  );
}

async function applyScene(sc) {
  state.scene = sc.key;
  state.mode = modeFor(sc.effect) ?? null;
  $("#color").value = sc.color;
  $("#bright").value = 255;
  $("#bright-out").textContent = 255;
  renderProfile();
  await sendLight({ color: sc.color, brightness: 255, mode: state.mode ?? undefined, cycle: !!sc.cycle }, `${sc.emoji} ${sc.name}`);
}

function sendLight(fields, okMsg) {
  return run(null, () => api("/light", { light: state.light, ...fields }), okMsg);
}

function segButton(label, pressed, onClick) {
  const b = el("button", { textContent: label });
  b.setAttribute("aria-pressed", pressed);
  b.addEventListener("click", onClick);
  return b;
}

function renderFiles() {
  const files = state.device?.files ?? [];
  const list = $("#files");
  if (!files.length) {
    list.replaceChildren(el("li", {}, el("span", { className: "muted", textContent: "No sounds loaded yet." })));
    return;
  }
  list.replaceChildren(
    ...files.map((f, i) => {
      const playing = state.device.playing === f.serial;
      const btn = el("button", { className: playing ? "btn outline" : "btn primary", textContent: playing ? "Stop" : "Play" });
      btn.addEventListener("click", () => run(btn, () => api(`/files/${f.serial}/play`, { play: !playing })));
      const end = el("div", { className: "end" });
      if (playing) end.append(el("span", { className: "badge orange", textContent: "Playing" }));
      const del = el("button", { className: "btn outline icon-only", title: "Delete from Skelly" });
      del.innerHTML = '<svg><use href="#i-trash"/></svg>';
      del.addEventListener("click", () => {
        if (!confirm(`Delete "${f.name}" from Skelly?`)) return;
        run(del, () => api(`/files/${f.serial}`, undefined, "DELETE"), `Deleted ${f.name}`);
      });
      end.append(btn, del);
      const li = el("li", {},
        el("div", { className: "who" },
          el("span", { className: "num", textContent: String(i + 1).padStart(2, "0") }),
          el("div", {}, el("div", { className: "name", textContent: f.name || `Sound ${f.serial}` }),
            el("div", { className: "meta", textContent: `Sound #${f.serial}` }))),
        end);
      li.classList.toggle("playing", playing);
      return li;
    }),
  );
}

// ---------- device tab ----------
$("#scan-btn").addEventListener("click", (ev) =>
  run(ev.currentTarget, async () => {
    const list = $("#scan-list");
    list.replaceChildren(el("li", {}, el("span", { className: "muted", textContent: "Scanning…" })));
    const found = await api("/scan", {});
    if (!found.length) {
      // A connected Skelly stops advertising, so he never shows up in his own scan.
      const d = state.device;
      const msg = d?.status === "connected"
        ? `Already connected to ${d.name || "Skelly"}. No other animatronics nearby.`
        : "Nothing found. Is Skelly switched on and nearby?";
      list.replaceChildren(el("li", {}, el("span", { className: "muted", textContent: msg })));
      return;
    }
    list.replaceChildren(
      ...found.map((f) => {
        const b = el("button", { className: "btn primary", textContent: "Connect" });
        b.addEventListener("click", () =>
          run(b, async () => {
            await api("/connect", { address: f.address, name: f.name });
            list.replaceChildren();
            showTab("controls");
          }, `Connected to ${f.name}`),
        );
        const strength = f.rssi == null ? null : f.rssi > -65 ? ["Strong", "green"] : f.rssi > -80 ? ["Fair", "orange"] : ["Weak", "red"];
        const end = el("div", { className: "end" });
        if (strength) end.append(el("span", { className: `badge ${strength[1]}`, textContent: strength[0] }));
        end.append(b);
        return el("li", {},
          el("div", { className: "who" },
            el("span", { className: "row-ico", innerHTML: '<svg><use href="#i-skull"/></svg>' }),
            el("div", {}, el("div", { className: "name", textContent: f.name }),
              el("div", { className: "meta", textContent: `${f.address}${f.rssi != null ? ` · ${f.rssi} dBm` : ""}` }))),
          end);
      }),
    );
  }),
);
$("#head-scan").addEventListener("click", () => { showTab("device"); $("#scan-btn").click(); });
$("#disconnect-btn").addEventListener("click", (ev) => run(ev.currentTarget, () => api("/disconnect", {})));
$("#live-btn").addEventListener("click", (ev) => run(ev.currentTarget, () => api("/live-mode", {}), "Live Mode on"));

function renderSettings(st) {
  if (!st) return;
  state.settings = st;
  $("#set-auto-connect").checked = st.auto_connect;
  $("#set-auto-live").checked = st.auto_live_mode;
  $("#keep-look").checked = st.keep_look;
}
for (const [id, key] of [["set-auto-connect", "auto_connect"], ["set-auto-live", "auto_live_mode"]]) {
  $(`#${id}`).addEventListener("change", (e) =>
    run(null, async () => renderSettings(await api("/settings", { [key]: e.target.checked }, "PATCH")), "Saved"),
  );
}

// ---------- Bluetooth radio ----------
function renderAdapters(list) {
  const ul = $("#adapters");
  if (!list.length) {
    ul.replaceChildren(el("li", {}, el("span", { className: "muted", textContent: "No Bluetooth radios found on the mini PC." })));
    return;
  }
  ul.replaceChildren(
    ...list.map((a) => {
      const end = el("div", { className: "end" });
      if (a.in_use) end.append(el("span", { className: "badge green", textContent: "In use" }));
      if (!a.powered) end.append(el("span", { className: "badge red", textContent: "Off" }));
      const btn = el("button", { className: a.in_use ? "btn outline" : "btn primary", textContent: a.in_use ? "Selected" : "Use this" });
      btn.disabled = a.in_use;
      btn.addEventListener("click", () =>
        run(btn, async () => {
          renderAdapters(await api("/adapter", { address: a.address }));
          setTimeout(loadAdapters, 8000);  // shows the reconnect through the new radio
        }, `Switching to ${a.kind.toLowerCase()}. Skelly will reconnect in a few seconds.`),
      );
      end.append(btn);
      return el("li", {},
        el("div", { className: "who" },
          el("span", { className: "row-ico", innerHTML: '<svg><use href="#i-bt"/></svg>' }),
          el("div", {}, el("div", { className: "name", textContent: a.label }),
            el("div", { className: "meta", textContent: `${a.name} · ${a.address}` }))),
        end);
    }),
  );
}
const loadAdapters = () => api("/adapters").then(renderAdapters).catch(() => {});
$("#adapters-refresh").addEventListener("click", (ev) => run(ev.currentTarget, loadAdapters));
loadAdapters();

// ---------- controls tab ----------
let colorTimer;
$("#color").addEventListener("input", (e) => {
  state.scene = null;
  renderScenes();
  clearTimeout(colorTimer);
  colorTimer = setTimeout(() => sendLight({ color: e.target.value, cycle: false }), 120);
});
let brightTimer;
$("#bright").addEventListener("input", (e) => {
  $("#bright-out").textContent = e.target.value;
  clearTimeout(brightTimer);
  brightTimer = setTimeout(() => sendLight({ brightness: Number(e.target.value) }), 120);
});
let speedTimer;
$("#speed").addEventListener("input", (e) => {
  $("#speed-out").textContent = e.target.value;
  clearTimeout(speedTimer);
  speedTimer = setTimeout(() => sendLight({ speed: Number(e.target.value) }), 120);
});
$("#lights-off").addEventListener("click", (ev) => {
  state.scene = null;
  renderScenes();
  run(ev.currentTarget, () => api("/light", { light: state.light, brightness: 0 }), "Lights off");
});
$("#keep-look").addEventListener("change", (e) =>
  run(null, () => api("/look/keep", { keep: e.target.checked }),
    e.target.checked ? "📌 Locked in. Skelly will keep this look." : "Unlocked"),
);
$("#save-sounds").addEventListener("click", (ev) =>
  run(ev.currentTarget, async () => {
    const { sounds } = await api("/look/save-to-sounds", {});
    toast(`💾 Saved to ${sounds} sound${sounds === 1 ? "" : "s"}. Skelly will stay this way.`);
  }),
);

let volTimer;
$("#vol").addEventListener("input", (e) => {
  $("#vol-out").textContent = e.target.value;
  clearTimeout(volTimer);
  volTimer = setTimeout(() => run(null, () => api("/volume", { volume: Number(e.target.value) })), 150);
});

// ---------- sounds tab ----------
$("#files-refresh").addEventListener("click", (ev) => run(ev.currentTarget, () => api("/files/refresh", {})));

// ---------- live events ----------
function applySnapshot(s) {
  state.device = s.device;
  state.profile = s.profile;
  state.look = s.look ?? null;
  renderSettings(s.settings);
  renderProfile();
  renderDevice();
  if (s.conversation) renderTalk(s.conversation);
  if (s.vision) renderVision(s.vision);
}

function connectEvents() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/events`);
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.type === "snapshot") applySnapshot(msg.data);
    else if (msg.type === "state") { state.device = msg.data; renderDevice(); }
    else if (msg.type === "settings") renderSettings(msg.data);
    else if (msg.type === "upload") renderUpload(msg.data);
    else if (msg.type === "look") { state.look = msg.data; renderPreview(); }
    else if (msg.type === "profile") { state.profile = msg.data; state.moves.clear(); state.mode = null; renderProfile(); }
    else if (msg.type === "conversation") renderTalk(msg.data);
    else if (msg.type === "conversation_level") setTalkLevel(msg.data.level);
    else if (msg.type === "transcript") addLine(msg.data);
    else if (msg.type === "vision") renderVision(msg.data);
    else if (msg.type === "vision_motion") setMotion(msg.data.motion);
    else if (msg.type === "visitor") onVisitor(msg.data);
  };
  ws.onclose = () => setTimeout(connectEvents, 1500);
}
// Load once over HTTP so the page works even before the live stream is up.
api("/state").then(applySnapshot).catch(() => {});
connectEvents();

// ---------- sound upload ----------
let pickedFile = null;
const UPLOAD_STAGES = { starting: "Getting Skelly ready…", sending: "Sending to Skelly…", finishing: "Skelly is saving it…",
  done: "Saved on Skelly 🎉", incomplete: "Sent, but Skelly hasn't finished saving it yet. Refresh in a minute.", error: "Upload failed" };

function pickFile(file) {
  if (!file) return;
  pickedFile = file;
  $("#drop").classList.add("has-file");
  $("#drop-text").innerHTML = "";
  $("#drop-text").append(el("strong", { textContent: file.name }), ` · ${(file.size / 1024 / 1024).toFixed(1)} MB`);
  const stem = file.name.replace(/\.[^.]+$/, "").trim().replace(/\s+/g, "_").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 16);
  $("#upload-name").value = stem;
  $("#upload-btn").disabled = false;
}
$("#upload-file").addEventListener("change", (e) => pickFile(e.target.files[0]));
const drop = $("#drop");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => pickFile(e.dataTransfer.files[0]));

$("#upload-btn").addEventListener("click", (ev) =>
  run(ev.currentTarget, async () => {
    if (!pickedFile) return;
    renderUpload({ stage: "converting", percent: 0 });
    $("#upload-stage").textContent = "Tuning it for Skelly's speaker…";
    const form = new FormData();
    form.append("file", pickedFile);
    form.append("name", $("#upload-name").value);
    const res = await fetch("/api/sounds/upload", { method: "POST", body: form });
    if (!res.ok) {
      let msg = `Upload failed (${res.status})`;
      try { const j = await res.json(); if (typeof j.detail === "string") msg = j.detail; } catch {}
      renderUpload({ stage: "error", error: msg, percent: 0 });
      throw new Error(msg);
    }
    const info = await res.json();
    toast(`Sending ${info.name} (${info.seconds}s) to Skelly…`);
  }),
);

function renderUpload(u) {
  const box = $("#upload-progress");
  box.hidden = false;
  box.classList.toggle("error", u.stage === "error");
  $("#upload-stage").textContent = u.stage === "error" ? u.error || UPLOAD_STAGES.error : UPLOAD_STAGES[u.stage] ?? "Working…";
  $("#upload-pct").textContent = u.stage === "error" ? "" : `${u.percent ?? 0}%`;
  $("#upload-bar").style.width = `${u.percent ?? 0}%`;
  const busy = !["done", "error", "incomplete"].includes(u.stage);
  $("#upload-btn").disabled = busy || !pickedFile;
  if (u.stage === "done") {
    toast(`${u.name} is on Skelly 🎉`);
    pickedFile = null;
    $("#upload-file").value = "";
    $("#upload-name").value = "";
    $("#drop").classList.remove("has-file");
    $("#drop-text").innerHTML = "<strong>Drop a sound here</strong> or tap to pick one";
    $("#upload-btn").disabled = true;
  }
}

// ---------- conversation ----------
const TALK_TEXT = { idle: "Idle", connecting: "Connecting…", listening: "Listening", thinking: "Thinking…",
  speaking: "Speaking", error: "Problem" };
const TALK_HINT = { idle: "Tap to start a conversation", connecting: "Waking Skelly's voice…",
  listening: "Skelly is listening. Say hello!", thinking: "Skelly is thinking…", speaking: "Skelly is talking",
  error: "Tap to try again" };
let talkCfg = {};
let vault = [];

function renderTalk(c) {
  const on = ["connecting", "listening", "thinking", "speaking"].includes(c.state);
  $("#talk-toggle").dataset.on = on;
  $("#talk-toggle").dataset.state = c.state;
  $("#talk-toggle").setAttribute("aria-label", on ? "Stop the conversation" : "Start a conversation");
  const b = $("#talk-state");
  b.textContent = TALK_TEXT[c.state] ?? c.state;
  b.className = `badge ${c.state === "error" ? "red" : on ? "green" : ""}`;
  $("#talk-hint").textContent = TALK_HINT[c.state] ?? "";
  $("#talk-error").hidden = c.state !== "error" || !c.error;
  $("#talk-error").textContent = c.error ?? "";
  if (!on) setTalkLevel(0);
  if (c.transcript && !$("#transcript").children.length) c.transcript.forEach(addLine);
}
function setTalkLevel(level) {
  $("#talk-ring").style.setProperty("--lvl", Math.min(1, level * 12).toFixed(2));
}
function addLine(t) {
  const ul = $("#transcript");
  const li = el("li", { className: t.role === "user" ? "you" : "skelly" },
    el("span", { className: "who-tag", textContent: t.role === "user" ? "Visitor" : "Skelly" }),
    el("span", { textContent: t.text }));
  ul.append(li);
  while (ul.children.length > 60) ul.firstChild.remove();
  ul.scrollTop = ul.scrollHeight;
}
$("#talk-toggle").addEventListener("click", (ev) => {
  const on = ev.currentTarget.dataset.on === "true";
  if (!on) $("#transcript").replaceChildren();
  run(ev.currentTarget, async () => renderTalk(await api(on ? "/conversation/stop" : "/conversation/start", {})));
});

const KEY_NEEDS = {
  elevenlabs: () => (talkCfg.elevenlabs_agent_id ? [] : ["agent"]),
  openai: () => ["openai_api_key"],
  claude: () => [...new Set(["anthropic_api_key", `${talkCfg.stt}_api_key`, `${talkCfg.tts}_api_key`])],
};
function renderTalkCfg() {
  const p = talkCfg.provider || "elevenlabs";
  document.querySelectorAll("#provider-seg button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.provider === p));
  document.querySelectorAll(".prov[data-for]").forEach((d) => (d.hidden = d.dataset.for !== p));
  document.querySelectorAll("[data-cfg]").forEach((i) => {
    if (document.activeElement === i) return;
    const v = talkCfg[i.dataset.cfg];
    if (i.type === "checkbox") i.checked = !!v; else if (v != null) i.value = v;
  });
  document.querySelectorAll("[data-seg]").forEach((seg) =>
    seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", talkCfg[seg.dataset.seg] === b.dataset.val)));
  document.querySelectorAll("[data-show-tts]").forEach((l) => (l.hidden = l.dataset.showTts !== talkCfg.tts));
  const have = new Set(vault.filter((v) => v.set).map((v) => v.name));
  const missing = KEY_NEEDS[p]().filter((n) => n === "agent" || !have.has(n));
  const warn = $("#key-warn");
  warn.hidden = !missing.length;
  warn.replaceChildren();
  if (missing.length) {
    const names = missing.map((n) => n === "agent" ? "an agent ID below" : vault.find((v) => v.name === n)?.label ?? n);
    warn.append(el("span", { textContent: `Needs ${names.join(", ")}.` }));
    if (missing.some((n) => n !== "agent")) {
      const go = el("button", { className: "btn outline small", textContent: "Open API keys" });
      go.addEventListener("click", () => showTab("settings"));
      warn.append(go);
    }
  }
}
let cfgTimer;
function saveTalkCfg(patch) {
  Object.assign(talkCfg, patch);
  renderTalkCfg();
  clearTimeout(cfgTimer);
  cfgTimer = setTimeout(() => run(null, async () => { talkCfg = await api("/conversation/config", talkCfg, "PUT"); }), 400);
}
document.querySelectorAll("#provider-seg button").forEach((b) =>
  b.addEventListener("click", () => saveTalkCfg({ provider: b.dataset.provider })));
document.querySelectorAll("[data-seg] button").forEach((b) =>
  b.addEventListener("click", () => saveTalkCfg({ [b.parentElement.dataset.seg]: b.dataset.val })));
document.querySelectorAll("[data-cfg]").forEach((i) =>
  i.addEventListener(i.type === "checkbox" || i.tagName === "SELECT" ? "change" : "input", () =>
    saveTalkCfg({ [i.dataset.cfg]: i.type === "checkbox" ? i.checked : i.type === "number" ? Number(i.value) : i.value })));
api("/conversation").then((r) => { talkCfg = r.config; renderTalkCfg(); renderTalk(r.state); }).catch(() => {});

// ---------- vision ----------
let visionCfg = {};
let cams = [];
function renderVision(v) {
  $("#cam-toggle").lastElementChild.textContent = v.running ? "Stop camera" : "Start camera";
  $("#cam-toggle").firstElementChild.innerHTML = `<use href="#i-${v.running ? "stop" : "play"}"/>`;
  $("#cam-toggle").className = v.running ? "btn outline" : "btn primary";
  const img = $("#cam-img");
  if (v.running && img.hidden) { img.src = `/api/vision/stream?t=${Date.now()}`; img.hidden = false; }
  if (!v.running && !img.hidden) { img.removeAttribute("src"); img.hidden = true; }
  $("#cam-empty").hidden = v.running && !v.error;
  $("#cam-empty").lastElementChild.textContent = v.error ? `Camera problem: ${v.error}` : v.running ? "Connecting…" : "Camera is off";
  $("#cam-sub").textContent = v.running ? (v.error ? "Retrying…" : `Watching${v.fps ? ` · ${v.fps} fps` : ""}`) : "Off";
  $("#cam-visitor").hidden = !v.visitor;
  if (v.description) { $("#cam-desc").hidden = false; $("#cam-desc").textContent = v.description; }
  if (!v.running) setMotion(0);
}
function setMotion(m) {
  const pct = Math.min(100, Math.round(m * 400));
  $("#motion-bar").style.width = `${pct}%`;
  $("#motion-out").textContent = `${pct}%`;
}
function onVisitor(v) {
  toast(v.description ? `👀 Visitor: ${v.description}` : "👀 Someone walked up");
}
function renderVisionCfg() {
  const src = visionCfg.source || "usb";
  document.querySelectorAll("#cam-source button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.val === src));
  document.querySelectorAll(".prov[data-cam]").forEach((d) => (d.hidden = d.dataset.cam !== src));
  const sel = $("#cam-device");
  sel.replaceChildren(...(cams.length ? cams : [{ device: visionCfg.usb_device || "/dev/video0", label: "No USB camera found" }])
    .map((c) => el("option", { value: c.device, textContent: `${c.label} (${c.device})` })));
  document.querySelectorAll("[data-vcfg]").forEach((i) => {
    const v = visionCfg[i.dataset.vcfg];
    if (i.type === "checkbox") i.checked = !!v; else if (v != null) i.value = v;
  });
  $("#sens-out").textContent = visionCfg.sensitivity ?? 50;
}
let vTimer;
function saveVisionCfg(patch) {
  Object.assign(visionCfg, patch);
  renderVisionCfg();
  clearTimeout(vTimer);
  vTimer = setTimeout(() => run(null, async () => { visionCfg = await api("/vision/config", visionCfg, "PUT"); }), 400);
}
document.querySelectorAll("#cam-source button").forEach((b) => b.addEventListener("click", () => saveVisionCfg({ source: b.dataset.val })));
document.querySelectorAll("[data-vcfg]").forEach((i) =>
  i.addEventListener(i.type === "range" ? "input" : "change", () =>
    saveVisionCfg({ [i.dataset.vcfg]: i.type === "checkbox" ? i.checked : ["rotate", "sensitivity"].includes(i.dataset.vcfg) ? Number(i.value) : i.value })));
$("#cam-toggle").addEventListener("click", (ev) =>
  run(ev.currentTarget, async () => renderVision(await api(ev.currentTarget.textContent.includes("Stop") ? "/vision/stop" : "/vision/start", {}))));
$("#cam-describe").addEventListener("click", (ev) =>
  run(ev.currentTarget, async () => {
    const r = await api("/vision/describe", {});
    $("#cam-desc").hidden = false;
    $("#cam-desc").textContent = `${r.people ? `${r.people} ${r.people === 1 ? "person" : "people"}. ` : "Nobody there. "}${r.description ?? ""}`;
  }));
api("/vision").then((r) => { visionCfg = r.config; cams = r.cameras; renderVisionCfg(); renderVision(r.state); }).catch(() => {});

// ---------- settings: speaker, vault ----------
async function loadAudio() {
  try {
    const d = await api("/audio/devices");
    const spk = d.speakers.find((x) => x.bluetooth);
    $("#spk-out").textContent = spk ? `${spk.label} · connected` : "Not connected";
    $("#mic-out").textContent = d.mics.find((m) => /usb/i.test(m.name))?.label ?? d.mics[0]?.label ?? "None found";
  } catch { $("#spk-out").textContent = "Audio isn't set up on this machine"; }
}
$("#spk-connect").addEventListener("click", (ev) =>
  run(ev.currentTarget, async () => { await api("/speaker/connect", {}); await loadAudio(); }, "Skelly's speaker is connected"));
loadAudio();

function secretRow(v) {
  const input = el("input", { type: "password", placeholder: v.set ? `Saved (${v.hint})` : "Paste here", autocomplete: "off" });
  const save = el("button", { className: "btn primary", textContent: v.set ? "Replace" : "Save" });
  save.addEventListener("click", () =>
    run(save, async () => {
      if (!input.value.trim()) throw new Error("Paste the value first");
      vault = await api(`/vault/${v.name}`, { value: input.value.trim() }, "PUT");
      renderVault();
    }, `${v.label} saved`));
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") save.click(); });
  const end = el("div", { className: "end" });
  if (v.set) {
    end.append(el("span", { className: "badge green", textContent: v.hint }));
    const del = el("button", { className: "btn outline", textContent: "Remove" });
    del.addEventListener("click", () => {
      if (!confirm(`Remove the ${v.label}?`)) return;
      run(del, async () => { vault = await api(`/vault/${v.name}`, undefined, "DELETE"); renderVault(); }, "Removed");
    });
    end.append(del);
  }
  return el("li", { className: "secret" },
    el("div", { className: "who" },
      el("span", { className: "row-ico", innerHTML: '<svg><use href="#i-key"/></svg>' }),
      el("div", {}, el("div", { className: "name", textContent: v.label }), el("div", { className: "meta", textContent: v.used_for }))),
    v.how ? el("div", { className: "secret-how" },
      el("span", { textContent: v.how }),
      el("a", { className: "btn outline small", href: v.url, target: "_blank", rel: "noopener", textContent: "Get it ↗" })) : "",
    el("div", { className: "secret-edit" }, input, save), end);
}
function renderVault() {
  $("#vault").replaceChildren(...vault.filter((v) => v.name !== "rtsp_url").map(secretRow));
  const rtsp = vault.find((v) => v.name === "rtsp_url");
  if (rtsp) $("#rtsp-inline").replaceChildren(el("ul", { className: "list" }, secretRow(rtsp)));
  renderTalkCfg();
}
api("/vault").then((v) => { vault = v; renderVault(); }).catch(() => {});
