// Supreme Skelly web UI. No build step: plain ES modules talking to /api.

const $ = (sel) => document.querySelector(sel);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};

const state = { device: null, profile: null, moves: new Set(), light: "all", mode: null, eye: null };

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

// ---------- rendering ----------
const STATUS_TEXT = {
  disconnected: "Not connected",
  scanning: "Scanning…",
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
  $("#live-btn").textContent = d.live_mode ? "Live Mode is on" : "Turn on Live Mode";
  $("#live-btn").disabled = !online || d.live_mode;
  if (d.volume != null && document.activeElement !== $("#vol")) {
    $("#vol").value = d.volume;
    $("#vol-out").textContent = d.volume;
  }
  if (d.error) toast(d.error, true);
  renderFiles();
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
      });
      return c;
    }),
  );
  // eyes
  $("#eyes-card").hidden = !p.eyes.length;
  $("#eyes").replaceChildren(
    ...p.eyes.map((e) => {
      const b = el("button", { className: "eye" }, el("span", { className: "num", textContent: e.value }), e.label);
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
    ...p.light_modes.map((m) => segButton(m.label, state.mode === m.value, () => { state.mode = m.value; renderProfile(); })),
  );
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
        el("div", {}, el("div", { textContent: f.name || `Sound ${f.serial}` }),
          el("div", { className: "meta", textContent: `#${f.serial}` })),
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
            showTab("controls");
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

// ---------- controls tab ----------
$("#moves-apply").addEventListener("click", (ev) =>
  run(ev.currentTarget, () => api("/movement", { parts: [...state.moves] }),
    state.moves.size ? "Movement updated" : "Movement off"),
);

const SWATCHES = ["#ff0000", "#ff7a1a", "#ffd000", "#00ff40", "#00c8ff", "#7a00ff", "#ff00c8", "#ffffff"];
$("#swatches").replaceChildren(
  ...SWATCHES.map((c) => {
    const b = el("button", { title: c });
    b.style.background = c;
    b.addEventListener("click", () => { $("#color").value = c; });
    return b;
  }),
);
for (const [id, out] of [["bright", "bright-out"], ["speed", "speed-out"]]) {
  $(`#${id}`).addEventListener("input", (e) => { $(`#${out}`).textContent = e.target.value; });
}
$("#lights-apply").addEventListener("click", (ev) =>
  run(ev.currentTarget, () =>
    api("/light", {
      light: state.light,
      mode: state.mode ?? undefined,
      brightness: Number($("#bright").value),
      color: $("#color").value,
      cycle: $("#cycle").checked,
      speed: Number($("#speed").value),
    }), "Lights updated"),
);
$("#lights-off").addEventListener("click", (ev) =>
  run(ev.currentTarget, () => api("/light", { light: state.light, brightness: 0 }), "Lights off"),
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
  renderProfile();
  renderDevice();
}

function connectEvents() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/events`);
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.type === "snapshot") applySnapshot(msg.data);
    else if (msg.type === "state") { state.device = msg.data; renderDevice(); }
    else if (msg.type === "profile") { state.profile = msg.data; state.moves.clear(); state.mode = null; renderProfile(); }
  };
  ws.onclose = () => setTimeout(connectEvents, 1500);
}
// Load once over HTTP so the page works even before the live stream is up.
api("/state").then(applySnapshot).catch(() => {});
connectEvents();
