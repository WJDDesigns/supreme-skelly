// Live preview of the 6ft Ultra Skelly: chest and mouth glow, eye screens and movement,
// redrawn from the "look" the service reports after every change.

const IRIS = {
  "Blue Eyes": "#3aa0ff", "Hazel Eyes": "#a8873a", "Green Eyes": "#3fdc5a", "Orange Eyes": "#ff8a00",
  "Red Eyes": "#ff2a2a", "Grey Eyes": "#9aa3ad", "Brown Eyes": "#7a4a22", "Ice Eye": "#bdf3ff", "Cyber Eye": "#00ffd5",
};

function ribs() {
  let out = "";
  for (let i = 0; i < 7; i++) {
    const y = 214 + i * 15;
    const w = 54 - Math.abs(i - 2.5) * 4;
    for (const s of [-1, 1]) {
      const x0 = 150 + s * 8;
      out += `<path class="bone-line rib" d="M${x0} ${y} C ${150 + s * (w * .7)} ${y - 8}, ${150 + s * (w + 8)} ${y + 2}, ${150 + s * w} ${y + 20}"/>`;
    }
  }
  return out;
}

function vertebrae(y0, n, gap = 11, w = 16) {
  let out = "";
  for (let i = 0; i < n; i++) out += `<rect class="bone" x="${150 - w / 2}" y="${y0 + i * gap}" width="${w}" height="${gap - 3}" rx="3"/>`;
  return out;
}

function hand(s) {
  // s = -1 left, 1 right
  const x = 150 + s * 83;
  let f = "";
  for (let i = 0; i < 4; i++) {
    const fx = x + s * (-9 + i * 6);
    f += `<path class="bone-line finger" d="M${fx} 378 l ${s * (i - 1.5) * 2} 14 l ${s * (i - 1.5)} 10"/>`;
  }
  return `<ellipse class="bone" cx="${x}" cy="374" rx="12" ry="9"/>${f}<path class="bone-line finger" d="M${x - s * 10} 372 l ${-s * 8} 10"/>`;
}

function arm(s) {
  const sx = 150 + s * 58, ex = 150 + s * 84, wx = 150 + s * 80;
  return `<g class="arm arm-${s < 0 ? "l" : "r"}" style="transform-origin:${sx}px 208px">
    <path class="bone-line limb" d="M${sx} 210 L ${ex} 290"/>
    <circle class="bone" cx="${ex}" cy="292" r="8"/>
    <path class="bone-line limb thin" d="M${ex - s * 2} 298 L ${wx} 366"/>
    <path class="bone-line limb thin" d="M${ex + s * 4} 298 L ${wx + s * 6} 366"/>
    ${hand(s)}
  </g>`;
}

export function skeletonSVG() {
  return `<svg class="skelly-svg" viewBox="0 0 300 430" role="img" aria-label="Live preview of Skelly">
  <defs>
    <linearGradient id="bone-grad" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#f3ead6"/><stop offset=".55" stop-color="#d9caa8"/><stop offset="1" stop-color="#a99572"/>
    </linearGradient>
    <radialGradient id="chest-grad"><stop offset="0" style="stop-color:var(--chest);stop-opacity:1"/><stop offset=".6" style="stop-color:var(--chest);stop-opacity:.55"/><stop offset="1" style="stop-color:var(--chest);stop-opacity:0"/></radialGradient>
    <radialGradient id="mouth-grad"><stop offset="0" style="stop-color:var(--mouth)"/><stop offset="1" style="stop-color:var(--mouth);stop-opacity:0"/></radialGradient>
    <filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="9"/></filter>
    <filter id="soft" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3"/></filter>
    <clipPath id="eye-clip"><circle r="12.5"/></clipPath>
  </defs>

  <g class="torso" style="transform-origin:150px 380px">
    <g class="fx chest-fx">
      <ellipse class="chest-glow" cx="150" cy="262" rx="74" ry="80" fill="url(#chest-grad)" filter="url(#glow)"/>
      <ellipse class="chest-core" cx="150" cy="258" rx="44" ry="54" style="fill:var(--chest)" opacity=".85" filter="url(#glow)"/>
    </g>
    ${vertebrae(208, 9, 11, 14)}
    ${ribs()}
    <rect class="bone" x="143" y="206" width="14" height="74" rx="7"/>
    <g class="fx chest-fx tint"><ellipse cx="150" cy="258" rx="66" ry="70" style="fill:var(--chest)" filter="url(#glow)"/></g>
    <path class="bone-line clav" d="M148 204 C 130 198, 108 202, 92 210"/>
    <path class="bone-line clav" d="M152 204 C 170 198, 192 202, 208 210"/>
    <circle class="bone" cx="92" cy="210" r="9"/><circle class="bone" cx="208" cy="210" r="9"/>
    ${arm(-1)}${arm(1)}

    <g class="head" style="transform-origin:150px 170px">
      ${vertebrae(152, 4, 10, 18)}
      <g transform="translate(150 150) scale(.86) translate(-150 -150)">
      <path class="bone skull" d="M150 18 C 106 18 90 52 92 86 C 93 104 100 116 108 124 L 110 138 C 112 146 122 150 150 150 C 178 150 188 146 190 138 L 192 124 C 200 116 207 104 208 86 C 210 52 194 18 150 18 Z"/>
      <g class="fx mouth-fx"><ellipse cx="150" cy="140" rx="30" ry="14" fill="url(#mouth-grad)" filter="url(#soft)"/></g>
      <path class="mouth" d="M126 133 Q150 146 174 133 L 172 142 Q150 152 128 142 Z"/>
      <path class="teeth" d="M131 135 v8 M137 137 v8 M143 138 v8 M150 139 v8 M157 138 v8 M163 137 v8 M169 135 v8"/>
      <path class="socket" d="M150 104 L 142 121 Q150 125 158 121 Z"/>
      <circle class="socket" cx="127" cy="92" r="18"/><circle class="socket" cx="173" cy="92" r="18"/>
      <g class="eye-scr" transform="translate(127 92)"><g clip-path="url(#eye-clip)" class="eye-img"></g></g>
      <g class="eye-scr" transform="translate(173 92)"><g clip-path="url(#eye-clip)" class="eye-img"></g></g>
      </g>
    </g>
  </g>

  <g class="pelvis">
    ${vertebrae(310, 4, 11, 16)}
    <path class="bone" d="M150 352 C 128 344 104 344 98 362 C 94 376 106 392 126 398 L 138 410 L 150 404 L 162 410 L 174 398 C 194 392 206 376 202 362 C 196 344 172 344 150 352 Z"/>
    <ellipse class="socket" cx="128" cy="384" rx="9" ry="7"/><ellipse class="socket" cx="172" cy="384" rx="9" ry="7"/>
  </g>
</svg>`;
}

// ---------- eye screens ----------
const bg = (c = "#050505") => `<circle r="13" fill="${c}"/>`;

function eyeArt(label) {
  if (!label) return `${bg("#16110c")}<circle r="5" fill="#2b241c"/>`;
  if (IRIS[label]) {
    const c = IRIS[label];
    return `${bg("#f6f3ee")}<circle r="7.5" fill="${c}"/><circle r="7.5" fill="none" stroke="#000" stroke-opacity=".35" stroke-width="1"/><circle r="3.2" fill="#050505"/><circle cx="-2.4" cy="-2.6" r="1.4" fill="#fff"/>`;
  }
  switch (label) {
    case "Yellow Reptile Eye":
    case "Orange Reptile Eye": {
      const c = label.startsWith("Yellow") ? "#ffd400" : "#ff7a00";
      return `${bg(c)}<circle r="11" fill="none" stroke="#000" stroke-opacity=".25" stroke-width="4"/><ellipse rx="1.9" ry="10" fill="#050505"/><circle cx="-4" cy="-4" r="1.5" fill="#fff" opacity=".8"/>`;
    }
    case "Rainbow Swirl": {
      const cols = ["#ff004c", "#ff9a00", "#ffe600", "#29ff4f", "#00c8ff", "#8a2be2"];
      return `<g class="spin">${cols.map((c, i) => {
        const a0 = (i / 6) * Math.PI * 2, a1 = ((i + 1) / 6) * Math.PI * 2;
        return `<path d="M0 0 L ${13 * Math.cos(a0)} ${13 * Math.sin(a0)} A 13 13 0 0 1 ${13 * Math.cos(a1)} ${13 * Math.sin(a1)} Z" fill="${c}"/>`;
      }).join("")}<circle r="3" fill="#fff"/></g>`;
    }
    case "Peppermint Swirl":
      return `${bg("#fff")}<g class="spin">${[0, 1, 2, 3, 4].map((i) => `<path d="M0 0 Q 6 -4 ${13 * Math.cos(i * 1.2566)} ${13 * Math.sin(i * 1.2566)} A 13 13 0 0 1 ${13 * Math.cos(i * 1.2566 + .6)} ${13 * Math.sin(i * 1.2566 + .6)} Z" fill="#e8102a"/>`).join("")}</g>`;
    case "Flames":
      return `${bg()}<path class="flicker" d="M0 11 C -8 10 -9 2 -5 -3 C -4 1 -2 1 -2 -2 C -2 -7 1 -9 3 -12 C 3 -6 9 -4 8 3 C 7 9 4 11 0 11 Z" fill="#ff5a00"/><path class="flicker" d="M0 10 C -4 9 -4 4 -1 0 C 0 3 2 2 2 0 C 4 3 5 6 3 8 C 2 10 1 10 0 10 Z" fill="#ffd400"/>`;
    case "Gold Star":
      return `${bg()}<polygon points="0,-10 2.9,-3.1 10,-3.1 4.3,1.5 6.5,9 0,4.5 -6.5,9 -4.3,1.5 -10,-3.1 -2.9,-3.1" fill="#ffc31a"/>`;
    case "Skull and Crossbones":
      return `${bg()}<path d="M-8 7 L 8 11 M 8 7 L -8 11" stroke="#eee" stroke-width="2" stroke-linecap="round"/><circle cy="-2" r="6.5" fill="#eee"/><rect x="-3.5" y="2" width="7" height="4" rx="1" fill="#eee"/><circle cx="-2.4" cy="-2.5" r="1.7" fill="#050505"/><circle cx="2.4" cy="-2.5" r="1.7" fill="#050505"/>`;
    case "Fireworks":
      return `${bg()}<g class="burst">${Array.from({ length: 12 }, (_, i) => {
        const a = (i / 12) * Math.PI * 2, c = ["#ff2a6a", "#ffd400", "#29c9ff", "#7dff3a"][i % 4];
        return `<path d="M${3 * Math.cos(a)} ${3 * Math.sin(a)} L ${11 * Math.cos(a)} ${11 * Math.sin(a)}" stroke="${c}" stroke-width="1.6" stroke-linecap="round"/>`;
      }).join("")}</g>`;
    case "American Flag":
      return `${Array.from({ length: 7 }, (_, i) => `<rect x="-13" y="${-13 + i * 3.72}" width="26" height="3.72" fill="${i % 2 ? "#fff" : "#d0102a"}"/>`).join("")}<rect x="-13" y="-13" width="12" height="11.2" fill="#1f3a8a"/>`;
    case "Heart":
      return `${bg()}<path class="beat" d="M0 9 C -12 1 -9 -9 -3.5 -8 C -1.5 -7.6 -0.5 -6 0 -4.8 C 0.5 -6 1.5 -7.6 3.5 -8 C 9 -9 12 1 0 9 Z" fill="#ff2a4a"/>`;
    case "Four-Leaf Clover":
      return `${bg()}<g fill="#2fd158"><circle cx="-4" cy="-4" r="4.6"/><circle cx="4" cy="-4" r="4.6"/><circle cx="-4" cy="4" r="4.6"/><circle cx="4" cy="4" r="4.6"/></g><path d="M1 3 Q 5 9 3 12" stroke="#2fd158" stroke-width="1.5" fill="none"/>`;
    case "Snowflake":
      return `${bg("#0a1830")}<g stroke="#dff6ff" stroke-width="1.6" stroke-linecap="round">${[0, 60, 120].map((r) => `<path transform="rotate(${r})" d="M0 -10 V 10 M -3 -7 L 0 -4 L 3 -7 M -3 7 L 0 4 L 3 7"/>`).join("")}</g>`;
    case "Confetti":
      return `${bg()}${Array.from({ length: 14 }, (_, i) => {
        const x = ((i * 37) % 22) - 11, y = ((i * 53) % 22) - 11, c = ["#ff2a6a", "#ffd400", "#29c9ff", "#7dff3a", "#b56bff"][i % 5];
        return `<rect x="${x}" y="${y}" width="3" height="2" rx=".5" fill="${c}" transform="rotate(${i * 29} ${x} ${y})"/>`;
      }).join("")}`;
    default:
      return `${bg("#f6f3ee")}<circle r="7.5" fill="#888"/><circle r="3.2" fill="#050505"/>`;
  }
}

// ---------- updating ----------
const rgbCss = (rgb) => (rgb ? `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})` : null);

function lightFor(look, key) {
  return { ...(look?.lights?.all ?? {}), ...(look?.lights?.[key] ?? {}) };
}

function applyLight(root, part, light, modes) {
  const fxs = root.querySelectorAll(`.${part}-fx`);
  const color = rgbCss(light.rgb);
  const on = color && (light.brightness ?? 255) > 0;
  root.style.setProperty(`--${part === "chest" ? "chest" : "mouth"}`, color ?? "#ffffff");
  const label = (modes.find((m) => m.value === light.mode)?.label ?? "Static").toLowerCase();
  const speed = light.speed ?? 127;
  for (const fx of fxs) {
    fx.style.opacity = on ? Math.max(.25, (light.brightness ?? 255) / 255) : color ? 0 : .08;
    fx.dataset.effect = light.cycle ? "cycle" : label;
    fx.style.setProperty("--fx-dur", `${(2.6 - (speed / 254) * 2.2).toFixed(2)}s`);
  }
}

export function updateSkeleton(root, { look, moves, profile }) {
  if (!root) return;
  const modes = profile?.light_modes ?? [];
  const lightKeys = (profile?.lights ?? []).map((l) => l.key);
  applyLight(root, "chest", lightFor(look, "chest"), modes);
  applyLight(root, "mouth", lightFor(look, lightKeys.includes("head") ? "head" : "chest"), modes);
  root.querySelector(".mouth-fx").style.display = lightKeys.length ? "" : "none";

  const eyes = profile?.eyes ?? [];
  const eyeLabel = eyes.find((e) => e.value === look?.eye)?.label ?? null;
  const art = eyeArt(eyeLabel);
  root.querySelectorAll(".eye-img").forEach((g) => {
    if (g.dataset.label !== String(eyeLabel)) {
      g.innerHTML = art;
      g.dataset.label = String(eyeLabel);
    }
  });

  const all = moves?.has("all");
  for (const part of ["head", "arms", "torso"]) root.classList.toggle(`moving-${part}`, !!(all || moves?.has(part)));
}

