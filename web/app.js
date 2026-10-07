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

// ---------- sounds: playlist and per-sound performances ----------
const DEFAULT_AFTER = 1.5;
let filesKey = "";

// Every sound on Skelly in playlist order: saved order first, new sounds (unticked) after.
function playlistRows() {
  const files = state.device?.files ?? [];
  const items = state.settings?.playlist?.items ?? [];
  const byName = new Map(files.map((f) => [f.name.toLowerCase(), f]));
  const rows = [];
  for (const it of items) {
    const f = byName.get(it.name.toLowerCase());
    if (f) { rows.push({ f, it }); byName.delete(it.name.toLowerCase()); }
  }
  for (const f of byName.values()) rows.push({ f, it: { name: f.name, on: false, before: 0, after: DEFAULT_AFTER } });
  return rows;
}

function savePlaylist(rows, patch = {}) {
  const cfg = state.settings?.playlist ?? {};
  const body = { items: rows.map((r) => r.it), loop: !!cfg.loop, shuffle: !!cfg.shuffle, ...patch };
  state.settings = { ...state.settings, playlist: body };
  renderFiles();
  return run(null, () => api("/playlist", body, "PUT"));
}

function perfSummary(perf) {
  if (!perf) return "";
  const p = state.profile;
  const bits = [];
  const moves = (perf.moves ?? []).map((k) => p?.movements.find((m) => m.key === k)?.label ?? k);
  if (moves.length) bits.push(moves.join(", "));
  const eye = p?.eyes.find((e) => e.value === perf.eye);
  if (eye) bits.push(`${EYE_EMOJI[eye.label] ?? "👁"} ${eye.label}`);
  const mode = p?.light_modes.find((m) => m.value === perf.mode);
  if (perf.cycle) bits.push("🌈 Rainbow");
  else if (perf.color || mode) bits.push([mode?.label, perf.color ? "light" : "lights"].filter(Boolean).join(" "));
  return bits.length ? `🎭 ${bits.join(" · ")}` : "🎭 Stays still";
}

function renderPlaylistBar() {
  const st = state.playlist ?? {};
  const cfg = state.settings?.playlist ?? {};
  const btn = $("#pl-play");
  btn.className = st.running ? "btn outline" : "btn primary";
  btn.innerHTML = st.running ? '<svg><use href="#i-stop"/></svg>Stop playlist' : '<svg><use href="#i-play"/></svg>Play playlist';
  $("#pl-skip").hidden = !st.running;
  $("#pl-loop").setAttribute("aria-pressed", !!cfg.loop);
  $("#pl-shuffle").setAttribute("aria-pressed", !!cfg.shuffle);
  const ticked = playlistRows().filter((r) => r.it.on).length;
  $("#pl-status").textContent = st.running
    ? st.waiting ? `Next in ${st.waiting}s` : `Playing ${st.position} of ${st.total}: ${st.name}`
    : ticked ? `${ticked} sound${ticked === 1 ? "" : "s"} ticked` : "Tick the sounds you want to play";
}

function renderFiles() {
  const files = state.device?.files ?? [];
  const key = JSON.stringify([files, state.device?.playing, state.settings?.playlist, state.settings?.performances,
    state.playlist, state.editing?.name, state.profile?.key]);
  if (key === filesKey) return;
  filesKey = key;
  renderPlaylistBar();
  const list = $("#files");
  if (!files.length) {
    list.replaceChildren(el("li", {}, el("span", { className: "muted", textContent: "No sounds loaded yet." })));
    return;
  }
  const rows = playlistRows();
  const perfs = state.settings?.performances ?? {};
  list.replaceChildren(
    ...rows.map(({ f, it }, i) => {
      const playing = state.device.playing === f.serial;
      const perf = perfs[f.name];
      const tick = el("input", { type: "checkbox", className: "tick", checked: it.on, title: "Play this in the playlist" });
      tick.addEventListener("change", () => { it.on = tick.checked; savePlaylist(rows); });
      const btn = el("button", { className: playing ? "btn outline" : "btn primary", textContent: playing ? "Stop" : "Play" });
      btn.addEventListener("click", () => run(btn, () => api(`/files/${f.serial}/play`, { play: !playing })));
      const end = el("div", { className: "end" });
      if (playing) end.append(el("span", { className: "badge orange", textContent: "Playing" }));
      const up = el("button", { className: "btn outline icon-only arrow", textContent: "↑", title: "Move up", disabled: i === 0 });
      up.addEventListener("click", () => { [rows[i - 1], rows[i]] = [rows[i], rows[i - 1]]; savePlaylist(rows); });
      const down = el("button", { className: "btn outline icon-only arrow", textContent: "↓", title: "Move down",
        disabled: i === rows.length - 1 });
      down.addEventListener("click", () => { [rows[i + 1], rows[i]] = [rows[i], rows[i + 1]]; savePlaylist(rows); });
      const editing = state.editing?.name === f.name;
      const perfBtn = el("button", { className: `btn outline icon-only${perf ? " set" : ""}`, textContent: "🎭",
        title: "What Skelly does while this plays" });
      perfBtn.setAttribute("aria-pressed", editing);
      perfBtn.addEventListener("click", () => {
        state.editing = editing ? null : { name: f.name, draft: structuredClone(perf ?? { moves: ["all"] }), it };
        renderFiles();
      });
      const del = el("button", { className: "btn outline icon-only", title: "Delete from Skelly" });
      del.innerHTML = '<svg><use href="#i-trash"/></svg>';
      del.addEventListener("click", () => {
        if (!confirm(`Delete "${f.name}" from Skelly?`)) return;
        run(del, () => api(`/files/${f.serial}`, undefined, "DELETE"), `Deleted ${f.name}`);
      });
      end.append(btn, perfBtn, up, down, del);
      const wait = it.before || it.after !== DEFAULT_AFTER
        ? ` · ⏱ ${it.before ? `${it.before}s before, ` : ""}${it.after}s after` : "";
      const li = el("li", { className: "sound" },
        el("div", { className: "who" }, tick,
          el("span", { className: "num", textContent: String(i + 1).padStart(2, "0") }),
          el("div", {}, el("div", { className: "name", textContent: f.name || `Sound ${f.serial}` }),
            el("div", { className: "meta", textContent: (perfSummary(perf) || `Sound #${f.serial}`) + wait }))),
        end);
      li.classList.toggle("playing", playing);
      li.classList.toggle("off", !it.on);
      if (editing) li.append(perfEditor(f, rows));
      return li;
    }),
  );
}

function perfEditor(f, rows) {
  const p = state.profile;
  const d = state.editing.draft;
  const it = rows.find((r) => r.f.name === f.name).it;
  const box = el("div", { className: "perf" });
  const redraw = () => box.replaceWith(perfEditor(f, rows));

  const moves = el("div", { className: "chips" },
    ...p.movements.map((m) => {
      const c = el("button", { className: "chip", textContent: m.label });
      c.setAttribute("aria-pressed", (d.moves ?? []).includes(m.key));
      c.addEventListener("click", () => {
        const on = new Set(d.moves ?? []);
        if (m.key === "all") d.moves = on.has("all") ? [] : ["all"];
        else {
          on.delete("all");
          on.has(m.key) ? on.delete(m.key) : on.add(m.key);
          d.moves = [...on];
        }
        redraw();
      });
      return c;
    }));

  const eyes = el("select", {},
    el("option", { value: "", textContent: "Don't change" }),
    ...p.eyes.map((e) => el("option", { value: e.value, textContent: `${EYE_EMOJI[e.label] ?? ""} ${e.label}`, selected: d.eye === e.value })));
  eyes.addEventListener("change", () => { d.eye = eyes.value ? Number(eyes.value) : null; });

  const modes = el("div", { className: "seg" },
    segButton("Don't change", d.mode == null && !d.color && !d.cycle, () => { d.mode = null; d.color = null; d.cycle = false; redraw(); }),
    ...p.light_modes.map((m) => segButton(m.label, d.mode === m.value, () => {
      d.mode = m.value;
      d.color ??= "#ff6a00";
      redraw();
    })));
  const color = el("input", { type: "color", value: d.color ?? "#ff6a00", ariaLabel: "Light colour" });
  const dot = el("label", { className: "color-dot small", title: "Light colour" }, color);
  dot.style.borderColor = d.color ?? "";
  color.addEventListener("input", () => {
    d.color = color.value;
    d.cycle = false;
    d.mode ??= p.light_modes[0]?.value ?? null;
    dot.style.borderColor = d.color;
    rainbow.setAttribute("aria-pressed", false);
  });
  const rainbow = el("button", { className: "chip", textContent: "🌈 Rainbow" });
  rainbow.setAttribute("aria-pressed", !!d.cycle);
  rainbow.addEventListener("click", () => { d.cycle = !d.cycle; if (d.cycle) { d.color ??= "#ff004c"; d.mode ??= p.light_modes[0]?.value ?? null; } redraw(); });
  const slider = (label, keyName, max, dflt) => {
    const out = el("output", { textContent: d[keyName] ?? dflt });
    const input = el("input", { type: "range", min: 0, max, value: d[keyName] ?? dflt });
    input.addEventListener("input", () => { d[keyName] = Number(input.value); out.textContent = input.value; });
    return el("label", { className: "slider" }, `${label} `, out, input);
  };
  const secs = (label, keyName) => {
    const input = el("input", { type: "number", min: 0, max: 600, step: 0.5, value: it[keyName] });
    input.addEventListener("change", () => {
      it[keyName] = Math.max(0, Math.min(600, Number(input.value) || 0));
      savePlaylist(rows);
    });
    return el("label", { className: "secs" }, label, input, "s");
  };

  const save = async (andPlay) => {
    await savePlaylist(rows);
    await api(`/files/${f.serial}/performance`, d, "PUT");
    if (andPlay) await api(`/files/${f.serial}/play`, { play: true });
  };
  const tryBtn = el("button", { className: "btn outline", innerHTML: '<svg><use href="#i-play"/></svg>Try it' });
  tryBtn.addEventListener("click", () => run(tryBtn, () => save(true)));
  const saveBtn = el("button", { className: "btn primary", textContent: "Save" });
  saveBtn.addEventListener("click", () => run(saveBtn, async () => { await save(false); state.editing = null; renderFiles(); },
    `🎭 Saved. Skelly will do this whenever ${f.name} plays.`));
  const clear = el("button", { className: "btn outline", textContent: "Reset" });
  clear.addEventListener("click", () => run(clear, async () => {
    await api(`/files/${f.serial}/performance`, undefined, "DELETE");
    state.editing = null;
    renderFiles();
  }, "Back to Skelly's own moves for this sound"));

  box.append(
    el("p", { className: "perf-title", textContent: `While ${f.name} plays` }),
    el("div", { className: "field" }, el("span", { className: "muted", textContent: "Move" }), moves),
    p.eyes.length ? el("label", { className: "field-row" }, "Eyes", eyes) : "",
    el("div", { className: "field" }, el("span", { className: "muted", textContent: "Lights" }), modes),
    el("div", { className: "row perf-color" }, dot, rainbow),
    slider("Brightness", "brightness", 255, 255),
    slider("Effect speed", "speed", 254, 127),
    el("div", { className: "row perf-wait" }, el("span", { className: "muted", textContent: "In the playlist, wait" }),
      secs("before", "before"), secs("after", "after")),
    el("div", { className: "row split" }, clear, el("div", { className: "row" }, tryBtn, saveBtn)),
  );
  return box;
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
  renderFiles();
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
$("#pl-play").addEventListener("click", (ev) =>
  run(ev.currentTarget, async () => {
    state.playlist = await api(state.playlist?.running ? "/playlist/stop" : "/playlist/play", {});
    renderFiles();
  }));
$("#pl-skip").addEventListener("click", (ev) => run(ev.currentTarget, () => api("/playlist/skip", {})));
$("#pl-loop").addEventListener("click", () => savePlaylist(playlistRows(), { loop: !state.settings?.playlist?.loop }));
$("#pl-shuffle").addEventListener("click", () => savePlaylist(playlistRows(), { shuffle: !state.settings?.playlist?.shuffle }));

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
  if (s.playlist) { state.playlist = s.playlist; renderFiles(); }
}

function connectEvents() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/events`);
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.type === "snapshot") applySnapshot(msg.data);
    else if (msg.type === "state") { state.device = msg.data; renderDevice(); }
    else if (msg.type === "settings") renderSettings(msg.data);
    else if (msg.type === "upload") renderUpload(msg.data);
    else if (msg.type === "playlist") { state.playlist = msg.data; renderFiles(); }
    else if (msg.type === "look") { state.look = msg.data; renderPreview(); }
    else if (msg.type === "profile") { state.profile = msg.data; state.moves.clear(); state.mode = null; renderProfile(); }
    else if (msg.type === "conversation") renderTalk(msg.data);
    else if (msg.type === "conversation_level") setTalkLevel(msg.data.level);
    else if (msg.type === "transcript") addLine(msg.data);
    else if (msg.type === "vision") renderVision(msg.data);
    else if (msg.type === "vision_motion") setMotion(msg.data.motion);
    else if (msg.type === "visitor") onVisitor(msg.data);
    else if (msg.type === "meters") setMeters(msg.data);
    else if (msg.type === "faces") renderFaceBoxes(msg.data.faces);
    else if (msg.type === "face_learned") { toast(`🦴 Skelly will remember ${msg.data.name}`); loadPeople(); }
    else if (msg.type === "known_visitor") toast(`👋 ${msg.data.name} is here`);
    else if (msg.type === "recording") { $("#rec-badge").hidden = !msg.data.recording; if (!msg.data.recording) loadRecordings(); }
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
  elevenlabs: () => ["elevenlabs_api_key", ...(talkCfg.elevenlabs_agent_id ? [] : ["agent"])],
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
    const names = missing.map((n) => n === "agent" ? "an agent (pick or create one below)" : vault.find((v) => v.name === n)?.label ?? n);
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
  const slow = "prompt" in patch || "first_message" in patch;
  cfgTimer = setTimeout(() => run(null, async () => {
    const { sync, ...cfg } = await api("/conversation/config", talkCfg, "PUT");
    talkCfg = cfg;
    renderTalkCfg();
    if (sync) toast(sync, sync.startsWith("Couldn't"));
  }), slow ? 1200 : 400);
}
document.querySelectorAll("#provider-seg button").forEach((b) =>
  b.addEventListener("click", () => saveTalkCfg({ provider: b.dataset.provider })));
document.querySelectorAll("[data-seg] button").forEach((b) =>
  b.addEventListener("click", () => saveTalkCfg({ [b.parentElement.dataset.seg]: b.dataset.val })));
document.querySelectorAll("[data-cfg]").forEach((i) =>
  i.addEventListener(i.type === "checkbox" || i.tagName === "SELECT" ? "change" : "input", () =>
    saveTalkCfg({ [i.dataset.cfg]: i.type === "checkbox" ? i.checked : i.type === "number" ? Number(i.value) : i.value })));
api("/conversation").then((r) => { talkCfg = r.config; renderTalkCfg(); renderTalk(r.state); loadEleven(); }).catch(() => {});

// ElevenLabs pickers: agents in the account and voices in the library.
function fillSelect(sel, items, current, empty) {
  const opts = items.map((i) => el("option", { value: i.id, textContent: i.name }));
  if (current && !items.some((i) => i.id === current)) opts.unshift(el("option", { value: current, textContent: `${current} (not in this account)` }));
  if (!opts.length) opts.push(el("option", { value: "", textContent: empty }));
  sel.replaceChildren(...opts);
  sel.value = current || items[0]?.id || "";
}
async function loadEleven() {
  if (!vault.find((v) => v.name === "elevenlabs_api_key")?.set) {
    fillSelect($("#agent-pick"), [], talkCfg.elevenlabs_agent_id, "Add your ElevenLabs key first");
    fillSelect($("#voice-pick"), [], talkCfg.elevenlabs_voice_id, "Add your ElevenLabs key first");
    return;
  }
  const [agents, voices] = await Promise.all([api("/elevenlabs/agents").catch(() => []), api("/elevenlabs/voices").catch(() => [])]);
  fillSelect($("#agent-pick"), agents, talkCfg.elevenlabs_agent_id, "No agents yet: create one below");
  fillSelect($("#voice-pick"), voices, talkCfg.elevenlabs_voice_id, "No voices found");
  if (!talkCfg.elevenlabs_agent_id && agents.length) saveTalkCfg({ elevenlabs_agent_id: agents[0].id });
}
$("#agents-refresh").addEventListener("click", (ev) =>
  run(ev.currentTarget, async () => {
    await loadEleven();
    if (talkCfg.elevenlabs_agent_id) { talkCfg = await api("/elevenlabs/pull", {}); renderTalkCfg(); }
  }, "Up to date with ElevenLabs"));
$("#agent-create").addEventListener("click", (ev) =>
  run(ev.currentTarget, async () => {
    const a = await api("/elevenlabs/agents", {});
    talkCfg.elevenlabs_agent_id = a.id;
    await loadEleven();
    renderTalkCfg();
  }, "Created a Skelly agent in your ElevenLabs account"));

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
  renderCostumes(v.costumes);
  if (!v.running) setMotion(0);
}
function setMotion(m) {
  const pct = Math.min(100, Math.round(m * 400));
  $("#motion-bar").style.width = `${pct}%`;
  $("#motion-out").textContent = `${pct}%`;
}
function onVisitor(v) {
  const dressed = v.costumes?.length ? ` 🎃 ${v.costumes.join(", ")}` : "";
  toast(v.description ? `👀 Visitor: ${v.description}${dressed}` : `👀 Someone walked up${dressed}`);
}
function renderCostumes(list = []) {
  $("#costumes").replaceChildren(...list.map((c) => el("span", { className: "chip costume", textContent: `🎃 ${c}` })));
}
function renderVisionCfg() {
  const src = visionCfg.source || "usb";
  if (typeof renderZones === "function" && $("#zones")) setTimeout(renderZones);
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
  if (typeof renderIgnore === "function" && $("#ignore-chips")) setTimeout(renderIgnore);
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
    renderCostumes((r.costumes ?? []).map((c) => (typeof c === "string" ? c : c.costume)).filter(Boolean));
  }));
let ignorable = {};
api("/vision").then((r) => { visionCfg = r.config; cams = r.cameras; ignorable = r.ignorable ?? {}; renderVisionCfg(); renderVision(r.state); renderZones(); renderIgnore(); }).catch(() => {});

// "Don't react to" chips: the AI check tells these apart and they don't count as visitors.
function renderIgnore() {
  const on = new Set(visionCfg.ignore ?? []);
  const needsAi = !visionCfg.ai_check;
  $("#ignore-chips").replaceChildren(...Object.entries(ignorable).map(([key, label]) => {
    const c = el("button", { className: "chip", textContent: label });
    c.setAttribute("aria-pressed", on.has(key));
    c.disabled = needsAi && key !== "weather";
    c.title = c.disabled ? "Needs Check with AI" : "";
    c.addEventListener("click", () => {
      on.has(key) ? on.delete(key) : on.add(key);
      saveVisionCfg({ ignore: [...on] });
      renderIgnore();
    });
    return c;
  }));
}

// ---------- ignored areas (Skelly himself, flags, trees) ----------
const camKey = () => (visionCfg.source === "rtsp" ? "rtsp" : visionCfg.usb_device);
const zonesNow = () => (visionCfg.zones ?? {})[camKey()] ?? [];
function renderZones() {
  const z = zonesNow();
  $("#zones").replaceChildren(...z.map((b, i) => {
    const d = el("div", { className: "zone" }, el("span", { textContent: i === 0 ? "Ignored" : "" }));
    Object.assign(d.style, { left: `${b[0] * 100}%`, top: `${b[1] * 100}%`, width: `${b[2] * 100}%`, height: `${b[3] * 100}%` });
    return d;
  }));
  $("#zone-sub").textContent = z.length ? `Ignored areas: ${z.length}. Motion and people there don't count.` : "Ignored areas: none";
  $("#zone-clear").disabled = !z.length;
}
async function putZones(zones) {
  visionCfg = await api("/vision/zones", { zones }, "PUT");
  renderZones();
}
$("#zone-find").addEventListener("click", (ev) =>
  run(ev.currentTarget, async () => { visionCfg = await api("/vision/find-skelly", {}); renderZones(); },
    "Found Skelly. He's now ignored, so only people count."));
$("#zone-clear").addEventListener("click", (ev) => run(ev.currentTarget, () => putZones([]), "Cleared"));
let drawing = null;
$("#zone-draw").addEventListener("click", () => {
  const on = $("#cam-view").classList.toggle("drawing");
  $("#zone-draw").textContent = on ? "Drag on the picture…" : "Draw area";
});
const frac = (e) => {
  const r = $("#cam-view").getBoundingClientRect();
  const p = e.touches?.[0] ?? e;
  return [Math.min(1, Math.max(0, (p.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (p.clientY - r.top) / r.height))];
};
function startDraw(e) {
  if (!$("#cam-view").classList.contains("drawing")) return;
  e.preventDefault();
  const [x, y] = frac(e);
  drawing = { x, y, box: el("div", { className: "zone live" }) };
  $("#zones").append(drawing.box);
}
function moveDraw(e) {
  if (!drawing) return;
  e.preventDefault();
  const [x, y] = frac(e);
  drawing.rect = [Math.min(x, drawing.x), Math.min(y, drawing.y), Math.abs(x - drawing.x), Math.abs(y - drawing.y)];
  Object.assign(drawing.box.style, { left: `${drawing.rect[0] * 100}%`, top: `${drawing.rect[1] * 100}%`,
    width: `${drawing.rect[2] * 100}%`, height: `${drawing.rect[3] * 100}%` });
}
function endDraw() {
  if (!drawing) return;
  const r = drawing.rect;
  drawing = null;
  $("#cam-view").classList.remove("drawing");
  $("#zone-draw").textContent = "Draw area";
  if (r && r[2] > 0.02 && r[3] > 0.02) run(null, () => putZones([...zonesNow(), r]), "Area ignored");
  else renderZones();
}
$("#cam-view").addEventListener("mousedown", startDraw);
$("#cam-view").addEventListener("touchstart", startDraw, { passive: false });
window.addEventListener("mousemove", moveDraw);
window.addEventListener("touchmove", moveDraw, { passive: false });
window.addEventListener("mouseup", endDraw);
window.addEventListener("touchend", endDraw);

// ---------- settings: sound ----------
let audio = { devices: { speakers: [], mics: [] }, volumes: {}, config: { mic: "", skelly: true, extra: [] } };
const sinkMac = (n) => (n.match(/^bluez_output\.([0-9A-F_]{17})\./i)?.[1] ?? "").replace(/_/g, ":");
async function loadAudio() {
  try { audio = await api("/audio"); } catch { return; }
  renderAudio();
}
function saveAudio(patch) {
  audio.config = { ...audio.config, ...patch };
  renderAudio();
  run(null, async () => { audio.config = await api("/audio/config", patch, "PUT"); });
}
function volSlider(sink) {
  const v = audio.volumes[sink];
  if (v == null) return "";
  const r = el("input", { type: "range", min: 0, max: 150, value: v, className: "vol-mini", title: "Volume" });
  let t;
  r.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => run(null, () => api("/audio/volume", { sink, volume: Number(r.value) })), 150); });
  return r;
}
function speakerRow({ label, meta, sink, on, onToggle, connected, extraBtn, idle = "Off" }) {
  const sw = el("input", { type: "checkbox", checked: on });
  sw.setAttribute("role", "switch");
  sw.addEventListener("change", () => onToggle(sw.checked));
  const end = el("div", { className: "end" });
  end.append(el("span", { className: `badge ${connected ? "green" : ""}`, textContent: connected ? "Connected" : idle }));
  if (extraBtn) end.append(extraBtn);
  end.append(el("label", { className: "mini-switch" }, sw));
  return el("li", { className: "spk" },
    el("div", { className: "who" },
      el("span", { className: "row-ico", innerHTML: '<svg><use href="#i-volume"/></svg>' }),
      el("div", {}, el("div", { className: "name", textContent: label }), el("div", { className: "meta", textContent: meta }))),
    sink && connected ? volSlider(sink) : "", end);
}
function renderAudio() {
  const { devices, config } = audio;
  const mics = devices.mics;
  $("#mic-pick").replaceChildren(el("option", { value: "", textContent: "Default microphone" }),
    ...mics.map((m) => el("option", { value: m.name, textContent: m.label })));
  $("#mic-pick").value = config.mic || "";
  renderGains();
  const have = new Set(devices.speakers.map((d) => d.name));
  const skellySink = audio.skelly_sink;
  const connectBtn = el("button", { className: "btn outline small", textContent: "Connect" });
  connectBtn.addEventListener("click", () => run(connectBtn, async () => { await api("/speaker/connect", {}); await loadAudio(); }, "Skelly's speaker is connected"));
  const rows = [speakerRow({ label: "Skelly (Live Mode speaker)", meta: "His own speaker, over Bluetooth", sink: skellySink,
    on: config.skelly !== false, connected: skellySink && have.has(skellySink), onToggle: (v) => saveAudio({ skelly: v }),
    extraBtn: skellySink ? null : connectBtn, idle: skellySink ? "Wakes when he talks" : "Not paired" })];
  const extra = config.extra ?? [];
  const others = devices.speakers.filter((d) => d.name !== skellySink && d.name !== "skelly_all_speakers");
  const names = [...new Set([...extra, ...others.map((d) => d.name)])];
  for (const n of names) {
    const d = devices.speakers.find((x) => x.name === n);
    const mac = sinkMac(n);
    let forget = null;
    if (mac) {
      forget = el("button", { className: "btn outline small", textContent: "Forget" });
      forget.addEventListener("click", () => {
        if (!confirm(`Forget ${d?.label ?? mac}?`)) return;
        run(forget, async () => { audio.config = await api("/audio/forget", { address: mac }); await loadAudio(); }, "Forgotten");
      });
    }
    rows.push(speakerRow({ label: d?.label ?? mac ?? n, meta: mac ? `Bluetooth · ${mac}` : "Wired output on the mini PC", sink: n,
      on: extra.includes(n), connected: have.has(n),
      onToggle: (v) => saveAudio({ extra: v ? [...extra, n] : extra.filter((x) => x !== n) }), extraBtn: forget,
      idle: mac ? "Connects when needed" : "Off" }));
  }
  $("#speakers").replaceChildren(...rows);
}
$("#mic-pick").addEventListener("change", (e) => saveAudio({ mic: e.target.value }));
$("#snd-test").addEventListener("click", (ev) => run(ev.currentTarget, async () => { await api("/audio/test", {}); await loadAudio(); }, "Ta-da! Played on the ticked speakers"));
$("#bt-scan").addEventListener("click", (ev) =>
  run(ev.currentTarget, async () => {
    $("#bt-hint").textContent = "Looking for speakers (10 seconds)…";
    const found = await api("/audio/scan", {});
    $("#bt-hint").textContent = found.length ? "Tap Pair on your speaker." : "Nothing found. Is it in pairing mode and close by?";
    $("#bt-found").replaceChildren(...found.filter((f) => !sinkMac(audio.skelly_sink || "") || f.address !== sinkMac(audio.skelly_sink)).map((f) => {
      const b = el("button", { className: f.paired ? "btn outline small" : "btn primary small", textContent: f.paired ? "Use" : "Pair" });
      b.addEventListener("click", () => run(b, async () => { await api("/audio/pair", { address: f.address }); $("#bt-found").replaceChildren(); await loadAudio(); }, `${f.name} added`));
      return el("li", {}, el("div", { className: "who" },
        el("span", { className: "row-ico", innerHTML: '<svg><use href="#i-bt"/></svg>' }),
        el("div", {}, el("div", { className: "name", textContent: f.name }), el("div", { className: "meta", textContent: `${f.address}${f.rssi != null ? ` · ${f.rssi} dBm` : ""}` }))),
        el("div", { className: "end" }, b));
    }));
  }));
loadAudio();

function secretRow(v) {
  const input = el("input", { type: "password", placeholder: v.set ? `Saved (${v.hint})` : "Paste here", autocomplete: "off" });
  const save = el("button", { className: "btn primary", textContent: v.set ? "Replace" : "Save" });
  save.addEventListener("click", () =>
    run(save, async () => {
      if (!input.value.trim()) throw new Error("Paste the value first");
      vault = await api(`/vault/${v.name}`, { value: input.value.trim() }, "PUT");
      renderVault();
      if (v.name === "elevenlabs_api_key") loadEleven();
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
api("/vault").then((v) => { vault = v; renderVault(); loadEleven(); }).catch(() => {});

// ---------- mic and speaker meters ----------
// Levels arrive on the event stream while the server is asked to measure; we keep asking
// while a page with meters is on screen, and it stops by itself shortly after.
function setMeters(m) {
  for (const k of ["mic", "speaker"]) {
    const dbv = m[`${k}_db`];
    const pct = dbv <= -60 ? 0 : Math.min(100, ((dbv + 60) / 60) * 100);  // -60 dB .. 0 dB
    document.querySelectorAll(`[data-meter="${k}"]`).forEach((i) => (i.style.clipPath = `inset(0 ${100 - pct}% 0 0)`));
    document.querySelectorAll(`[data-meter-db="${k}"]`).forEach((o) => (o.textContent = dbv <= -99 ? "–" : `${Math.round(dbv)} dB`));
  }
}
function wantMeters() {
  const visible = ["tab-settings", "tab-talk"].some((id) => !document.getElementById(id).hidden);
  if (visible && !document.hidden) api("/audio/meters", {}).catch(() => {});
}
setInterval(wantMeters, 8000);
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => setTimeout(wantMeters, 50)));
wantMeters();

// Mic gain and the cap on Skelly's voice: same setting, shown under both sets of meters.
function renderGains() {
  for (const k of ["mic_gain", "out_gain"]) {
    const v = audio.config[k] ?? 100;
    document.querySelectorAll(`[data-gain="${k}"]`).forEach((r) => { if (document.activeElement !== r) r.value = v; });
    document.querySelectorAll(`[data-gain-out="${k}"]`).forEach((o) => (o.textContent = `${v}%`));
  }
}
let gainTimer;
document.querySelectorAll("[data-gain]").forEach((r) =>
  r.addEventListener("input", () => {
    audio.config[r.dataset.gain] = Number(r.value);
    renderGains();
    clearTimeout(gainTimer);
    gainTimer = setTimeout(() => run(null, () => api("/audio/config", { [r.dataset.gain]: Number(r.value) }, "PUT")), 250);
  }));
renderGains();

// ---------- faces ----------
let people = [];
function renderFaceBoxes(faces = []) {
  $("#face-boxes").replaceChildren(...faces.map((f) => {
    const tag = el("button", { className: "face-tag", textContent: f.name ?? "Who's this?" });
    if (!f.name) tag.addEventListener("click", () => nameFace(f.index));
    const d = el("div", { className: `face-box${f.name ? " known" : ""}` }, tag);
    Object.assign(d.style, { left: `${f.box[0] * 100}%`, top: `${f.box[1] * 100}%`, width: `${f.box[2] * 100}%`, height: `${f.box[3] * 100}%` });
    return d;
  }));
}
function nameFace(index) {
  const name = prompt("What's this person's name?");
  if (!name?.trim()) return;
  run(null, async () => { people = await api("/faces/name", { name: name.trim(), index }); renderPeople(); }, `Skelly will remember ${name.trim()}`);
}
const ago = (t) => {
  if (!t) return "";
  const m = Math.round((Date.now() / 1000 - t) / 60);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
};
function renderPeople() {
  $("#people-sub").textContent = people.length ? `${people.length} ${people.length === 1 ? "person" : "people"}` : "Nobody yet. People who tell Skelly their name show up here.";
  $("#people-forget-all").disabled = !people.length;
  $("#people").replaceChildren(...people.map((p) => {
    const rename = el("button", { className: "btn outline small", textContent: "Rename" });
    rename.addEventListener("click", () => {
      const n = prompt("New name", p.name);
      if (n?.trim()) run(rename, async () => { people = await api(`/faces/${p.id}`, { name: n.trim() }, "PUT"); renderPeople(); });
    });
    const forget = el("button", { className: "btn outline small", textContent: "Forget" });
    forget.addEventListener("click", () => {
      if (confirm(`Forget ${p.name}'s face?`)) run(forget, async () => { people = await api(`/faces/${p.id}`, undefined, "DELETE"); renderPeople(); }, "Forgotten");
    });
    return el("li", {},
      p.thumb ? el("img", { src: `data:image/jpeg;base64,${p.thumb}`, alt: "" }) : el("span", { className: "no-thumb" }),
      el("div", { className: "p-name", textContent: p.name }),
      el("div", { className: "meta", textContent: `${p.visits || 1} ${p.visits === 1 ? "visit" : "visits"} · ${ago(p.last_seen)}` }),
      el("div", { className: "p-actions" }, rename, forget));
  }));
}
async function loadPeople() {
  try {
    const r = await api("/faces");
    people = r.people;
    renderPeople();
    renderFaceBoxes(r.seen);
  } catch {}
}
$("#people-forget-all").addEventListener("click", (ev) => {
  if (confirm("Forget every face Skelly knows? This can't be undone.")) run(ev.currentTarget, async () => { people = await api("/faces", undefined, "DELETE"); renderPeople(); }, "Forgot everyone");
});
loadPeople();

// ---------- recordings ----------
function fmtDur(s) { return s == null ? "" : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }
async function loadRecordings() {
  let list = [];
  try { list = await api("/recordings"); } catch { return; }
  const total = list.reduce((a, r) => a + r.size, 0);
  $("#rec-sub").textContent = list.length ? `${list.length} saved · ${(total / 1024 / 1024).toFixed(0)} MB` : "None yet. Turn on Record conversations above.";
  $("#recordings").replaceChildren(...list.map((r) => {
    const play = el("button", { className: "btn primary small", innerHTML: '<svg><use href="#i-play"/></svg>Play' });
    play.addEventListener("click", async () => {
      $("#rec-player").hidden = false;
      $("#rec-video").src = `/api/recordings/${r.name}`;
      $("#rec-video").play().catch(() => {});
      const t = await api(`/recordings/${r.name}/transcript`).catch(() => []);
      $("#rec-transcript").replaceChildren(...t.map((x) => el("li", { className: x.role === "user" ? "you" : "skelly" },
        el("span", { className: "who-tag", textContent: x.role === "user" ? "Visitor" : "Skelly" }), el("span", { textContent: x.text }))));
      $("#rec-player").scrollIntoView({ behavior: "smooth", block: "center" });
    });
    const dl = el("a", { className: "btn outline small", href: `/api/recordings/${r.name}?download=true`, textContent: "Download" });
    const del = el("button", { className: "btn outline small", textContent: "Delete" });
    del.addEventListener("click", () => {
      if (confirm("Delete this recording?")) run(del, async () => { await api(`/recordings/${r.name}`, undefined, "DELETE"); loadRecordings(); }, "Deleted");
    });
    const when = new Date(r.started * 1000).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    return el("li", {},
      el("div", { className: "who" }, el("span", { className: "row-ico", innerHTML: '<svg><use href="#i-camera"/></svg>' }),
        el("div", {}, el("div", { className: "name", textContent: `${when} · ${fmtDur(r.seconds)}` }),
          el("div", { className: "meta", textContent: r.preview ? `“${r.preview}”` : `${r.lines} lines` }))),
      el("div", { className: "end" }, play, dl, del));
  }));
}
$("#rec-refresh").addEventListener("click", (ev) => run(ev.currentTarget, loadRecordings));
loadRecordings();

// ---------- system ----------
const RESTART = {
  audio: ["Restart sound? Any conversation stops for a few seconds.", "Sound restarted"],
  bluetooth: ["Restart Bluetooth? Skelly disconnects and reconnects by himself.", "Bluetooth restarted. Skelly is reconnecting…"],
  camera: [null, "Camera restarted"],
  app: ["Restart the Skelly app? The page reconnects in about 15 seconds.", "Restarting the app…"],
  reboot: ["Reboot the mini PC? Everything is back in about a minute.", "Rebooting. Back in about a minute…"],
};
function dur(s) { const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60); return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`; }
const gb = (b) => (b == null ? "–" : `${(b / 1024 ** 3).toFixed(1)} GB`);
async function loadSystem() {
  try {
    const s = await api("/system");
    $("#sys-up").textContent = dur(s.uptime_s);
    $("#sys-load").textContent = `${Math.round((s.load / (s.cpus || 1)) * 100)}%`;
    $("#sys-mem").textContent = `${gb(s.mem_free)} of ${gb(s.mem_total)}`;
    $("#sys-disk").textContent = `${gb(s.disk_free)} of ${gb(s.disk_total)}`;
  } catch {}
}
document.querySelectorAll("[data-restart]").forEach((b) =>
  b.addEventListener("click", () => {
    const [ask, done] = RESTART[b.dataset.restart];
    if (ask && !confirm(ask)) return;
    run(b, () => api(`/system/restart/${b.dataset.restart}`, {}), done);
  }));
loadSystem();
setInterval(() => { if (!$("#tab-settings").hidden) loadSystem(); }, 15000);
