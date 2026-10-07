// Bottom right: the Clef core, a small 3D scene in space.
//
// Every effect is caused by a real backend event:
//   ingest   -> a particle stream leaves that source's orbiting moon and arcs into the core
//   decision -> the core flares, a shockwave sphere and a disk ripple expand in the source's
//               color; items that make it onto the dashboard stream back out to their moon
//   stats    -> the gauge arcs (decisions/min, queue) and the online/offline power level
// With no events the core only breathes while the camera drifts slowly around it.
import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

const body = document.getElementById("core-body");
const css = getComputedStyle(document.documentElement);
const color = (name) => new THREE.Color(css.getPropertyValue(name).trim());

const C = {
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

const FOV = 45;
const CAM_DIST = 12.5;

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

// A round, soft point sprite.
const SOFT_POINT = /* glsl */ `
  float d = length(gl_PointCoord - .5);
  if (d > .5) discard;
  float soft = smoothstep(.5, 0., d);`;

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

const additive = { transparent: true, depthWrite: false, blending: THREE.AdditiveBlending };

// ---------------------------------------------------------------- materials

// Faint nebula on the inside of a huge sphere: direction-based noise, so no seams.
const nebulaMaterial = new THREE.ShaderMaterial({
  uniforms: { uPower: U.uPower, uTeal: { value: C.accent }, uCyan: { value: C.cyan } },
  side: THREE.BackSide,
  depthWrite: false,
  vertexShader: /* glsl */ `
    varying vec3 vDir;
    void main() { vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.); }`,
  fragmentShader: /* glsl */ `
    uniform float uPower;
    uniform vec3 uTeal, uCyan;
    varying vec3 vDir;
    ${NOISE}
    float fbm(vec3 p) { return snoise(p) * .55 + snoise(p * 2.1) * .3 + snoise(p * 4.3) * .15; }
    void main() {
      float n = fbm(vDir * 1.6);
      float wisps = fbm(vDir * 3.4 + 7.3);
      float cloud = pow(smoothstep(.2, .95, n * .5 + .5), 2.) * (.5 + .5 * wisps);
      vec3 col = uTeal * cloud * .045 + uCyan * pow(max(wisps, 0.), 5.) * .015;
      gl_FragColor = vec4(col * mix(.5, 1., uPower), 1.);
    }`,
});

const starMaterial = new THREE.ShaderMaterial({
  uniforms: { uTime: U.uTime, uPixelRatio: U.uPixelRatio, uPower: U.uPower },
  ...additive,
  vertexShader: /* glsl */ `
    uniform float uTime, uPixelRatio;
    attribute float aSize, aSeed;
    attribute vec3 aColor;
    varying vec3 vColor;
    varying float vAlpha;
    void main() {
      vColor = aColor;
      vAlpha = .55 + .45 * sin(uTime * (.6 + aSeed * 2.2) + aSeed * 60.);
      gl_PointSize = aSize * uPixelRatio;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.);
    }`,
  fragmentShader: /* glsl */ `
    uniform float uPower;
    varying vec3 vColor;
    varying float vAlpha;
    void main() {
      ${SOFT_POINT}
      float a = pow(soft, 2.) * vAlpha * mix(.4, 1., uPower);
      gl_FragColor = vec4(vColor * a, a);
    }`,
});

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

// Soft atmosphere: a slightly larger sphere that only glows at its edge.
const atmosphereMaterial = new THREE.ShaderMaterial({
  uniforms: { ...U, uHot: { value: C.cyan } },
  ...additive,
  vertexShader: /* glsl */ `
    varying vec3 vNormal, vView;
    void main() {
      vec4 mv = modelViewMatrix * vec4(position, 1.);
      vNormal = normalize(normalMatrix * normal);
      vView = normalize(-mv.xyz);
      gl_Position = projectionMatrix * mv;
    }`,
  fragmentShader: /* glsl */ `
    uniform float uBreath, uFlare, uPower, uAbsorb;
    uniform vec3 uHot, uFlareColor;
    varying vec3 vNormal, vView;
    void main() {
      float f = pow(1. - abs(dot(vNormal, vView)), 3.);
      float a = f * (.55 + .2 * uBreath + .9 * uFlare + .3 * uAbsorb) * uPower;
      gl_FragColor = vec4(mix(uHot, uFlareColor, uFlare) * a, a);
    }`,
});

// Accretion disk: thousands of particles on Keplerian orbits (inner ones faster) with spiral arms.
const diskMaterial = new THREE.ShaderMaterial({
  uniforms: { ...U, uInner: { value: C.hot }, uOuter: { value: C.accent } },
  ...additive,
  vertexShader: /* glsl */ `
    uniform float uTime, uFlare, uPixelRatio;
    uniform vec3 uInner, uOuter, uFlareColor;
    attribute float aRadius, aTheta, aHeight, aSeed;
    varying vec3 vColor;
    varying float vAlpha;
    void main() {
      float th = aTheta + uTime * .55 * pow(aRadius, -1.5);
      float arm = .5 + .5 * cos(2. * (th - log(aRadius) * 3.2));
      float r = aRadius * (1. + uFlare * .12 * aSeed);
      vec3 p = vec3(r * cos(th), aHeight * (1. + uFlare), r * sin(th));
      vec4 mv = modelViewMatrix * vec4(p, 1.);
      float t = smoothstep(1.85, 3.35, aRadius);
      vColor = mix(mix(uInner, uOuter, t), uFlareColor, uFlare * .5);
      vAlpha = (.22 + .6 * arm) * (1. - t * .45) * (.6 + .4 * aSeed);
      gl_PointSize = (1.3 + 2.4 * aSeed) * uPixelRatio * (13. / -mv.z);
      gl_Position = projectionMatrix * mv;
    }`,
  fragmentShader: /* glsl */ `
    uniform float uPower, uFlare;
    varying vec3 vColor;
    varying float vAlpha;
    void main() {
      ${SOFT_POINT}
      float a = soft * vAlpha * (1. + uFlare) * uPower;
      gl_FragColor = vec4(vColor * a, a);
    }`,
});

// Segmented emissive ring. Works on RingGeometry (angle from position) and TorusGeometry (angle from uv.x).
function ringMaterial({ col = C.cyan, opacity = 0.5, segments = 0, duty = 1, fill = -1, torus = false } = {}) {
  return new THREE.ShaderMaterial({
    uniforms: {
      uPower: U.uPower,
      uColor: { value: col.clone() },
      uOpacity: { value: opacity },
      uSeg: { value: segments },
      uDuty: { value: duty },
      uFill: { value: fill },
    },
    ...additive,
    side: THREE.DoubleSide,
    vertexShader: /* glsl */ `
      varying float vA;
      void main() {
        vA = ${torus ? "uv.x" : "atan(position.y, position.x) / 6.28318 + .5"};
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.);
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uColor;
      uniform float uOpacity, uSeg, uDuty, uFill, uPower;
      varying float vA;
      void main() {
        float on = uSeg > 0. ? step(fract(vA * uSeg), uDuty) : 1.;
        float alpha = uOpacity * on;
        if (uFill >= 0.) alpha *= vA < uFill ? 1. : .15;
        alpha *= mix(.45, 1., uPower);
        gl_FragColor = vec4(uColor * alpha, alpha);
      }`,
  });
}

// Expanding shockwave shell: bright at its edge, see-through in the middle.
function shellMaterial() {
  return new THREE.ShaderMaterial({
    uniforms: { uColor: { value: C.cyan.clone() }, uOpacity: { value: 0 } },
    ...additive,
    vertexShader: /* glsl */ `
      varying vec3 vNormal, vView;
      void main() {
        vec4 mv = modelViewMatrix * vec4(position, 1.);
        vNormal = normalize(normalMatrix * normal);
        vView = normalize(-mv.xyz);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uColor;
      uniform float uOpacity;
      varying vec3 vNormal, vView;
      void main() {
        float f = pow(1. - abs(dot(vNormal, vView)), 2.5);
        gl_FragColor = vec4(uColor * f * uOpacity, f * uOpacity);
      }`,
  });
}

const pointMaterial = (sizeScale) => new THREE.ShaderMaterial({
  uniforms: { uPixelRatio: U.uPixelRatio, uPower: U.uPower },
  ...additive,
  vertexShader: /* glsl */ `
    uniform float uPixelRatio;
    attribute vec3 aColor;
    attribute float aSize, aAlpha;
    varying vec3 vColor;
    varying float vAlpha;
    void main() {
      vColor = aColor; vAlpha = aAlpha;
      vec4 mv = modelViewMatrix * vec4(position, 1.);
      gl_PointSize = aSize * uPixelRatio * (${sizeScale.toFixed(1)} / -mv.z);
      gl_Position = projectionMatrix * mv;
    }`,
  fragmentShader: /* glsl */ `
    uniform float uPower;
    varying vec3 vColor;
    varying float vAlpha;
    void main() {
      ${SOFT_POINT}
      float a = soft * vAlpha * mix(.4, 1., uPower);
      gl_FragColor = vec4(vColor * a * 1.6, a);
    }`,
});

// ---------------------------------------------------------------- scene

const canvas = document.createElement("canvas");
body.append(canvas);
const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: "low-power" });
renderer.setClearColor(0x000000, 1); // the theme bg gets double sRGB-converted through the composer; black reads the same
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(FOV, 16 / 9, 0.1, 400);

const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.75, 0.3, 0.22);
composer.addPass(bloom);
composer.addPass(new OutputPass());

scene.add(new THREE.Mesh(new THREE.SphereGeometry(200, 48, 24), nebulaMaterial));

function pointCloud(n, place, material, extra = {}) {
  const g = new THREE.BufferGeometry();
  const pos = new Float32Array(n * 3);
  const attrs = Object.fromEntries(Object.entries(extra).map(([k, size]) => [k, new Float32Array(n * size)]));
  for (let i = 0; i < n; i++) place(i, pos, attrs);
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  for (const [k, size] of Object.entries(extra)) g.setAttribute(k, new THREE.BufferAttribute(attrs[k], size));
  const pts = new THREE.Points(g, material);
  pts.frustumCulled = false;
  return pts;
}

function randomDirection() {
  const u = Math.random() * 2 - 1, th = Math.random() * Math.PI * 2, s = Math.sqrt(1 - u * u);
  return [s * Math.cos(th), u, s * Math.sin(th)];
}

// Distant stars: mostly faint cyan-white, a few bright, a few warm.
const starWarm = new THREE.Color("#ffd9a8"), starCool = new THREE.Color("#cfefff");
scene.add(pointCloud(2600, (i, pos, a) => {
  const [x, y, z] = randomDirection(), r = 60 + Math.random() * 100;
  pos.set([x * r, y * r, z * r], i * 3);
  const bright = Math.random() < 0.04;
  a.aSize[i] = bright ? 3 + Math.random() * 2.5 : 0.8 + Math.random() * 1.6;
  a.aSeed[i] = Math.random();
  const c = (Math.random() < 0.12 ? starWarm : starCool).clone().lerp(C.cyan, Math.random() * 0.35)
    .multiplyScalar(bright ? 1.8 : 0.7 + Math.random() * 0.5);
  a.aColor.set([c.r, c.g, c.b], i * 3);
}, starMaterial, { aSize: 1, aSeed: 1, aColor: 3 }));

// Nearby dust drifting with the camera for parallax.
const dust = pointCloud(350, (i, pos, a) => {
  const [x, y, z] = randomDirection(), r = 5 + Math.random() * 14;
  pos.set([x * r, y * r, z * r], i * 3);
  a.aSize[i] = 0.6 + Math.random() * 0.9;
  a.aSeed[i] = Math.random();
  const c = C.accent.clone().multiplyScalar(0.35 + Math.random() * 0.3);
  a.aColor.set([c.r, c.g, c.b], i * 3);
}, starMaterial, { aSize: 1, aSeed: 1, aColor: 3 });
scene.add(dust);

// The core system lives in its own group so the whole thing can be scaled to fit the panel.
const system = new THREE.Group();
scene.add(system);

const core = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 40), coreMaterial);
system.add(core);
system.add(new THREE.Mesh(new THREE.IcosahedronGeometry(1.32, 12), atmosphereMaterial));

const diskGroup = new THREE.Group();
diskGroup.rotation.set(0.32, 0, 0.12);
system.add(diskGroup);
diskGroup.add(pointCloud(7000, (i, pos, a) => {
  a.aRadius[i] = 1.85 + Math.pow(Math.random(), 1.4) * 1.5; // dark gap between core and disk
  a.aTheta[i] = Math.random() * Math.PI * 2;
  a.aHeight[i] = (Math.random() - 0.5) * 0.06 * a.aRadius[i];
  a.aSeed[i] = Math.random();
}, diskMaterial, { aRadius: 1, aTheta: 1, aHeight: 1, aSeed: 1 }));

// Gyroscope rings: real 3D tori on different axes.
const gyros = [
  [1.48, { opacity: 0.5, segments: 64, duty: 0.55 }, [1.25, 0.2, 0], [0, 0.22, 0]],
  [1.7, { opacity: 0.35, segments: 3, duty: 0.3 }, [-0.6, 0.9, 0.3], [0.15, 0, -0.12]],
].map(([radius, opts, rot, spin]) => {
  const m = new THREE.Mesh(new THREE.TorusGeometry(radius, 0.01, 6, 256), ringMaterial({ ...opts, torus: true }));
  m.rotation.set(...rot);
  m.userData.spin = spin;
  system.add(m);
  return m;
});

// HUD rings + gauges always face the camera, like an overlay pinned to the core.
const hudGroup = new THREE.Group();
system.add(hudGroup);
const hudRings = [
  [3.3, 3.33, { opacity: 0.4, segments: 180, duty: 0.45 }, 0.05],
  [3.45, 3.5, { opacity: 0.45, segments: 3, duty: 0.25 }, -0.12],
].map(([r0, r1, opts, speed]) => {
  const m = new THREE.Mesh(new THREE.RingGeometry(r0, r1, 256, 1), ringMaterial(opts));
  m.rotation.z = Math.random() * 6;
  m.userData.speed = speed;
  hudGroup.add(m);
  return m;
});
// A half ring spans a in [0.5, 1) in the ring shader, so fill = 0.5 + 0.5 * level.
function gauge(rotation) {
  const m = new THREE.Mesh(new THREE.RingGeometry(3.62, 3.7, 128, 1, 0, Math.PI), ringMaterial({ opacity: 0.85, fill: 0.5 }));
  m.rotation.z = rotation;
  hudGroup.add(m);
  return m;
}
const dpmGauge = gauge(Math.PI / 2);    // left half, sweeping top to bottom
const queueGauge = gauge(-Math.PI / 2); // right half, sweeping bottom to top

// ---------------------------------------------------------------- source moons

// One moon per source on its own tilted orbit, like an orrery. Streams leave from the moon.
const MOONS = {
  mail:   { radius: 4.3, incline: 0.55, node: 0.4, speed: 0.07, phase: 0.0 },
  news:   { radius: 5.0, incline: -0.35, node: 1.9, speed: 0.05, phase: 2.2 },
  jobs:   { radius: 4.65, incline: 0.95, node: -1.1, speed: 0.06, phase: 4.1 },
  system: { radius: 3.95, incline: -0.85, node: 2.8, speed: 0.08, phase: 1.1 },
};
const moonNames = Object.keys(MOONS);
const moonPositions = {};
for (const [name, m] of Object.entries(MOONS)) {
  m.basis = new THREE.Matrix4().makeRotationY(m.node).multiply(new THREE.Matrix4().makeRotationX(m.incline));
  m.activity = 0;
  moonPositions[name] = new THREE.Vector3();
  const pts = Array.from({ length: 160 }, (_, i) => {
    const a = (i / 160) * Math.PI * 2;
    return new THREE.Vector3(Math.cos(a) * m.radius, 0, Math.sin(a) * m.radius).applyMatrix4(m.basis);
  });
  const line = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({ color: C.src[name], transparent: true, opacity: 0.09, depthWrite: false,
      blending: THREE.AdditiveBlending }));
  system.add(line);
}

function moonAt(name, t, out) {
  const m = MOONS[name], a = m.phase + t * m.speed;
  return out.set(Math.cos(a) * m.radius, 0, Math.sin(a) * m.radius).applyMatrix4(m.basis);
}

const moonGeo = new THREE.BufferGeometry();
const moonPos = new Float32Array(moonNames.length * 3);
const moonCol = new Float32Array(moonNames.length * 3);
const moonSize = new Float32Array(moonNames.length);
const moonAlpha = new Float32Array(moonNames.length).fill(1);
moonNames.forEach((n, i) => moonCol.set([C.src[n].r, C.src[n].g, C.src[n].b], i * 3));
moonGeo.setAttribute("position", new THREE.BufferAttribute(moonPos, 3));
moonGeo.setAttribute("aColor", new THREE.BufferAttribute(moonCol, 3));
moonGeo.setAttribute("aSize", new THREE.BufferAttribute(moonSize, 1));
moonGeo.setAttribute("aAlpha", new THREE.BufferAttribute(moonAlpha, 1));
const moons = new THREE.Points(moonGeo, pointMaterial(14));
moons.frustumCulled = false;
system.add(moons);

// ---------------------------------------------------------------- shockwaves

const shells = Array.from({ length: 6 }, () => {
  const m = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 4), shellMaterial());
  m.visible = false;
  m.userData = { t: 1 };
  system.add(m);
  return m;
});
const diskRipples = Array.from({ length: 6 }, () => {
  const m = new THREE.Mesh(new THREE.RingGeometry(0.97, 1, 160, 1), ringMaterial({ opacity: 0 }));
  m.rotation.x = -Math.PI / 2; // lie in the disk plane
  m.visible = false;
  m.userData = { t: 1 };
  diskGroup.add(m);
  return m;
});
let waveIdx = 0;

function shockwave(col, { delay = 0, max = 5, strength = 1 } = {}) {
  const i = waveIdx++ % shells.length;
  for (const [m, size] of [[shells[i], max], [diskRipples[i], max * 1.1]]) {
    m.material.uniforms.uColor.value.copy(col);
    Object.assign(m.userData, { t: -delay, dur: 1.3 + max * 0.12, max: size, strength });
  }
}

function updateWaves(dt) {
  for (const group of [shells, diskRipples]) {
    for (const m of group) {
      const d = m.userData;
      if (d.t >= 1) { m.visible = false; continue; }
      d.t += dt / d.dur;
      if (d.t < 0) continue;
      const e = 1 - Math.pow(1 - Math.min(d.t, 1), 3);
      m.visible = true;
      m.scale.setScalar(1.05 + e * (d.max - 1.05));
      m.material.uniforms.uOpacity.value = (1 - e) * (group === shells ? 0.9 : 0.8) * d.strength;
    }
  }
}

// ---------------------------------------------------------------- particle streams

const MAXP = 600;  // particles
const TRAIL = 6;   // points per particle (head + trail)
const P = {
  s: new Float32Array(MAXP * 3), c: new Float32Array(MAXP * 3), e: new Float32Array(MAXP * 3),
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
const streams = new THREE.Points(streamGeo, pointMaterial(10));
streams.frustumCulled = false;
system.add(streams);

let nextSlot = 0;
const pending = []; // packets waiting to launch, so a burst of 300 ingests doesn't spawn all at once
const tmpA = new THREE.Vector3(), tmpB = new THREE.Vector3(), tmpC = new THREE.Vector3();

function freeSlot() {
  for (let n = 0; n < MAXP; n++) {
    const i = (nextSlot + n) % MAXP;
    if (!P.alive[i]) { nextSlot = i + 1; return i; }
  }
  return -1;
}

function launch({ source, count, outbound, dim }) {
  const name = MOONS[source] ? source : "news";
  const col = C.src[name] ?? C.cyan;
  const moon = moonPositions[name];
  MOONS[name].activity = Math.min(MOONS[name].activity + (outbound ? 0.6 : 1), 1.5);
  for (let k = 0; k < count; k++) {
    const i = freeSlot();
    if (i < 0) return;
    const start = tmpA.copy(moon).add(tmpC.set(Math.random() - 0.5, Math.random() - 0.5, Math.random() - 0.5).multiplyScalar(0.35));
    const end = tmpB.copy(start).normalize().multiplyScalar(1.05)
      .add(tmpC.set(Math.random() - 0.5, Math.random() - 0.5, Math.random() - 0.5).multiplyScalar(0.25));
    const [s, e] = outbound ? [end, start] : [start, end];
    // Control point lifted off the straight line so streams arc through space.
    const mid = s.clone().add(e).multiplyScalar(0.5);
    const lift = new THREE.Vector3().crossVectors(e.clone().sub(s), new THREE.Vector3(0, 1, 0)).normalize()
      .multiplyScalar((Math.random() < 0.5 ? -1 : 1) * (0.6 + Math.random() * 1.2))
      .add(new THREE.Vector3(0, (Math.random() - 0.3) * 1.2, 0));
    P.s.set([s.x, s.y, s.z], i * 3);
    P.e.set([e.x, e.y, e.z], i * 3);
    P.c.set([mid.x + lift.x, mid.y + lift.y, mid.z + lift.z], i * 3);
    P.t[i] = -k * 0.04 - Math.random() * 0.05;
    P.dur[i] = (outbound ? 1.2 : 1.7) + Math.random() * 0.6;
    P.size[i] = (dim ? 1.6 : 2.6) + Math.random() * 1.5;
    P.out[i] = outbound ? 1 : 0;
    P.alive[i] = 1;
    const c = dim ? col.clone().multiplyScalar(0.45) : col;
    P.col.set([c.r, c.g, c.b], i * 3);
  }
}

function updateStreams(dt) {
  const ease = (t, out) => (out ? 1 - Math.pow(1 - t, 2) : Math.pow(t, 1.7)); // fall into the core, fly out of it
  let any = false;
  for (let i = 0; i < MAXP; i++) {
    const base = i * TRAIL;
    if (!P.alive[i]) {
      if (sAlpha[base] !== 0) for (let k = 0; k < TRAIL; k++) sAlpha[base + k] = 0;
      continue;
    }
    any = true;
    P.t[i] += dt / P.dur[i];
    const t = P.t[i];
    if (t >= 1 + TRAIL * 0.035) {
      P.alive[i] = 0;
      if (!P.out[i]) U.uAbsorb.value = Math.min(U.uAbsorb.value + 0.02, 0.35);
      continue;
    }
    for (let k = 0; k < TRAIL; k++) {
      const tk = t - k * 0.035, j = base + k;
      if (tk < 0 || tk > 1) { sAlpha[j] = 0; continue; }
      const e = ease(tk, P.out[i]), a = 1 - e, b = e;
      for (let d = 0; d < 3; d++) {
        sPos[j * 3 + d] = a * a * P.s[i * 3 + d] + 2 * a * b * P.c[i * 3 + d] + b * b * P.e[i * 3 + d];
        sCol[j * 3 + d] = P.col[i * 3 + d];
      }
      sAlpha[j] = (1 - k / TRAIL) * Math.min(tk * 6, 1) * (P.out[i] ? 1 - tk * 0.8 : 1);
      sSize[j] = P.size[i] * (1 - k / (TRAIL + 2));
    }
  }
  for (const name of ["position", "aColor", "aSize", "aAlpha"]) streamGeo.attributes[name].needsUpdate = true;
  return any;
}

// ---------------------------------------------------------------- HUD overlay

const hud = document.createElement("div");
hud.className = "hud";
hud.innerHTML = `
  <div class="hud-corner tl"><div class="hud-title">CLEF-FLASH</div><div id="hud-status">Q4_K_M · RTX 3060</div></div>
  <div class="hud-corner tr"><div><span id="hud-dpm">0</span> <small>DEC/MIN</small></div><div><span id="hud-ms">–</span> <small>MS</small></div><div><span id="hud-q">0</span> <small>QUEUE</small></div></div>
  <div class="hud-corner bl"><div id="hud-path" class="hud-path">AWAITING SIGNAL</div><div id="hud-title" class="hud-sub"></div></div>
  <div class="hud-corner br" id="hud-log"></div>
  <div class="hud-alert" id="hud-alert"></div>
  ${moonNames.map((n) => `<div class="moon-label" id="moon-${n}" style="color:var(--src-${n})">${n === "system" ? "SYS" : n.toUpperCase()}</div>`).join("")}`;
body.append(hud);
const $ = (id) => hud.querySelector(id);
const moonLabels = Object.fromEntries(moonNames.map((n) => [n, $(`#moon-${n}`)]));

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
  shockwave(col, { max: hot ? 6 : shown ? 4.8 : 2.8, strength: shown ? 1 : 0.5 });
  if (hot) shockwave(col, { delay: 0.2, max: 7.5 });
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
  if (ev.t === "alert" || ev.t === "hello") showAlert(ev.alert);
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
      typePath(ev.last.path, ev.last.show ? C.src[ev.last.source] ?? C.cyan : C.accent);
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

let size = { w: 1, h: 1 };

function resize() {
  const { width, height } = body.getBoundingClientRect();
  if (!width || !height) return;
  size = { w: width, h: height };
  const pr = Math.min(devicePixelRatio, 1.25);
  renderer.setPixelRatio(pr);
  renderer.setSize(width, height, false);
  composer.setPixelRatio(pr);
  composer.setSize(width, height);
  bloom.resolution.set(width / 2, height / 2);
  U.uPixelRatio.value = pr;
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  // Keep the outermost orbit inside short panels.
  const halfH = Math.tan(THREE.MathUtils.degToRad(FOV / 2)) * CAM_DIST;
  system.scale.setScalar(Math.min(1, (halfH * 0.95) / 5.2));
}
new ResizeObserver(resize).observe(body);
resize();

const clock = new THREE.Clock();
let launchBudget = 0;
// 30 fps while something is happening, 20 fps while the core breathes and the camera drifts.
const ACTIVE_MS = 1000 / 30;
const IDLE_MS = 1000 / 20;
let lastFrame = 0;
let streamsAlive = false;
const proj = new THREE.Vector3();

function busy() {
  return U.uFlare.value > 0.02 || U.uAbsorb.value > 0.02 || pending.length > 0 || streamsAlive ||
    shells.some((m) => m.visible);
}

function frame(now) {
  requestAnimationFrame(frame);
  if (now - lastFrame < (busy() ? ACTIVE_MS : IDLE_MS) - 2) return;
  lastFrame = now;
  tick(Math.min(clock.getDelta(), 0.1));
}

function tick(dt) {
  const t = (U.uTime.value += dt);

  U.uBreath.value = 0.5 + 0.5 * Math.sin((t * Math.PI * 2) / 6); // one breath every 6 s
  U.uFlare.value *= Math.exp(-dt / 0.55);
  U.uAbsorb.value *= Math.exp(-dt / 0.8);
  U.uPower.value += (powerTarget - U.uPower.value) * Math.min(dt * 2, 1);
  if (U.uFlare.value < 0.02) U.uFlareColor.value.lerp(C.hot, dt * 2);

  // Camera drifts around the core: a full lap every ~3.5 minutes, gently bobbing in height.
  const az = t * 0.03, el = 0.28 + 0.1 * Math.sin(t * 0.045);
  camera.position.set(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el)).multiplyScalar(CAM_DIST);
  camera.lookAt(0, 0, 0);
  dust.rotation.y = t * 0.004;

  const spin = (1 + U.uFlare.value * 3) * U.uPower.value;
  core.rotation.y += dt * 0.08;
  for (const g of gyros) {
    g.rotation.x += g.userData.spin[0] * dt * spin;
    g.rotation.y += g.userData.spin[1] * dt * spin;
    g.rotation.z += g.userData.spin[2] * dt * spin;
  }
  hudGroup.quaternion.copy(camera.quaternion);
  for (const r of hudRings) r.rotation.z += r.userData.speed * dt * spin;

  // Gauges ease toward the latest stats.
  const dpmU = dpmGauge.material.uniforms.uFill, qU = queueGauge.material.uniforms.uFill;
  dpmU.value += (0.5 + dpmTarget * 0.5 - dpmU.value) * Math.min(dt * 3, 1);
  qU.value += (0.5 + queueTarget * 0.5 - qU.value) * Math.min(dt * 3, 1);
  queueGauge.material.uniforms.uColor.value.copy(queueTarget > 0.8 ? C.alert : C.cyan);

  // Moons orbit; they swell briefly when their source sends data. Labels follow them on screen.
  moonNames.forEach((name, i) => {
    const m = MOONS[name];
    const p = moonAt(name, t, moonPositions[name]);
    moonPos.set([p.x, p.y, p.z], i * 3);
    m.activity *= Math.exp(-dt / 0.9);
    moonSize[i] = (10 + m.activity * 8) * (0.85 + 0.15 * U.uBreath.value);
    proj.copy(p).multiplyScalar(system.scale.x).project(camera);
    const label = moonLabels[name];
    label.style.transform = `translate(${((proj.x + 1) / 2) * size.w + 9}px, ${((1 - proj.y) / 2) * size.h - 6}px)`;
    label.style.opacity = proj.z < 1 ? (0.45 + Math.min(m.activity, 1) * 0.55).toFixed(2) : "0";
  });
  moonGeo.attributes.position.needsUpdate = true;
  moonGeo.attributes.aSize.needsUpdate = true;

  updateWaves(dt);

  // Launch at most ~14 packets per second.
  launchBudget = Math.min(launchBudget + dt * 14, 3);
  while (pending.length && launchBudget >= 1) {
    launch(pending.shift());
    launchBudget -= 1;
  }
  streamsAlive = updateStreams(dt);

  composer.render(dt);
}

// ?capture: no real-time loop; each step() advances a fixed dt, so recordings are smooth
// however slowly the browser renders (used to make docs/core.gif).
if (new URLSearchParams(location.search).has("capture")) {
  window.clefCore = { onEvent, step: tick };
} else {
  requestAnimationFrame(frame);
}
