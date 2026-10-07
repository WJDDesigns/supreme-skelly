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
// What each eye looks like on the drawn Skelly: iris colour, plus a symbol for picture eyes.
const EYE_LOOK = {
  "Blue Eyes": ["#3fa9ff"], "Hazel Eyes": ["#9a7b3c"], "Green Eyes": ["#3ddc6b"], "Orange Eyes": ["#ff8a1a"],
  "Red Eyes": ["#ff2b2b"], "Grey Eyes": ["#b9c0c8"], "Brown Eyes": ["#7a4a22"], "Yellow Reptile Eye": ["#ffd400"],
  "Orange Reptile Eye": ["#ff7a00"], "Rainbow Swirl": ["#c04bff"], "Flames": ["#ff5a00"], "Gold Star": ["#ffd700"],
  "Skull and Crossbones": ["#f4f4f4"], "Fireworks": ["#ff3fb4"], "American Flag": ["#3c5bd6"], "Heart": ["#ff2a55"],
  "Four-Leaf Clover": ["#2fbf4a"], "Snowflake": ["#bfe9ff"], "Confetti": ["#ff9f1a"], "Ice Eye": ["#8fe3ff"],
  "Peppermint Swirl": ["#ff3b3b"], "Cyber Eye": ["#00ffd5"],
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
const TABS = ["home", "looks", "moves", "sounds", "settings"];
document.querySelectorAll(".nav button").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.tab)),
);
function showTab(name) {
  if (!TABS.includes(name)) name = "home";
  document.querySelectorAll(".nav button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((s) => (s.hidden = s.id !== `tab-${name}`));
  try { localStorage.setItem("tab", name); } catch {}
  window.scrollTo({ top: 0 });
}
let savedTab = "home";
try { savedTab = localStorage.getItem("tab") || "home"; } catch {}
showTab(savedTab);
for (const id of [".look-card", "#tab-moves", "#tab-sounds"]) $(id)?.classList.add("disabled-when-offline");
$("#conn-pill").addEventListener("click", () => showTab("home"));

// Desktop: collapse the sidebar to icons.
const setCollapsed = (on) => {
  document.body.classList.toggle("collapsed", on);
  try { localStorage.setItem("collapsed", on ? "1" : ""); } catch {}
};
try { setCollapsed(!!localStorage.getItem("collapsed")); } catch {}
$("#side-toggle").addEventListener("click", () => setCollapsed(!document.body.classList.contains("collapsed")));

// Greeting and the little bar charts on the stat cards.
const hour = new Date().getHours();
$("#greeting").textContent = hour < 5 ? "Up past midnight? Spooky." : hour < 12 ? "Good morning, boneyard boss!"
  : hour < 18 ? "Good afternoon, boneyard boss!" : "Good evening, boneyard boss!";
document.querySelectorAll(".bars").forEach((b, n) => {
  for (let i = 0; i < 9; i++) {
    const h = 20 + ((i * 37 + n * 13) % 50) + i * 4;
    b.append(Object.assign(document.createElement("i"), { style: `height:${Math.min(h, 100)}%` }));
  }
});

// The look page shows a live copy of the drawn Skelly, zoomed in on him.
const preview = $("#skelly-art").cloneNode(true);
preview.removeAttribute("id");
preview.setAttribute("viewBox", "148 6 164 196");
$("#look-preview").append(preview);

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
  $("#dev-model").textContent = state.profile?.name ?? "Unknown model";
  $("#dev-version").textContent = d.version ?? "–";
  $("#dev-bt").textContent = d.bt_name ?? "–";
  $("#dev-free").textContent = d.free_kb != null ? `${(d.free_kb / 1024).toFixed(1)} MB` : "–";
  $("#live-btn").textContent = d.live_mode ? "Live Mode is on" : "Turn on Live Mode";
  $("#live-btn").disabled = !online || d.live_mode;
  if (d.volume != null && document.activeElement !== $("#vol")) {
    $("#vol").value = d.volume;
    $("#vol-out").textContent = d.volume;
  }
  $("#stat-conn").textContent = online ? "Online" : d.status === "disconnected" ? "Offline" : "Waking…";
  $("#stat-conn-sub").textContent = online ? (d.name || "Connected") : STATUS_TEXT[d.status] || d.status;
  $("#stat-vol").textContent = d.volume ?? "–";
  $("#stat-sounds").textContent = online ? (d.files?.length ?? 0) : "–";
  $("#stat-free").textContent = d.free_kb != null ? `${(d.free_kb / 1024).toFixed(1)} MB` : "–";
  $("#stat-fw").textContent = `Firmware ${d.version ?? "–"}`;
  $("#side-status").textContent = online ? `${d.name || "Skelly"} is awake and listening.`
    : d.status === "disconnected" ? "Skelly is resting in his crypt." : "Rattling the bones…";
  if (d.error) toast(d.error, true);
  renderFiles();
}

// ---------- drawn Skelly ----------
function lightColor(look, key) {
  const lights = look?.lights ?? {};
  const e = { ...(lights.all ?? {}), ...(lights[key] ?? {}) };
  if (!e.rgb || e.brightness === 0) return e.brightness === 0 ? "transparent" : null;
  const a = (e.brightness ?? 255) / 255;
  return `rgba(${e.rgb.join(",")},${Math.max(a, .25).toFixed(2)})`;
}
function renderLook(look) {
  if (!look) return;
  state.look = look;
  const root = document.documentElement.style;
  const chest = lightColor(look, "chest") ?? lightColor(look, "light") ?? lightColor(look, "lantern");
  const head = lightColor(look, "head");
  if (chest) root.setProperty("--glow", chest);
  root.setProperty("--mouth", head ?? chest ?? "transparent");
  const eye = state.profile?.eyes.find((x) => x.value === look.eye);
  if (eye) {
    state.eye = eye.value;
    root.setProperty("--eye", (EYE_LOOK[eye.label] ?? ["#3ddc6b"])[0]);
  }
  const cyc = Object.values(look.lights ?? {}).some((e) => e.cycle);
  document.body.classList.toggle("party-mode", cyc);
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
          renderLook({ ...(state.look ?? { lights: {} }), eye: e.value });
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
    ...files.map((f) => {
      const playing = state.device.playing === f.serial;
      const btn = el("button", { className: playing ? "btn" : "btn primary", textContent: playing ? "Stop" : "Play" });
      btn.addEventListener("click", () => run(btn, () => api(`/files/${f.serial}/play`, { play: !playing })));
      const li = el("li", {},
        el("span", { className: "num", textContent: String(f.serial).padStart(2, "0") }),
        el("div", { className: "grow" }, el("div", { textContent: f.name || `Sound ${f.serial}` }),
          el("div", { className: "meta", textContent: state.device.playing === f.serial ? "Playing…" : "Stored on Skelly" })),
        btn);
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
            showTab("looks");
          }, `Connected to ${f.name}`),
        );
        return el("li", {},
          el("div", {}, el("div", { textContent: f.name }),
            el("div", { className: "meta", textContent: `${f.address}${f.rssi != null ? ` · ${f.rssi} dBm` : ""}` })),
          b);
      }),
    );
  }),
);
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
  renderLook(s.look);
}

function connectEvents() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/events`);
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.type === "snapshot") applySnapshot(msg.data);
    else if (msg.type === "state") { state.device = msg.data; renderDevice(); }
    else if (msg.type === "settings") renderSettings(msg.data);
    else if (msg.type === "look") renderLook(msg.data);
    else if (msg.type === "profile") { state.profile = msg.data; state.moves.clear(); state.mode = null; renderProfile(); }
  };
  ws.onclose = () => setTimeout(connectEvents, 1500);
}
// Load once over HTTP so the page works even before the live stream is up.
api("/state").then(applySnapshot).catch(() => {});
connectEvents();
