// Bottom right: the Clef core. Every motion here is caused by a real backend event:
//   ingest   -> a particle stream flies in from the edge facing that source's panel
//   decision -> the core flares and ripples in the source's color; items that make it
//               onto the dashboard shoot back out toward their panel
//   stats    -> the gauge arcs (decisions/min, queue) and online/offline power level
// With no events the core only breathes.
import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

const body = document.getElementById("core-body");
const css = getComputedStyle(document.documentElement);
const color = (name) => new THREE.Color(css.getPropertyValue(name).trim());

const C = {
  bg: color("--bg"),
  cyan: color("--cyan"),
  hot: color("--cyan-bright"),
  accent: color("--accent"),
  muted: color("--muted"),
  alert: color("--alert"),
  src: {
    mail: color("--src-mail"),
    news: color("--src-news"),
    jobs: color("--src-jobs"),
    system: color("--src-system"),
  },
};

// Where each source's panel sits relative to the core (bottom right of the grid),
// as a spawn region on the scene's edge in normalized [-1, 1] coordinates.
const EDGES = {
  mail: () => [-1.08, 0.6 + Math.random() * 0.5],                 // top-left
  news: () => [-1.08, -0.7 + Math.random() * 1.0],                 // left
  system: () => [-0.4 + Math.random() * 1.2, 1.1],                 // top (vitals)
  jobs: () => [1.08, -0.8 + Math.random() * 1.6],                  // right
};

const CAM_Z = 10;
const FOV = 38;

// ---------------------------------------------------------------- GLSL

// 3D simplex noise, Ashima Arts / Stefan Gustavson (MIT).
const NOISE = /* glsl */ `
vec3 mod289(vec3 x){return x-floor(x*(1./289.))*289.;}
vec4 mod289(vec4 x){return x-floor(x*(1./289.))*289.;}
vec4 permute(vec4 x){return mod289(((x*34.)+1.)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1./6.,1./3.);const vec4 D=vec4(0.,.5,1.,2.);
  vec3 i=floor(v+dot(v,C.yyy));vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz);vec3 l=1.-g;vec3 i1=min(g.xyz,l.zxy);vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx;vec3 x2=x0-i2+C.yyy;vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.,i1.z,i2.z,1.))+i.y+vec4(0.,i1.y,i2.y,1.))+i.x+vec4(0.,i1.x,i2.x,1.));
  float n_=.142857142857;vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.*floor(p*ns.z*ns.z);vec4 x_=floor(j*ns.z);vec4 y_=floor(j-7.*x_);
  vec4 x=x_*ns.x+ns.yyyy;vec4 y=y_*ns.x+ns.yyyy;vec4 h=1.-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy);vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.+1.;vec4 s1=floor(b1)*2.+1.;vec4 sh=-step(h,vec4(0.));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x);vec3 p1=vec3(a0.zw,h.y);vec3 p2=vec3(a1.xy,h.z);vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
  vec4 m=max(.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.);m=m*m;
  return 42.*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}`;

// Uniforms shared by every material so one update drives the whole scene.
const U = {
  uTime: { value: 0 },
  uBreath: { value: 0 },
  uFlare: { value: 0 },
  uFlareColor: { value: C.hot.clone() },
  uPower: { value: 1 },
  uAbsorb: { value: 0 },
  uPixelRatio: { value: 1 },
};

const coreMaterial = new THREE.ShaderMaterial({
  uniforms: { ...U, uBase: { value: C.accent }, uHot: { value: C.cyan } },
  vertexShader: /* glsl */ `
    uniform float uTime, uFlare, uBreath, uAbsorb;
    varying vec3 vNormal, vView, vPos;
    ${NOISE}
    void main() {
      vec3 p = position;
      float n = snoise(p * 1.6 + vec3(0., uTime * .25, uTime * .15));
      p += normal * (n * (.05 + .12 * uFlare) + uBreath * .025 + uAbsorb * .04);
      vec4 mv = modelViewMatrix * vec4(p, 1.);
      vNormal = normalize(normalMatrix * normal);
      vView = normalize(-mv.xyz);
      vPos = p;
      gl_Position = projectionMatrix * mv;
    }`,
  fragmentShader: /* glsl */ `
    uniform float uTime, uFlare, uBreath, uPower, uAbsorb;
    uniform vec3 uBase, uHot, uFlareColor;
    varying vec3 vNormal, vView, vPos;
    ${NOISE}
    void main() {
      float rim = pow(1. - max(dot(vNormal, vView), 0.), 2.2);
      float n1 = snoise(vPos * 2.2 + vec3(uTime * .35));
      float n2 = snoise(vPos * 4.5 - vec3(0., uTime * .6, 0.));
      float plasma = smoothstep(-.2, 1., n1 * .65 + n2 * .35);
      vec3 col = mix(uBase * .2, uHot, plasma) * (.3 + .2 * uBreath + .25 * uAbsorb);
      col += uHot * rim * .9;
      col = mix(col, uFlareColor * (.6 + plasma), uFlare * .7);
      gl_FragColor = vec4(col * uPower, 1.);
    }`,
});

const glowMaterial = new THREE.ShaderMaterial({
  uniforms: { ...U, uHot: { value: C.cyan } },
  transparent: true,
  depthWrite: false,
  blending: THREE.AdditiveBlending,
  vertexShader: /* glsl */ `
    varying vec2 vUv;
    void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.); }`,
  fragmentShader: /* glsl */ `
    uniform float uBreath, uFlare, uPower, uAbsorb;
    uniform vec3 uHot, uFlareColor;
    varying vec2 vUv;
    void main() {
      float d = length(vUv - .5) * 2.;
      float a = pow(max(1. - d, 0.), 4.) * (.22 + .12 * uBreath + .7 * uFlare + .3 * uAbsorb) * uPower;
      gl_FragColor = vec4(mix(uHot, uFlareColor, uFlare) * a, a);
    }`,
});

const shellMaterial = new THREE.ShaderMaterial({
  uniforms: { ...U, uColor: { value: C.cyan } },
  transparent: true,
  depthWrite: false,
  blending: THREE.AdditiveBlending,
  vertexShader: /* glsl */ `
    uniform float uTime, uFlare, uPixelRatio;
    attribute vec4 aParams;   // theta0, phi, radius, angular speed
    attribute float aSeed;
    varying float vAlpha;
    void main() {
      float th = aParams.x + uTime * aParams.w;
      float ph = aParams.y + .15 * sin(uTime * .3 + aSeed * 6.283);
      float r = aParams.z * (1. + .3 * uFlare * (.5 + aSeed));
      vec3 p = vec3(r * sin(ph) * cos(th), r * cos(ph), r * sin(ph) * sin(th));
      vec4 mv = modelViewMatrix * vec4(p, 1.);
      gl_PointSize = (1.2 + 2.4 * aSeed) * uPixelRatio * (9. / -mv.z);
      vAlpha = .25 + .55 * (.5 + .5 * sin(uTime * (1. + 3. * aSeed) + aSeed * 40.));
      gl_Position = projectionMatrix * mv;
    }`,
  fragmentShader: /* glsl */ `
    uniform vec3 uColor, uFlareColor;
    uniform float uFlare, uPower;
    varying float vAlpha;
    void main() {
      float d = length(gl_PointCoord - .5);
      if (d > .5) discard;
      float a = smoothstep(.5, 0., d) * vAlpha * uPower;
      gl_FragColor = vec4(mix(uColor, uFlareColor, uFlare * .6) * a, a);
    }`,
});

function ringMaterial({ col = C.cyan, opacity = 0.5, segments = 0, duty = 1, fill = -1 } = {}) {
  return new THREE.ShaderMaterial({
    uniforms: {
      uPower: U.uPower,
      uColor: { value: col.clone() },
      uOpacity: { value: opacity },
      uSeg: { value: segments },
      uDuty: { value: duty },
      uFill: { value: fill },
    },
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    blending: THREE.AdditiveBlending,
    vertexShader: /* glsl */ `
      varying vec2 vPos;
      void main() { vPos = position.xy; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.); }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uColor;
      uniform float uOpacity, uSeg, uDuty, uFill, uPower;
      varying vec2 vPos;
      void main() {
        float a = atan(vPos.y, vPos.x) / 6.28318 + .5;
        float on = uSeg > 0. ? step(fract(a * uSeg), uDuty) : 1.;
        float alpha = uOpacity * on;
        if (uFill >= 0.) alpha *= a < uFill ? 1. : .15;
        alpha *= mix(.45, 1., uPower);
        gl_FragColor = vec4(uColor * alpha, alpha);
      }`,
  });
}

const streamMaterial = new THREE.ShaderMaterial({
  uniforms: { uPixelRatio: U.uPixelRatio },
  transparent: true,
  depthWrite: false,
  blending: THREE.AdditiveBlending,
  vertexShader: /* glsl */ `
    uniform float uPixelRatio;
    attribute vec3 aColor;
    attribute float aSize, aAlpha;
    varying vec3 vColor;
    varying float vAlpha;
    void main() {
      vColor = aColor; vAlpha = aAlpha;
      vec4 mv = modelViewMatrix * vec4(position, 1.);
      gl_PointSize = aSize * uPixelRatio * (10. / -mv.z);
      gl_Position = projectionMatrix * mv;
    }`,
  fragmentShader: /* glsl */ `
    varying vec3 vColor;
    varying float vAlpha;
    void main() {
      float d = length(gl_PointCoord - .5);
      if (d > .5) discard;
      float a = smoothstep(.5, .05, d) * vAlpha;
      gl_FragColor = vec4(vColor * a * 1.6, a);
    }`,
});

// ---------------------------------------------------------------- scene

const canvas = document.createElement("canvas");
body.append(canvas);
const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: "low-power" });
renderer.setClearColor(0x000000, 1); // the theme bg gets double sRGB-converted through the composer; black reads the same
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(FOV, 16 / 9, 0.1, 100);
camera.position.z = CAM_Z;

const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.75, 0.2, 0.22);
composer.addPass(bloom);
composer.addPass(new OutputPass());

const core = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 40), coreMaterial);
scene.add(core);

const glow = new THREE.Mesh(new THREE.PlaneGeometry(5, 5), glowMaterial);
glow.position.z = -1.2;
scene.add(glow);

{
  const N = 1600;
  const params = new Float32Array(N * 4);
  const seeds = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    params.set([Math.random() * Math.PI * 2, Math.acos(2 * Math.random() - 1),
      1.2 + Math.pow(Math.random(), 2) * 0.6, (0.08 + Math.random() * 0.25) * (Math.random() < 0.5 ? -1 : 1)], i * 4);
    seeds[i] = Math.random();
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(N * 3), 3));
  g.setAttribute("aParams", new THREE.BufferAttribute(params, 4));
  g.setAttribute("aSeed", new THREE.BufferAttribute(seeds, 1));
  const shell = new THREE.Points(g, shellMaterial);
  shell.frustumCulled = false;
  shell.rotation.z = 0.35;
  scene.add(shell);
}

// HUD rings: [inner, outer, material opts, tilt x, tilt y, spin speed]
const rings = [
  [1.78, 1.8, { opacity: 0.55 }, 0, 0, 0.05],
  [1.86, 1.93, { opacity: 0.4, segments: 120, duty: 0.35 }, 0, 0, -0.08],
  [2.05, 2.1, { opacity: 0.55, segments: 3, duty: 0.27 }, 0, 0, 0.18],
  [2.3, 2.32, { opacity: 0.35, segments: 240, duty: 0.5 }, 1.15, 0.2, 0.12],
  [2.45, 2.47, { opacity: 0.3, segments: 8, duty: 0.7, col: C.accent }, -0.9, 0.5, -0.1],
].map(([r0, r1, opts, tx, ty, speed]) => {
  const m = new THREE.Mesh(new THREE.RingGeometry(r0, r1, 256, 1), ringMaterial(opts));
  m.rotation.set(tx, ty, Math.random() * 6);
  m.userData.speed = speed;
  scene.add(m);
  return m;
});

// Gauges: decisions/min on the left half, queue depth on the right half.
// A half ring spans a in [0.5, 1) in the ring shader, so fill = 0.5 + 0.5 * level.
function gauge(r0, r1, rotation) {
  const m = new THREE.Mesh(new THREE.RingGeometry(r0, r1, 128, 1, 0, Math.PI), ringMaterial({ opacity: 0.85, fill: 0.5 }));
  m.rotation.z = rotation;
  scene.add(m);
  return m;
}
const dpmGauge = gauge(2.68, 2.76, Math.PI / 2);   // sweeps the left half, top to bottom
const queueGauge = gauge(2.68, 2.76, -Math.PI / 2); // sweeps the right half, bottom to top

// ---------------------------------------------------------------- ripples

const ripples = Array.from({ length: 10 }, () => {
  const mat = ringMaterial({ opacity: 0 });
  const m = new THREE.Mesh(new THREE.RingGeometry(0.96, 1, 128, 1), mat);
  m.visible = false;
  m.userData = { t: 1, dur: 1.6, max: 4.5 };
  scene.add(m);
  return m;
});
let rippleIdx = 0;

function ripple(col, { delay = 0, max = 4.5, strength = 1 } = {}) {
  const m = ripples[rippleIdx++ % ripples.length];
  m.material.uniforms.uColor.value.copy(col);
  Object.assign(m.userData, { t: -delay, dur: 1.4 + max * 0.08, max, strength });
}

// ---------------------------------------------------------------- particle streams

const MAXP = 600;  // particles
const TRAIL = 6;   // points per particle (head + trail)
const P = {
  sx: new Float32Array(MAXP), sy: new Float32Array(MAXP), cx: new Float32Array(MAXP), cy: new Float32Array(MAXP),
  ex: new Float32Array(MAXP), ey: new Float32Array(MAXP), z: new Float32Array(MAXP),
  t: new Float32Array(MAXP), dur: new Float32Array(MAXP), size: new Float32Array(MAXP),
  out: new Uint8Array(MAXP), alive: new Uint8Array(MAXP), col: new Float32Array(MAXP * 3),
};
const streamGeo = new THREE.BufferGeometry();
const sPos = new Float32Array(MAXP * TRAIL * 3);
const sCol = new Float32Array(MAXP * TRAIL * 3);
const sSize = new Float32Array(MAXP * TRAIL);
const sAlpha = new Float32Array(MAXP * TRAIL);
streamGeo.setAttribute("position", new THREE.BufferAttribute(sPos, 3).setUsage(THREE.DynamicDrawUsage));
streamGeo.setAttribute("aColor", new THREE.BufferAttribute(sCol, 3).setUsage(THREE.DynamicDrawUsage));
streamGeo.setAttribute("aSize", new THREE.BufferAttribute(sSize, 1).setUsage(THREE.DynamicDrawUsage));
streamGeo.setAttribute("aAlpha", new THREE.BufferAttribute(sAlpha, 1).setUsage(THREE.DynamicDrawUsage));
const streams = new THREE.Points(streamGeo, streamMaterial);
streams.frustumCulled = false;
scene.add(streams);

let half = { w: 6, h: 3.4 };
let nextSlot = 0;
const pending = []; // packets waiting to launch, so a burst of 300 ingests doesn't spawn all at once

function freeSlot() {
  for (let n = 0; n < MAXP; n++) {
    const i = (nextSlot + n) % MAXP;
    if (!P.alive[i]) { nextSlot = i + 1; return i; }
  }
  return -1;
}

function launch({ source, count, outbound, dim }) {
  const col = C.src[source] ?? C.cyan;
  const [nx, ny] = (EDGES[source] ?? EDGES.news)();
  const edge = [nx * half.w, ny * half.h];
  for (let k = 0; k < count; k++) {
    const i = freeSlot();
    if (i < 0) return;
    const jx = edge[0] + (Math.random() - 0.5) * 0.9, jy = edge[1] + (Math.random() - 0.5) * 0.9;
    const ang = Math.atan2(jy, jx) + (Math.random() - 0.5) * 0.6;
    const cxr = Math.cos(ang) * 1.05, cyr = Math.sin(ang) * 1.05;
    const [sx, sy, ex, ey] = outbound ? [cxr, cyr, jx, jy] : [jx, jy, cxr, cyr];
    // Control point off to one side so streams curve in like they're being pulled into orbit.
    const mx = (sx + ex) / 2, my = (sy + ey) / 2;
    const swirl = (Math.random() < 0.5 ? -1 : 1) * (0.8 + Math.random() * 1.4);
    const len = Math.hypot(ex - sx, ey - sy) || 1;
    P.sx[i] = sx; P.sy[i] = sy; P.ex[i] = ex; P.ey[i] = ey;
    P.cx[i] = mx + (-(ey - sy) / len) * swirl; P.cy[i] = my + ((ex - sx) / len) * swirl;
    P.z[i] = (Math.random() - 0.5) * 0.8;
    P.t[i] = -k * 0.04 - Math.random() * 0.05; // stagger within the packet
    P.dur[i] = (outbound ? 1.1 : 1.6) + Math.random() * 0.6;
    P.size[i] = (dim ? 1.6 : 2.6) + Math.random() * 1.5;
    P.out[i] = outbound ? 1 : 0;
    P.alive[i] = 1;
    const c = dim ? col.clone().multiplyScalar(0.45) : col;
    P.col.set([c.r, c.g, c.b], i * 3);
  }
}

function updateStreams(dt) {
  const ease = (t, out) => (out ? 1 - Math.pow(1 - t, 2) : Math.pow(t, 1.7)); // fall into the core, fly out of it
  for (let i = 0; i < MAXP; i++) {
    const base = i * TRAIL;
    if (!P.alive[i]) {
      for (let k = 0; k < TRAIL; k++) sAlpha[base + k] = 0;
      continue;
    }
    P.t[i] += dt / P.dur[i];
    const t = P.t[i];
    if (t >= 1 + TRAIL * 0.035) {
      P.alive[i] = 0;
      if (!P.out[i]) U.uAbsorb.value = Math.min(U.uAbsorb.value + 0.02, 0.35);
      continue;
    }
    for (let k = 0; k < TRAIL; k++) {
      const tk = t - k * 0.035;
      const j = base + k;
      if (tk < 0 || tk > 1) { sAlpha[j] = 0; continue; }
      const e = ease(tk, P.out[i]);
      const a = 1 - e, b = e;
      sPos[j * 3] = a * a * P.sx[i] + 2 * a * b * P.cx[i] + b * b * P.ex[i];
      sPos[j * 3 + 1] = a * a * P.sy[i] + 2 * a * b * P.cy[i] + b * b * P.ey[i];
      sPos[j * 3 + 2] = P.z[i] * (P.out[i] ? e : 1 - e);
      sCol[j * 3] = P.col[i * 3]; sCol[j * 3 + 1] = P.col[i * 3 + 1]; sCol[j * 3 + 2] = P.col[i * 3 + 2];
      const fadeIn = Math.min(tk * 6, 1);
      sAlpha[j] = (1 - k / TRAIL) * fadeIn * (P.out[i] ? 1 - tk * 0.8 : 1);
      sSize[j] = P.size[i] * (1 - k / (TRAIL + 2));
    }
  }
  for (const name of ["position", "aColor", "aSize", "aAlpha"]) streamGeo.attributes[name].needsUpdate = true;
}

// ---------------------------------------------------------------- HUD overlay

const hud = document.createElement("div");
hud.className = "hud";
hud.innerHTML = `
  <div class="hud-corner tl"><div class="hud-title">CLEF-FLASH</div><div id="hud-status">Q4_K_M · RTX 3060</div></div>
  <div class="hud-corner tr"><div><span id="hud-dpm">0</span> <small>DEC/MIN</small></div><div><span id="hud-ms">–</span> <small>MS</small></div><div><span id="hud-q">0</span> <small>QUEUE</small></div></div>
  <div class="hud-corner bl"><div id="hud-path" class="hud-path">AWAITING SIGNAL</div><div id="hud-title" class="hud-sub"></div></div>
  <div class="hud-corner br" id="hud-log"></div>
  <div class="hud-alert" id="hud-alert"></div>`;
body.append(hud);
const $ = (id) => hud.querySelector(id);

// Type the decision path on character by character, like a HUD readout.
let typeTimer;
function typePath(text, col) {
  const el = $("#hud-path");
  clearInterval(typeTimer);
  el.style.color = `#${col.getHexString()}`;
  let n = 0;
  typeTimer = setInterval(() => {
    el.textContent = text.slice(0, ++n) + (n < text.length ? "▌" : "");
    if (n >= text.length) clearInterval(typeTimer);
  }, 18);
}

function logLine(ev, col) {
  const log = $("#hud-log");
  const line = document.createElement("div");
  line.textContent = ev.path;
  line.style.color = `#${col.getHexString()}`;
  log.prepend(line);
  while (log.children.length > 5) log.lastChild.remove();
}

// ---------------------------------------------------------------- events

let dpmTarget = 0, queueTarget = 0, powerTarget = 1;

function onDecision(ev) {
  const shown = ev.show;
  const hot = shown && (ev.flag ?? 0) >= 0.7 && (ev.score ?? 0) >= 2.5;
  const col = shown ? C.src[ev.source] ?? C.cyan : C.accent.clone().lerp(C.muted, 0.3);
  U.uFlareColor.value.copy(col);
  U.uFlare.value = Math.max(U.uFlare.value, hot ? 1 : shown ? 0.8 : 0.3);
  ripple(col, { max: hot ? 5.5 : shown ? 4.2 : 2.6 });
  if (hot) ripple(col, { delay: 0.18, max: 6.5 });
  if (shown) pending.push({ source: ev.source, count: hot ? 14 : 8, outbound: true });

  typePath(ev.path, col);
  $("#hud-title").textContent = `${ev.title.slice(0, 80)} · ${ev.latency_ms}ms`;
  logLine(ev, col);
}

function showAlert(alert) {
  const el = $("#hud-alert");
  el.classList.toggle("on", !!alert);
  if (alert) el.innerHTML = `<b>⚠ ${alert.subsystem.replace("_", " ").toUpperCase()}</b> ${alert.message.replace(/[<>&]/g, "")}`;
}

export function onEvent(ev) {
  if (ev.t === "alert") showAlert(ev.alert);
  if (ev.t === "hello") showAlert(ev.alert);
  if (ev.t === "ingest") {
    pending.push({ source: ev.source, count: ev.dup ? 3 : 7, dim: !!ev.dup });
    if (pending.length > 40) pending.splice(0, pending.length - 40);
  } else if (ev.t === "decision") {
    onDecision(ev);
  } else if (ev.t === "stats" || ev.t === "hello") {
    dpmTarget = Math.min(ev.dpm / 240, 1);
    queueTarget = Math.min(ev.queue / 60, 1);
    powerTarget = ev.clef_up ? 1 : 0.3;
    $("#hud-dpm").textContent = ev.dpm;
    $("#hud-ms").textContent = ev.avg_ms ?? "–";
    $("#hud-q").textContent = ev.queue;
    $("#hud-status").innerHTML = ev.clef_up
      ? "Q4_K_M · RTX 3060 · <b>ONLINE</b>" : `<b class="off">OFFLINE</b> · llama-server down`;
    if (ev.t === "hello" && ev.last) {
      const col = ev.last.show ? C.src[ev.last.source] ?? C.cyan : C.accent;
      typePath(ev.last.path, col);
      $("#hud-title").textContent = ev.last.title.slice(0, 80);
    }
  }
}

export function onDisconnect() {
  powerTarget = 0.3;
  $("#hud-status").innerHTML = `<b class="off">LINK LOST</b> · reconnecting`;
}

if (new URLSearchParams(location.search).has("dev")) window.clefCore = { onEvent, bloom, renderer, scene }; // console testing only

// ---------------------------------------------------------------- loop

function resize() {
  const { width, height } = body.getBoundingClientRect();
  if (!width || !height) return;
  const pr = Math.min(devicePixelRatio, 1.25);
  renderer.setPixelRatio(pr);
  renderer.setSize(width, height, false);
  composer.setPixelRatio(pr);
  composer.setSize(width, height);
  bloom.resolution.set(width / 2, height / 2);
  U.uPixelRatio.value = pr;
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  half.h = Math.tan(THREE.MathUtils.degToRad(FOV / 2)) * CAM_Z;
  half.w = half.h * camera.aspect;
  // Keep the outer gauge inside short panels.
  const s = Math.min(1, (half.h * 0.9) / 2.8);
  scene.scale.setScalar(s);
}
new ResizeObserver(resize).observe(body);
resize();

const clock = new THREE.Clock();
let launchBudget = 0;
// 30 fps while something is happening, 15 fps while the core only breathes.
// Rendering is the kiosk's main CPU/iGPU cost, so idle frames are worth skipping.
const ACTIVE_MS = 1000 / 30;
const IDLE_MS = 1000 / 15;
let lastFrame = 0;

function busy() {
  return U.uFlare.value > 0.02 || U.uAbsorb.value > 0.02 || pending.length > 0 ||
    P.alive.some((a) => a) || ripples.some((m) => m.visible);
}

function frame(now) {
  requestAnimationFrame(frame);
  if (now - lastFrame < (busy() ? ACTIVE_MS : IDLE_MS) - 2) return;
  lastFrame = now;
  const dt = Math.min(clock.getDelta(), 0.1);
  const t = (U.uTime.value += dt);

  U.uBreath.value = 0.5 + 0.5 * Math.sin((t * Math.PI * 2) / 6); // one breath every 6 s
  U.uFlare.value *= Math.exp(-dt / 0.55);
  U.uAbsorb.value *= Math.exp(-dt / 0.8);
  U.uPower.value += (powerTarget - U.uPower.value) * Math.min(dt * 2, 1);
  if (U.uFlare.value < 0.02) U.uFlareColor.value.lerp(C.hot, dt * 2);

  core.rotation.y += dt * 0.08;
  const spin = 1 + U.uFlare.value * 3;
  for (const r of rings) r.rotation.z += r.userData.speed * dt * spin * U.uPower.value;

  // Gauges ease toward the latest stats.
  const dpmU = dpmGauge.material.uniforms.uFill, qU = queueGauge.material.uniforms.uFill;
  dpmU.value += (0.5 + dpmTarget * 0.5 - dpmU.value) * Math.min(dt * 3, 1);
  qU.value += (0.5 + queueTarget * 0.5 - qU.value) * Math.min(dt * 3, 1);
  queueGauge.material.uniforms.uColor.value.copy(queueTarget > 0.8 ? C.alert : C.cyan);

  for (const m of ripples) {
    const d = m.userData;
    if (d.t >= 1) { m.visible = false; continue; }
    d.t += dt / d.dur;
    if (d.t < 0) continue;
    const e = 1 - Math.pow(1 - Math.min(d.t, 1), 3);
    m.visible = true;
    m.scale.setScalar(1 + e * (d.max - 1));
    m.material.uniforms.uOpacity.value = (1 - e) * 0.9 * (d.strength ?? 1);
  }

  // Launch at most ~14 packets per second.
  launchBudget = Math.min(launchBudget + dt * 14, 3);
  while (pending.length && launchBudget >= 1) {
    launch(pending.shift());
    launchBudget -= 1;
  }
  updateStreams(dt);

  composer.render(dt);
}
requestAnimationFrame(frame);
