// Music page: songs, playlists and the show settings. Skelly sings the voice; the band goes to the other speakers.

const STATUS = {
  waiting: "Waiting its turn", fetching: "Fetching", splitting: "Separating voice from band",
  analysing: "Finding the beat", failed: "Couldn't get ready",
};
const PALETTE_NAMES = { "": "A different set each song", halloween: "Halloween", blood: "Blood red", ghost: "Ghostly blues", party: "Party" };

export function initMusic({ $, el, api, run, toast }) {
  const m = { library: { songs: [], playlists: [] }, player: {}, config: {}, open: null };

  const fmt = (s) => { s = Math.max(0, Math.round(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  const song = (id) => m.library.songs.find((s) => s.id === id);
  const ready = (ids) => ids.filter((id) => song(id)?.status === "ready");
  const icon = (name) => { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.innerHTML = `<use href="#i-${name}"/>`; return s; };
  const iconBtn = (name, title, cls = "btn outline icon-only") => { const b = el("button", { className: cls, title }); b.append(icon(name)); return b; };

  async function load() {
    const s = await api("/music");
    m.library = s.library; m.player = s.player; m.config = s.config;
    $("#mu-palette").replaceChildren(...["", ...s.palettes].map((k) => el("option", { value: k, textContent: PALETTE_NAMES[k] ?? k })));
    renderConfig(); renderLibrary(); renderPlayer();
  }

  // ---------- now playing ----------
  function renderPlayer(p = m.player) {
    m.player = p;
    const on = !!p.playing;
    $("#mu-title").textContent = on ? p.title || "Song" : "Nothing playing";
    const pl = m.library.playlists.find((x) => x.id === p.playlist);
    $("#mu-sub").textContent = on
      ? [p.artist, pl ? `${pl.name} · song ${p.index + 1} of ${p.queue.length}` : p.queue.length > 1 ? `song ${p.index + 1} of ${p.queue.length}` : ""].filter(Boolean).join(" · ")
      : "Pick a song or a playlist below and Skelly will sing it.";
    $("#mu-badge").hidden = !on;
    $("#mu-badge").textContent = p.paused ? "Paused" : "Singing";
    $("#mu-pos").textContent = fmt(on ? p.position : 0);
    $("#mu-dur").textContent = fmt(on ? p.duration : 0);
    $("#mu-bar").style.width = on && p.duration ? `${Math.min(100, (p.position / p.duration) * 100)}%` : "0%";
    const play = $("#mu-play");
    play.replaceChildren(icon(on ? (p.paused ? "play" : "pause") : "play"), el("span", { textContent: on ? (p.paused ? "Carry on" : "Pause") : "Sing everything" }));
    $("#mu-stop").hidden = !on;
    $("#mu-prev").disabled = !on;
    $("#mu-next").disabled = !on;
    $("#mu-error").hidden = !p.error;
    $("#mu-error").textContent = p.error || "";
    document.querySelectorAll("#mu-songs li").forEach((li) => li.classList.toggle("playing", on && li.dataset.id === p.song));
  }
  $("#mu-play").addEventListener("click", (e) => {
    const p = m.player;
    if (p.playing) return run(e.currentTarget, () => api("/music/pause", { paused: !p.paused }).then(renderPlayer));
    return run(e.currentTarget, () => api("/music/play", {}).then(renderPlayer));
  });
  $("#mu-stop").addEventListener("click", (e) => run(e.currentTarget, () => api("/music/stop", {}).then(renderPlayer)));
  $("#mu-next").addEventListener("click", (e) => run(e.currentTarget, () => api("/music/skip", { step: 1 })));
  $("#mu-prev").addEventListener("click", (e) => run(e.currentTarget, () => api("/music/skip", { step: m.player.position > 5 ? 0 : -1 })));

  // ---------- adding ----------
  $("#mu-add").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("#mu-text").value.trim();
    if (!text) return;
    run($("#mu-add-btn"), async () => {
      const out = await api("/music/add", { text, playlist: $("#mu-to").value || null });
      $("#mu-text").value = "";
      toast(out.added.length === 1 ? `Added "${out.added[0].title}". Getting it ready…` : `Added ${out.added.length} songs. Getting them ready…`);
    });
  });
  async function upload(files) {
    for (const f of files) {
      const form = new FormData();
      form.append("file", f);
      form.append("playlist", $("#mu-to").value || "");
      const res = await fetch("/api/music/upload", { method: "POST", body: form });
      if (!res.ok) { let msg = `Upload failed (${res.status})`; try { msg = (await res.json()).detail || msg; } catch {} toast(msg, true); return; }
    }
    toast(files.length === 1 ? `Added ${files[0].name}. Getting it ready…` : `Added ${files.length} songs. Getting them ready…`);
  }
  $("#mu-file").addEventListener("change", () => { upload([...$("#mu-file").files]); $("#mu-file").value = ""; });
  const drop = $("#mu-drop");
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); upload([...e.dataTransfer.files]); });

  // ---------- songs ----------
  function renderLibrary(lib = m.library) {
    m.library = lib;
    const songs = lib.songs;
    const nReady = songs.filter((s) => s.status === "ready").length;
    $("#mu-count").textContent = songs.length ? `${nReady} ready${songs.length > nReady ? `, ${songs.length - nReady} getting ready` : ""}` : "No songs yet";
    $("#mu-clear").hidden = nReady === songs.length;
    const to = $("#mu-to").value;
    $("#mu-to").replaceChildren(el("option", { value: "", textContent: "Library only" }),
      ...lib.playlists.map((p) => el("option", { value: p.id, textContent: p.name })));
    $("#mu-to").value = lib.playlists.some((p) => p.id === to) ? to : "";
    const list = $("#mu-songs");
    if (!songs.length) {
      list.replaceChildren(el("li", {}, el("span", { className: "muted", textContent: "Add a link or a song name above to get started." })));
    } else {
      list.replaceChildren(...[...songs].reverse().map(songRow));
    }
    renderPlaylists();
    renderPlayer();
  }

  function songRow(s) {
    const li = el("li", { className: "sound" });
    li.dataset.id = s.id;
    const isReady = s.status === "ready";
    let meta;
    if (isReady) meta = [s.artist, fmt(s.duration), s.bpm ? `${Math.round(s.bpm)} BPM` : "", s.singing === 0 ? "no singing found" : ""].filter(Boolean).join(" · ");
    else if (s.status === "failed") meta = s.error || STATUS.failed;
    else meta = `${STATUS[s.status] ?? s.status}${s.status === "splitting" ? ` ${s.progress || 0}%` : "…"}`;
    const end = el("div", { className: "end" });
    if (isReady) {
      const sing = el("button", { className: "btn primary small", textContent: "Sing" });
      sing.addEventListener("click", () => run(sing, () => api("/music/play", { songs: [s.id] }).then(renderPlayer)));
      end.append(sing);
      if (m.library.playlists.length) {
        const add = el("select", { className: "mini-select", title: "Add to a playlist" },
          el("option", { value: "", textContent: "+ Playlist" }),
          ...m.library.playlists.map((p) => el("option", { value: p.id, textContent: p.name, disabled: p.songs.includes(s.id) })));
        add.addEventListener("change", () => {
          const p = m.library.playlists.find((x) => x.id === add.value);
          add.value = "";
          if (p) run(null, () => api("/music/playlists", { id: p.id, name: p.name, songs: [...p.songs, s.id] }, "PUT"), `Added to ${p.name}`);
        });
        end.append(add);
      }
    } else if (s.status === "failed") {
      const retry = el("button", { className: "btn outline small", textContent: "Try again" });
      retry.addEventListener("click", () => run(retry, () => api(`/music/songs/${s.id}/retry`, {})));
      end.append(retry);
    } else {
      end.append(el("span", { className: "badge orange", textContent: s.status === "waiting" ? "Queued" : "Working" }));
    }
    const del = iconBtn("trash", "Remove this song");
    del.addEventListener("click", () => {
      if (confirm(`Remove "${s.title}"?`)) run(del, () => api(`/music/songs/${s.id}`, undefined, "DELETE"));
    });
    end.append(del);
    li.append(el("div", { className: "who" },
      el("div", {}, el("div", { className: "name", textContent: s.title }), el("div", { className: "meta", textContent: meta }))), end);
    if (!isReady && s.status !== "failed") {
      li.append(el("div", { className: "bar song-bar" }, el("i", { style: `width:${s.status === "splitting" ? s.progress || 0 : s.status === "analysing" ? 100 : 4}%` })));
    }
    li.classList.toggle("off", s.status === "failed");
    return li;
  }

  // ---------- playlists ----------
  function renderPlaylists() {
    const list = $("#mu-playlists");
    const pls = m.library.playlists;
    if (!pls.length) {
      list.replaceChildren(el("li", {}, el("span", { className: "muted", textContent: "No playlists yet. Make one, then add songs with \"+ Playlist\"." })));
      return;
    }
    list.replaceChildren(...pls.map((p) => {
      const open = m.open === p.id;
      const n = ready(p.songs).length;
      const li = el("li", { className: "sound playlist" });
      const play = el("button", { className: "btn primary small", textContent: "Sing", disabled: !n });
      play.addEventListener("click", () => run(play, () => api("/music/play", { playlist: p.id }).then(renderPlayer)));
      const shuffle = el("button", { className: "btn outline small", textContent: "🔀", title: "Shuffle", disabled: n < 2 });
      shuffle.addEventListener("click", () => run(shuffle, () => api("/music/play", { playlist: p.id, shuffle: true }).then(renderPlayer)));
      const loop = el("button", { className: "btn outline small", textContent: "🔁", title: "Play on repeat", disabled: !n });
      loop.addEventListener("click", () => run(loop, () => api("/music/play", { playlist: p.id, loop: true }).then(renderPlayer), `${p.name} on repeat`));
      const edit = el("button", { className: "btn outline small", textContent: open ? "Done" : "Edit" });
      edit.addEventListener("click", () => { m.open = open ? null : p.id; renderPlaylists(); });
      li.append(el("div", { className: "who" }, el("div", {}, el("div", { className: "name", textContent: p.name }),
        el("div", { className: "meta", textContent: `${p.songs.length} song${p.songs.length === 1 ? "" : "s"}${p.songs.length > n ? ` (${n} ready)` : ""}` }))),
      el("div", { className: "end" }, play, shuffle, loop, edit));
      if (open) li.append(playlistEditor(p));
      return li;
    }));
  }

  function savePl(p, changes) {
    return run(null, () => api("/music/playlists", { id: p.id, name: p.name, songs: p.songs, ...changes }, "PUT"));
  }

  function playlistEditor(p) {
    const box = el("div", { className: "pl-editor" });
    const name = el("input", { type: "text", value: p.name, maxLength: 60, "aria-label": "Playlist name" });
    name.addEventListener("change", () => savePl(p, { name: name.value }));
    const del = el("button", { className: "btn outline small", textContent: "Delete playlist" });
    del.addEventListener("click", () => { if (confirm(`Delete the playlist "${p.name}"? The songs stay in your library.`)) run(del, () => api(`/music/playlists/${p.id}`, undefined, "DELETE")); });
    box.append(el("div", { className: "row" }, name, del));
    const rows = el("ol", { className: "pl-songs" });
    p.songs.forEach((id, i) => {
      const s = song(id);
      const up = el("button", { className: "btn outline icon-only arrow", textContent: "↑", disabled: i === 0, title: "Move up" });
      up.addEventListener("click", () => { const a = [...p.songs]; [a[i - 1], a[i]] = [a[i], a[i - 1]]; savePl(p, { songs: a }); });
      const down = el("button", { className: "btn outline icon-only arrow", textContent: "↓", disabled: i === p.songs.length - 1, title: "Move down" });
      down.addEventListener("click", () => { const a = [...p.songs]; [a[i + 1], a[i]] = [a[i], a[i + 1]]; savePl(p, { songs: a }); });
      const out = el("button", { className: "btn outline icon-only", textContent: "✕", title: "Take off this playlist" });
      out.addEventListener("click", () => savePl(p, { songs: p.songs.filter((_, j) => j !== i) }));
      const sing = el("button", { className: "btn outline small", textContent: "Sing from here", disabled: s?.status !== "ready" });
      sing.addEventListener("click", () => run(sing, () => api("/music/play", { playlist: p.id, start: i }).then(renderPlayer)));
      rows.append(el("li", {}, el("span", { className: "name", textContent: s ? s.title : "?" }), el("span", { className: "end" }, sing, up, down, out)));
    });
    if (!p.songs.length) rows.append(el("li", { className: "muted", textContent: "Empty. Use \"+ Playlist\" next to a song, or pick this playlist in \"Add to\" when adding songs." }));
    box.append(rows);
    return box;
  }

  $("#mu-clear").addEventListener("click", (e) => {
    const n = m.library.songs.filter((s) => s.status !== "ready").length;
    if (confirm(`Remove the ${n} song${n === 1 ? "" : "s"} that aren't ready yet? Ready songs stay.`)) {
      run(e.currentTarget, () => api("/music/songs", undefined, "DELETE"), `Removed ${n} song${n === 1 ? "" : "s"}`);
    }
  });
  $("#mu-new").addEventListener("click", (e) => {
    const name = prompt("Name for the new playlist", "Halloween night");
    if (name === null) return;
    run(e.currentTarget, async () => { const p = await api("/music/playlists", { name, songs: [] }, "PUT"); m.open = p.id; });
  });

  // ---------- show settings ----------
  function renderConfig(cfg = m.config) {
    m.config = cfg;
    document.querySelectorAll("[data-mcfg]").forEach((i) => {
      const v = cfg[i.dataset.mcfg];
      if (document.activeElement === i && i.type === "range") return;
      if (i.type === "checkbox") i.checked = !!v; else if (v != null) i.value = v;
    });
    $("#mu-voice-out").textContent = `${cfg.voice_level}%`;
    $("#mu-band-out").textContent = `${cfg.skelly_band}%`;
    $("#mu-spk-out").textContent = `${cfg.speakers_level}%`;
    $("#mu-sync-out").textContent = `${cfg.sync_ms > 0 ? "+" : ""}${cfg.sync_ms} ms`;
    $("#mu-off-out").textContent = `${cfg.speakers_offset_ms > 0 ? "+" : ""}${cfg.speakers_offset_ms} ms`;
    $("#mu-gap-out").textContent = `${cfg.gap_s} s`;
    document.querySelectorAll('[data-mcfg="speakers_voice"], [data-mcfg="speakers_level"]').forEach((i) => { i.disabled = !cfg.speakers; });
    $("#mu-palette").disabled = !cfg.lights;
  }
  let saveTimer;
  let pending = {};
  document.querySelectorAll("[data-mcfg]").forEach((i) => {
    i.addEventListener(i.type === "range" ? "input" : "change", () => {
      const k = i.dataset.mcfg;
      const v = i.type === "checkbox" ? i.checked : i.tagName === "SELECT" ? i.value : Number(i.value);
      renderConfig({ ...m.config, [k]: v });
      pending[k] = v;
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => {
        const body = pending;
        pending = {};
        run(null, () => api("/music/config", body, "PUT").then(renderConfig));
      }, i.type === "range" ? 150 : 0);
    });
  });

  $("#mu-reset-mix").addEventListener("click", () => run(null, () =>
    api("/music/config", { voice_level: 100, skelly_band: 100, speakers_level: 100 }, "PUT").then(renderConfig)));

  load().catch((e) => toast(e.message, true));
  return { renderPlayer, renderLibrary };
}
