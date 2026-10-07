// Supreme Skelly web UI. No build step: plain ES modules talking to /api.

const $ = (sel) => document.querySelector(sel);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};

const state = { device: null, profile: null, moves: new Set(), light: "all", mode: null, eye: null, scene: null };

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
      end.append(btn);
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
      list.replaceChildren(el("li", {}, el("span", { className: "muted", textContent: "Nothing found. Is Skelly switched on and nearby?" })));
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
  renderSettings(s.settings);
  renderProfile();
  renderDevice();
}

function connectEvents() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/events`);
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.type === "snapshot") applySnapshot(msg.data);
    else if (msg.type === "state") { state.device = msg.data; renderDevice(); }
    else if (msg.type === "settings") renderSettings(msg.data);
    else if (msg.type === "profile") { state.profile = msg.data; state.moves.clear(); state.mode = null; renderProfile(); }
  };
  ws.onclose = () => setTimeout(connectEvents, 1500);
}
// Load once over HTTP so the page works even before the live stream is up.
api("/state").then(applySnapshot).catch(() => {});
connectEvents();
