// Viewer page: the live camera with both sides of the conversation, heard on this device.
//
// One WebSocket brings everything (see server/skelly/viewer.py). Binary messages start with a
// byte saying what they are: 1 a JPEG frame, 2 a slice of mic sound, 3 a slice of what
// Skelly's speaker plays (16-bit mono PCM). The sliders and mute buttons only change what
// you hear here; Skelly's own speaker and mic are left alone.

const VIDEO = 1, MIC = 2, SPEAKER = 3;
const RATE = 24000;
const CH = { [MIC]: "mic", [SPEAKER]: "speaker" };
const LEAD_S = 0.2;  // a little cushion so uneven Wi-Fi doesn't make the sound stutter
const MAX_LAG_S = 1.0;  // further behind than this and we skip ahead to stay live

export function initViewer({ $, el, lineEl }) {
  const section = $("#tab-viewer");
  const img = $("#viewer-img");
  let ws = null, retry = 0, status = {}, frames = 0, fps = 0, lastFrameAt = 0;
  let listening = false, ctx = null;
  const gains = {}, next = { mic: 0, speaker: 0 }, level = { mic: 0, speaker: 0 };
  const mix = load();

  function load() {
    try { return { mic: { vol: 100, mute: false }, speaker: { vol: 100, mute: false }, ...JSON.parse(localStorage.getItem("viewer-mix")) }; }
    catch { return { mic: { vol: 100, mute: false }, speaker: { vol: 100, mute: false } }; }
  }
  function save() { try { localStorage.setItem("viewer-mix", JSON.stringify(mix)); } catch {} }

  // ---------- connection: open while the page is on screen, or while you're listening ----------
  const onScreen = () => !section.hidden && !document.hidden;
  const wanted = () => onScreen() || listening;
  function tell() {
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ listen: listening, video: onScreen() }));
  }
  function connect() {
    if (ws || !wanted()) return;
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/viewer`);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => { retry = 0; tell(); };
    ws.onmessage = (e) => (typeof e.data === "string" ? onStatus(JSON.parse(e.data)) : onData(e.data));
    ws.onclose = () => {
      ws = null;
      onStatus({ video: "off", video_note: "", mic: false, speaker: false, closed: true });
      if (wanted()) setTimeout(connect, Math.min(10000, 1000 * 2 ** retry++));
    };
  }
  function sync() {
    if (!wanted()) { ws?.close(); return; }
    if (!ws) connect(); else tell();
  }
  new MutationObserver(sync).observe(section, { attributes: true, attributeFilter: ["hidden"] });
  document.addEventListener("visibilitychange", sync);

  // ---------- picture ----------
  function onData(buf) {
    const kind = new Uint8Array(buf, 0, 1)[0];
    if (kind === VIDEO) return showFrame(buf);
    if (CH[kind]) play(CH[kind], buf);
  }
  function showFrame(buf) {
    const old = img.src;
    img.src = URL.createObjectURL(new Blob([new Uint8Array(buf, 1)], { type: "image/jpeg" }));
    if (old.startsWith("blob:")) URL.revokeObjectURL(old);
    img.hidden = false;
    $("#viewer-empty").hidden = true;
    frames++;
    lastFrameAt = performance.now();
  }
  setInterval(() => {  // frame rate, and notice a picture that froze
    fps = frames / 2; frames = 0;
    if (img.hidden || performance.now() - lastFrameAt < 6000) return renderStatus();
    img.hidden = true;
    renderStatus();
  }, 2000);
  function onStatus(s) {
    if (s.type && s.type !== "status") return;
    status = s;
    renderStatus();
  }
  function renderStatus() {
    const live = !img.hidden;
    const empty = $("#viewer-empty");
    empty.hidden = live;
    empty.lastElementChild.textContent = status.closed ? "Reconnecting…"
      : status.video === "unset" || status.video === "error" ? status.video_note || "No picture"
      : "Connecting to the camera…";
    $("#viewer-live").hidden = !live;
    const parts = [live ? (status.video === "slow" ? "A picture a second" : `Live${fps ? ` · ${Math.round(fps)} fps` : ""}`) : "No picture yet"];
    if (listening) parts.push(status.mic || status.speaker ? "listening" : "waiting for sound…");
    $("#viewer-sub").textContent = parts.join(" · ");
  }

  // ---------- sound ----------
  function startAudio() {
    if (ctx) return ctx.resume();
    try { if (navigator.audioSession) navigator.audioSession.type = "playback"; } catch {}  // iPhone: play even on silent
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    for (const ch of ["mic", "speaker"]) {
      gains[ch] = ctx.createGain();
      gains[ch].connect(ctx.destination);
      applyMix(ch);
    }
    return ctx.resume();
  }
  function play(ch, buf) {
    if (!ctx || !listening) return;
    const pcm = new Int16Array(buf.slice(1));
    const out = ctx.createBuffer(1, pcm.length, RATE);
    const d = out.getChannelData(0);
    let sum = 0;
    for (let i = 0; i < pcm.length; i++) { d[i] = pcm[i] / 32768; sum += d[i] * d[i]; }
    level[ch] = Math.max(Math.sqrt(sum / (pcm.length || 1)), level[ch] * 0.7);
    const src = ctx.createBufferSource();
    src.buffer = out;
    src.connect(gains[ch]);
    const now = ctx.currentTime;
    if (next[ch] < now + 0.02 || next[ch] > now + MAX_LAG_S) next[ch] = now + LEAD_S;
    src.start(next[ch]);
    next[ch] += out.duration;
  }
  function applyMix(ch) {
    const m = mix[ch];
    if (gains[ch]) gains[ch].gain.setTargetAtTime(m.mute ? 0 : m.vol / 100, ctx.currentTime, 0.03);
    const r = section.querySelector(`[data-vvol="${ch}"]`);
    if (document.activeElement !== r) r.value = m.vol;
    section.querySelector(`[data-vvol-out="${ch}"]`).textContent = m.mute ? "Muted" : `${m.vol}%`;
    const b = section.querySelector(`[data-vmute="${ch}"]`);
    b.setAttribute("aria-pressed", m.mute);
    b.innerHTML = `<svg><use href="#i-${m.mute ? "mute" : "volume"}"/></svg>`;
    b.closest(".mix").classList.toggle("muted", m.mute);
  }
  function drawMeters() {
    for (const ch of ["mic", "speaker"]) {
      const v = listening ? level[ch] : 0;
      level[ch] *= 0.85;
      const dbv = v > 1e-5 ? 20 * Math.log10(v) : -100;
      const pct = dbv <= -60 ? 0 : Math.min(100, ((dbv + 60) / 60) * 100);
      section.querySelector(`[data-vmeter="${ch}"]`).style.clipPath = `inset(0 ${100 - pct}% 0 0)`;
    }
    requestAnimationFrame(drawMeters);
  }
  requestAnimationFrame(drawMeters);

  section.querySelectorAll("[data-vvol]").forEach((r) => r.addEventListener("input", () => {
    const ch = r.dataset.vvol;
    mix[ch].vol = Number(r.value);
    mix[ch].mute = false;
    applyMix(ch); save();
  }));
  section.querySelectorAll("[data-vmute]").forEach((b) => b.addEventListener("click", () => {
    const ch = b.dataset.vmute;
    mix[ch].mute = !mix[ch].mute;
    applyMix(ch); save();
  }));
  ["mic", "speaker"].forEach(applyMix);

  const listenBtn = $("#viewer-listen");
  listenBtn.addEventListener("click", async () => {
    listening = !listening;
    if (listening) await startAudio(); else { ctx?.suspend(); next.mic = next.speaker = 0; }
    listenBtn.dataset.on = listening;
    listenBtn.className = listening ? "btn outline" : "btn primary";
    listenBtn.innerHTML = `<svg><use href="#i-${listening ? "mute" : "volume"}"/></svg><span>${listening ? "Stop listening" : "Listen"}</span>`;
    $("#viewer-hint").hidden = listening;
    sync();
    renderStatus();
  });

  const view = $("#viewer-view");
  const full = $("#viewer-full");
  const goFull = view.requestFullscreen || view.webkitRequestFullscreen;
  full.hidden = !goFull;
  full.addEventListener("click", () => {
    if (document.fullscreenElement || document.webkitFullscreenElement) (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    else goFull.call(view);
  });

  // ---------- what's being said (the same words as the Conversation page) ----------
  const ul = $("#viewer-transcript");
  let chatting = false;
  function addLine(t) {
    if (ul.firstElementChild?.dataset.placeholder) ul.replaceChildren();
    ul.append(lineEl(t));
    while (ul.children.length > 80) ul.firstChild.remove();
    ul.scrollTop = ul.scrollHeight;
  }
  function talk(c) {
    const on = ["connecting", "listening", "thinking", "speaking"].includes(c.state);
    $("#viewer-chat").hidden = !on;
    $("#viewer-chat").textContent = c.state === "speaking" ? "Skelly is talking" : c.state === "thinking" ? "Skelly is thinking" : "Chatting";
    if (on && !chatting && ul.children.length && !ul.firstElementChild.dataset.placeholder) {
      ul.append(el("li", { className: "note" }, el("span", { textContent: "New chat" })));
    }
    if (on && !chatting && c.transcript?.length && ul.firstElementChild?.dataset.placeholder) c.transcript.forEach(addLine);
    chatting = on;
    $("#viewer-talk-sub").textContent = on ? "Live, as it's said" : "The last chat stays here until the next one";
  }
  ul.firstElementChild.dataset.placeholder = "1";

  sync();
  return { addLine, talk };
}
