// Runs before the app: asks for the password if one is set, and shows the first-run welcome.

import { timeZones } from "./zones.js";

const $ = (sel) => document.querySelector(sel);

async function call(path, body, method = "POST") {
  const res = await fetch(`/api${path}`, {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof out.detail === "string" ? out.detail : `Request failed (${res.status})`);
  return out;
}

function shake(form, msg) {
  let p = form.querySelector(".gate-error");
  if (!p) p = form.appendChild(Object.assign(document.createElement("p"), { className: "gate-error" }));
  p.textContent = msg;
}

function show(form) {
  $("#gate").hidden = false;
  form.hidden = false;
  form.querySelector("input, select")?.focus();
  return new Promise((done) => { form._done = done; });
}

async function login() {
  const form = $("#login");
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try { await call("/auth/login", { password: $("#login-pw").value }); form._done(); }
    catch (e) { shake(form, e.message); }
  });
  await show(form);
  form.hidden = true;
}

async function setup() {
  const form = $("#setup");
  timeZones($("#setup-tz"));
  const finish = async (password) => {
    try {
      if (password) await call("/auth/password", { new: password }, "PUT");
      await call("/settings", { timezone: $("#setup-tz").value, setup_done: true }, "PATCH");
      form._done();
    } catch (e) { shake(form, e.message); }
  };
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const pw = $("#setup-pw").value;
    if (pw.length < 6) return shake(form, "Use at least 6 characters, or skip below.");
    finish(pw);
  });
  $("#setup-skip").addEventListener("click", () => finish(""));
  await show(form);
  form.hidden = true;
}

let status = { password: false, signed_in: true, setup_done: true };
try { status = await (await fetch("/api/auth/status")).json(); } catch {}
if (!status.signed_in) await login();
if (!status.setup_done) await setup();
$("#gate").hidden = true;
await import("./app.js");
