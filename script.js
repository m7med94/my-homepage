/**
 * =========================================================================
 * SensorsHub — Cortesa Cosmic Intelligence & Quantum Point Cloud Engine
 * 3D WebGL Humanoid, Topological Signal Graph, Web Audio Synthesizer,
 * Real-Time ESP32 Telemetry & Synced Task Terminal.
 * =========================================================================
 */

// =========================================================================
// 1. THREE.JS 3D TRANSLUCENT HUMANOID & COSMIC PARTICLE CLOUD
// =========================================================================
let scene, camera, renderer, bustGroup, bustGeometry, bustMesh;
let positions, originPositions, colors;
let pIndex = 0;
const MAX_POINTS = 52000;
let lightTrails = [];
let dustCloud;
let targetRotX = 0, targetRotY = 0;
let pulseFactor = 0;

function initThreeBackground() {
  const canvas3D = document.getElementById('webglCanvas');
  if (!canvas3D || typeof THREE === 'undefined') return;

  scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x02040a, 0.018);

  camera = new THREE.PerspectiveCamera(42, window.innerWidth / window.innerHeight, 0.1, 1000);
  camera.position.set(0, 0, 26);

  renderer = new THREE.WebGLRenderer({ canvas: canvas3D, antialias: true, alpha: true });
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

  bustGroup = new THREE.Group();
  scene.add(bustGroup);

  // Custom glow particle texture
  const pCanvas = document.createElement('canvas');
  pCanvas.width = 64;
  pCanvas.height = 64;
  const pCtx = pCanvas.getContext('2d');
  const grad = pCtx.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, 'rgba(255,255,255,1)');
  grad.addColorStop(0.18, 'rgba(121,221,255,0.95)');
  grad.addColorStop(0.55, 'rgba(0,180,255,0.3)');
  grad.addColorStop(1, 'rgba(0,0,0,0)');
  pCtx.fillStyle = grad;
  pCtx.fillRect(0, 0, 64, 64);
  const particleTexture = new THREE.CanvasTexture(pCanvas);

  // Anatomical Signed Distance Field (SDF)
  function smin(a, b, k = 0.5) {
    const h = Math.max(k - Math.abs(a - b), 0.0) / k;
    return Math.min(a, b) - h * h * k * 0.25;
  }

  function humanoidSDF(x, y, z) {
    const symX = Math.abs(x);
    const dCranium = (x * x / 14.8 + Math.pow(y - 3.8, 2) / 19.0 + Math.pow(z + 0.3, 2) / 15.6) - 1.0;
    const dForehead = (symX * symX / 11.8 + Math.pow(y - 4.2, 2) / 4.2 + Math.pow(z - 1.6, 2) / 4.6) - 0.7;

    const jawT = Math.max(0.0, Math.min(1.0, (y - 0.8) / 2.6));
    const jawWidth = 1.1 + jawT * 2.3;
    const jawDepth = 2.4 + jawT * 1.3;
    const dJaw = (symX * symX / (jawWidth * jawWidth) + Math.pow(y - 1.8, 2) / 3.8 + Math.pow(z - 0.4, 2) / (jawDepth * jawDepth)) - 1.0;

    const dCheek = (Math.pow(symX - 1.95, 2) / 1.6 + Math.pow(y - 2.7, 2) / 1.4 + Math.pow(z - 1.65, 2) / 1.6) - 0.65;
    const dBrow = (Math.pow(symX - 1.25, 2) / 2.0 + Math.pow(y - 3.85, 2) / 0.4 + Math.pow(z - 2.55, 2) / 0.6) - 0.45;

    let head = smin(dCranium, dForehead, 0.4);
    head = smin(head, dJaw, 0.35);
    head = smin(head, dCheek, 0.3);
    head = smin(head, dBrow, 0.2);

    const dEyeSocket = Math.sqrt(Math.pow(symX - 1.35, 2) + Math.pow(y - 3.4, 2) + Math.pow(z - 2.6, 2)) - 0.75;
    head = Math.max(head, -dEyeSocket * 1.15);

    const dEyeBall = Math.sqrt(Math.pow(symX - 1.35, 2) + Math.pow(y - 3.4, 2) + Math.pow(z - 2.3, 2)) - 0.45;
    head = smin(head, dEyeBall, 0.15);

    if (y > 1.8 && y < 3.8 && z > 1.2) {
      const noseY = (y - 1.8) / 1.9;
      const nWidth = 0.32 + (1.0 - noseY) * 0.32;
      const nProtrusion = 3.35 - noseY * 0.75;
      const dNose = (x * x / (nWidth * nWidth) + Math.pow(y - 2.55, 2) / 1.5 + Math.pow(z - nProtrusion, 2) / 0.7) - 0.32;
      head = smin(head, dNose, 0.16);
    }

    if (y > 1.3 && y < 2.2 && z > 1.6) {
      const dUpperLip = (x * x / 1.35 + Math.pow(y - 1.85, 2) / 0.12 + Math.pow(z - 2.78, 2) / 0.22) - 0.22;
      const dLowerLip = (x * x / 1.25 + Math.pow(y - 1.55, 2) / 0.15 + Math.pow(z - 2.72, 2) / 0.25) - 0.22;
      head = smin(head, dUpperLip, 0.09);
      head = smin(head, dLowerLip, 0.09);
    }

    const neckY = Math.max(-1.8, Math.min(1.2, y));
    const neckTaper = 1.65 + (1.2 - neckY) * 0.4;
    const dNeck = (symX * symX / (neckTaper * neckTaper) + Math.pow(z + 0.1, 2) / (neckTaper * 0.85 * neckTaper * 0.85)) - 1.0;
    const sMuscleX = 1.9 - (y + 1.8) * 0.45;
    const dMuscle = (Math.pow(symX - sMuscleX, 2) / 0.45 + Math.pow(z - 0.2, 2) / 0.55) - 0.35;
    let upperBody = smin(dNeck, dMuscle, 0.3);

    if (y < 0.2) {
      const torsoT = (-0.2 - y) / 6.0;
      const shoulderSpan = 2.1 + Math.pow(torsoT, 0.65) * 11.0;
      const chestDepth = 1.5 + torsoT * 4.0;
      const dClavicle = (Math.pow(symX - 4.2, 2) / 18.0 + Math.pow(y + 0.9, 2) / 0.3 + Math.pow(z - 1.9, 2) / 0.45) - 0.32;
      const dTorso = (x * x / (shoulderSpan * shoulderSpan) + Math.pow(y + 3.2, 2) / 14.0 + Math.pow(z + 0.2, 2) / (chestDepth * chestDepth)) - 1.0;
      upperBody = smin(upperBody, dTorso, 0.55);
      upperBody = smin(upperBody, dClavicle, 0.22);
    }

    return smin(head, upperBody, 0.45);
  }

  function calcNormal(x, y, z) {
    const eps = 0.02;
    const nx = humanoidSDF(x + eps, y, z) - humanoidSDF(x - eps, y, z);
    const ny = humanoidSDF(x, y + eps, z) - humanoidSDF(x, y - eps, z);
    const nz = humanoidSDF(x, y, z + eps) - humanoidSDF(x, y - eps, z);
    const len = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1.0;
    return [nx / len, ny / len, nz / len];
  }

  positions = new Float32Array(MAX_POINTS * 3);
  originPositions = new Float32Array(MAX_POINTS * 3);
  colors = new Float32Array(MAX_POINTS * 3);

  const cyanBright = new THREE.Color(0x00f0ff);
  const cyanPale = new THREE.Color(0x79ddff);
  const pureWhite = new THREE.Color(0xffffff);
  const deepCobalt = new THREE.Color(0x1a4fb5);

  function addPoint(x, y, z, intensity = 0.5, isPureEdge = false) {
    if (pIndex >= MAX_POINTS) return;
    const i3 = pIndex * 3;
    positions[i3] = x;
    positions[i3 + 1] = y;
    positions[i3 + 2] = z;

    originPositions[i3] = x;
    originPositions[i3 + 1] = y;
    originPositions[i3 + 2] = z;

    const col = new THREE.Color();
    if (isPureEdge || intensity > 0.85) {
      col.lerpColors(cyanBright, pureWhite, (intensity - 0.85) * 6.6);
    } else if (intensity > 0.4) {
      col.lerpColors(cyanPale, cyanBright, (intensity - 0.4) * 2.2);
    } else {
      col.lerpColors(deepCobalt, cyanPale, intensity * 2.5);
    }

    colors[i3] = col.r;
    colors[i3 + 1] = col.g;
    colors[i3 + 2] = col.b;

    pIndex++;
  }

  // 1. Radial cranial strands
  const NUM_MERIDIANS = 54;
  const STEPS_PER_MERIDIAN = 120;
  const apex = { x: 0, y: 7.7, z: -0.3 };

  for (let m = 0; m < NUM_MERIDIANS; m++) {
    const phi = (m / NUM_MERIDIANS) * Math.PI * 2;
    const dirX = Math.cos(phi);
    const dirZ = Math.sin(phi);

    for (let s = 1; s <= STEPS_PER_MERIDIAN; s++) {
      const t = s / STEPS_PER_MERIDIAN;
      const y = apex.y - t * 4.2;

      let dist = 0.1;
      for (let step = 0; step < 40; step++) {
        dist += 0.14;
        if (humanoidSDF(dirX * dist, y, dirZ * dist) >= 0.0) break;
      }

      const px = dirX * dist;
      const pz = dirZ * dist;
      const crownGlow = Math.pow(1.0 - t, 1.4) * 0.95 + 0.15;
      const norm = calcNormal(px, y, pz);
      const rim = Math.pow(1.0 - Math.abs(norm[2]), 2.0);

      addPoint(px, y, pz, Math.max(crownGlow, rim), true);
    }
  }

  // 2. Translucent humanoid body
  const NUM_LATITUDES = 170;
  for (let lat = 0; lat < NUM_LATITUDES; lat++) {
    const y = 7.5 - (lat / NUM_LATITUDES) * 14.0;
    const numRays = y > 0 ? 190 : 230;

    for (let r = 0; r < numRays; r++) {
      const theta = (r / numRays) * Math.PI * 2;
      const dirX = Math.cos(theta);
      const dirZ = Math.sin(theta);

      let dist = 0.1;
      let found = false;

      for (let step = 0; step < 45; step++) {
        dist += 0.22;
        if (humanoidSDF(dirX * dist, y, dirZ * dist) >= 0.0) {
          let low = dist - 0.22, high = dist;
          for (let b = 0; b < 6; b++) {
            const mid = (low + high) * 0.5;
            if (humanoidSDF(dirX * mid, y, dirZ * mid) < 0) low = mid;
            else high = mid;
          }
          dist = (low + high) * 0.5;
          found = true;
          break;
        }
      }

      if (found && dist < 14.0) {
        const px = dirX * dist;
        const pz = dirZ * dist;
        const norm = calcNormal(px, y, pz);
        const dotView = Math.abs(norm[2]);
        const rim = Math.pow(1.0 - dotView, 2.4);
        const isEdge = dotView < 0.28;
        const scanBand = Math.cos(y * 26.0) * 0.25 + 0.75;
        const finalIntensity = isEdge ? Math.min(1.0, rim * 1.5) : (rim * 0.45 * scanBand);

        addPoint(px, y, pz, finalIntensity, isEdge);
      }
    }
  }

  // 3. Volumetric interior dust
  for (let i = 0; i < 4500; i++) {
    const u = (Math.random() - 0.5) * 5.5;
    const v = -5.0 + Math.random() * 12.0;
    const w = (Math.random() - 0.5) * 4.5;
    if (humanoidSDF(u, v, w) < -0.3) {
      addPoint(u, v, w, 0.08 + Math.random() * 0.2, false);
    }
  }

  bustGeometry = new THREE.BufferGeometry();
  bustGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  bustGeometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));

  const bustMaterial = new THREE.PointsMaterial({
    size: 0.13,
    vertexColors: true,
    map: particleTexture,
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false
  });

  bustMesh = new THREE.Points(bustGeometry, bustMaterial);
  bustMesh.position.y = -0.6;
  bustGroup.add(bustMesh);

  // Orbiting light trails
  const trailGroup = new THREE.Group();
  bustGroup.add(trailGroup);

  const trailConfigs = [
    { rx: 5.6, ry: 2.2, rz: 5.0, tiltX: 0.32, tiltZ: 0.48, speed: 0.007, color: 0x00f0ff },
    { rx: 6.5, ry: 2.7, rz: 5.9, tiltX: -0.52, tiltZ: -0.30, speed: -0.0055, color: 0x79ddff },
    { rx: 5.9, ry: 3.5, rz: 5.3, tiltX: 0.72, tiltZ: -0.58, speed: 0.0065, color: 0xffffff },
    { rx: 7.2, ry: 2.1, rz: 6.8, tiltX: -0.20, tiltZ: 0.78, speed: -0.0045, color: 0x3d94ff },
    { rx: 6.1, ry: 1.8, rz: 5.6, tiltX: 0.15, tiltZ: -0.85, speed: 0.0058, color: 0x00f0ff }
  ];

  trailConfigs.forEach(cfg => {
    const curvePts = [];
    const numPts = 240;
    for (let i = 0; i <= numPts; i++) {
      const a = (i / numPts) * Math.PI * 2;
      const x = cfg.rx * Math.cos(a);
      const y = 3.0 + cfg.ry * Math.sin(a) * 0.9;
      const z = cfg.rz * Math.sin(a);
      curvePts.push(new THREE.Vector3(x, y, z));
    }

    const curve = new THREE.CatmullRomCurve3(curvePts, true);
    const splinePoints = curve.getPoints(400);
    const trailGeo = new THREE.BufferGeometry().setFromPoints(splinePoints);
    const trailMat = new THREE.LineBasicMaterial({
      color: cfg.color,
      transparent: true,
      opacity: 0.6,
      blending: THREE.AdditiveBlending
    });

    const trailLine = new THREE.Line(trailGeo, trailMat);
    trailLine.rotation.x = cfg.tiltX;
    trailLine.rotation.z = cfg.tiltZ;

    const streamCount = 18;
    const streamGeo = new THREE.BufferGeometry();
    const streamPos = new Float32Array(streamCount * 3);
    streamGeo.setAttribute('position', new THREE.BufferAttribute(streamPos, 3));
    const streamMat = new THREE.PointsMaterial({
      size: 0.28,
      color: cfg.color,
      map: particleTexture,
      transparent: true,
      blending: THREE.AdditiveBlending
    });
    const streamParticles = new THREE.Points(streamGeo, streamMat);
    trailLine.add(streamParticles);

    trailGroup.add(trailLine);
    lightTrails.push({ trailLine, curve, streamParticles, streamCount, speed: cfg.speed, headProgress: Math.random() });
  });

  // Background cosmic dust field
  const DUST_COUNT = 14000;
  const dustGeo = new THREE.BufferGeometry();
  const dustPos = new Float32Array(DUST_COUNT * 3);
  const dustCols = new Float32Array(DUST_COUNT * 3);

  const dustCyan = new THREE.Color(0x79ddff);
  const dustWhite = new THREE.Color(0xffffff);
  const dustNavy = new THREE.Color(0x184488);

  for (let i = 0; i < DUST_COUNT; i++) {
    const i3 = i * 3;
    const clusterAngle = Math.random() * Math.PI * 2;
    const clusterRadius = 4 + Math.pow(Math.random(), 1.8) * 45;
    const clusterHeight = (Math.random() - 0.5) * 80;

    dustPos[i3] = Math.cos(clusterAngle) * clusterRadius + (Math.random() - 0.5) * 12;
    dustPos[i3 + 1] = clusterHeight;
    dustPos[i3 + 2] = Math.sin(clusterAngle) * clusterRadius - 15 + (Math.random() - 0.5) * 20;

    const colChoice = Math.random();
    const dCol = new THREE.Color();
    if (colChoice > 0.8) dCol.copy(dustWhite);
    else if (colChoice > 0.4) dCol.copy(dustCyan);
    else dCol.copy(dustNavy);

    dustCols[i3] = dCol.r;
    dustCols[i3 + 1] = dCol.g;
    dustCols[i3 + 2] = dCol.b;
  }

  dustGeo.setAttribute('position', new THREE.BufferAttribute(dustPos, 3));
  dustGeo.setAttribute('color', new THREE.BufferAttribute(dustCols, 3));

  const dustMat = new THREE.PointsMaterial({
    size: 0.12,
    vertexColors: true,
    map: particleTexture,
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false
  });

  dustCloud = new THREE.Points(dustGeo, dustMat);
  scene.add(dustCloud);

  // Mouse move listener
  window.addEventListener('mousemove', (e) => {
    const mx = (e.clientX / window.innerWidth - 0.5) * 2;
    const my = (e.clientY / window.innerHeight - 0.5) * 2;
    targetRotY = mx * 0.36;
    targetRotX = my * 0.20;
  });

  canvas3D.addEventListener('click', (e) => {
    if (e.target === canvas3D) triggerQuantumPulse();
  });

  window.addEventListener('resize', () => {
    if (!camera || !renderer) return;
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });

  // Render loop
  const clock = new THREE.Clock();

  function animateThree() {
    requestAnimationFrame(animateThree);
    const time = clock.getElapsedTime();

    if (bustGroup) {
      bustGroup.rotation.y += (targetRotY - bustGroup.rotation.y) * 0.055;
      bustGroup.rotation.x += (targetRotX - bustGroup.rotation.x) * 0.055;
      bustGroup.position.y = Math.sin(time * 1.25) * 0.24;
    }

    lightTrails.forEach(trail => {
      trail.trailLine.rotation.y += trail.speed;
      trail.headProgress = (trail.headProgress + 0.0035) % 1.0;

      const posAttr = trail.streamParticles.geometry.attributes.position;
      const arr = posAttr.array;

      for (let i = 0; i < trail.streamCount; i++) {
        const prog = (trail.headProgress - i * 0.012 + 1.0) % 1.0;
        const pt = trail.curve.getPoint(prog);
        arr[i * 3] = pt.x;
        arr[i * 3 + 1] = pt.y;
        arr[i * 3 + 2] = pt.z;
      }
      posAttr.needsUpdate = true;
    });

    if (dustCloud) {
      dustCloud.rotation.y = time * 0.01;
    }

    if (bustGeometry && positions) {
      const posAttr = bustGeometry.attributes.position;
      const posArr = posAttr.array;

      if (pulseFactor > 0.001) pulseFactor *= 0.94;

      for (let i = 0; i < pIndex; i++) {
        const i3 = i * 3;
        const ox = originPositions[i3];
        const oy = originPositions[i3 + 1];
        const oz = originPositions[i3 + 2];

        if (pulseFactor > 0.01) {
          const dist = Math.sqrt(ox * ox + oy * oy + oz * oz) + 0.1;
          posArr[i3] += (ox / dist) * pulseFactor * 0.42;
          posArr[i3 + 1] += (oy / dist) * pulseFactor * 0.42;
          posArr[i3 + 2] += (oz / dist) * pulseFactor * 0.42;
        } else {
          const wave = Math.sin(oy * 1.6 + time * 2.2) * 0.032;
          posArr[i3] += (ox + wave - posArr[i3]) * 0.085;
          posArr[i3 + 1] += (oy - posArr[i3 + 1]) * 0.085;
          posArr[i3 + 2] += (oz + wave - posArr[i3 + 2]) * 0.085;
        }
      }
      posAttr.needsUpdate = true;
    }

    renderer.render(scene, camera);
  }
  animateThree();
}

function triggerQuantumPulse() {
  pulseFactor = 1.0;
  playChime();
}

// =========================================================================
// 2. INTERACTIVE 2D UNIFIED SIGNAL GRAPH
// =========================================================================
let graphCanvas, gCtx;
let graphNodes = [];
let graphEdges = [];
let activeDragNode = null;
let isGraphDragging = false;

function initSignalGraph() {
  graphCanvas = document.getElementById('signalGraphCanvas');
  if (!graphCanvas) return;
  gCtx = graphCanvas.getContext('2d');

  const container = graphCanvas.parentElement;
  graphCanvas.width = container.clientWidth;
  graphCanvas.height = container.clientHeight;

  const cx = graphCanvas.width / 2;
  const cy = graphCanvas.height / 2;

  graphNodes = [
    { id: 'core', label: 'SensorsHub Core (GCP)', type: 'FastAPI Gateway', x: cx, y: cy, vx: 0, vy: 0, r: 24, isCore: true, color: '#00f0ff' },
    { id: 'esp32', label: 'XiaoZhi ESP32 Client', type: 'Audio & Display', x: cx - 180, y: cy - 90, vx: (Math.random() - 0.5) * 0.3, vy: (Math.random() - 0.5) * 0.3, r: 16, color: '#79ddff' },
    { id: 'dht22', label: 'DHT22 Living Room', type: 'Temp & Humidity', x: cx - 210, y: cy + 80, vx: (Math.random() - 0.5) * 0.3, vy: (Math.random() - 0.5) * 0.3, r: 13, color: '#00f59b' },
    { id: 'bmp280', label: 'BMP280 Barometer', type: 'Pressure Node', x: cx - 60, y: cy + 180, vx: (Math.random() - 0.5) * 0.3, vy: (Math.random() - 0.5) * 0.3, r: 13, color: '#00f59b' },
    { id: 'air', label: 'BME680 Air Sensor', type: 'Air Quality (PPM)', x: cx + 180, y: cy - 100, vx: (Math.random() - 0.5) * 0.3, vy: (Math.random() - 0.5) * 0.3, r: 14, color: '#ffaa00' },
    { id: 'db', label: 'SQLite WAL Database', type: 'Persistence', x: cx + 200, y: cy + 90, vx: (Math.random() - 0.5) * 0.3, vy: (Math.random() - 0.5) * 0.3, r: 15, color: '#a855f7' },
    { id: 'mcp', label: 'XiaoZhi Cloud MCP', type: 'Voice LLM Tools', x: cx + 70, y: cy - 170, vx: (Math.random() - 0.5) * 0.3, vy: (Math.random() - 0.5) * 0.3, r: 16, color: '#00f0ff' },
    { id: 'rpi', label: 'Raspberry Pi Deck', type: 'Local Hub', x: cx - 90, y: cy - 180, vx: (Math.random() - 0.5) * 0.3, vy: (Math.random() - 0.5) * 0.3, r: 14, color: '#79ddff' }
  ];

  graphEdges = [];
  for (let i = 1; i < graphNodes.length; i++) {
    graphEdges.push({ from: 0, to: i, packets: [{ progress: Math.random(), speed: 0.008 + Math.random() * 0.008 }] });
    if (i > 1 && Math.random() > 0.4) {
      graphEdges.push({ from: i, to: i - 1, packets: [{ progress: Math.random(), speed: 0.006 }] });
    }
  }

  const nodesCountEl = document.getElementById('graphNodesCount');
  if (nodesCountEl) nodesCountEl.textContent = `${graphNodes.length} ACTIVE`;

  function renderGraph() {
    if (!gCtx || !graphCanvas) return;
    gCtx.clearRect(0, 0, graphCanvas.width, graphCanvas.height);
    const cx = graphCanvas.width / 2;
    const cy = graphCanvas.height / 2;

    // Draw Edges & data packets
    graphEdges.forEach(e => {
      const n1 = graphNodes[e.from];
      const n2 = graphNodes[e.to];
      if (!n1 || !n2) return;

      gCtx.strokeStyle = 'rgba(0, 200, 255, 0.16)';
      gCtx.lineWidth = 1;
      gCtx.beginPath();
      gCtx.moveTo(n1.x, n1.y);
      gCtx.lineTo(n2.x, n2.y);
      gCtx.stroke();

      e.packets.forEach(p => {
        p.progress += p.speed;
        if (p.progress > 1) p.progress = 0;

        const px = n1.x + (n2.x - n1.x) * p.progress;
        const py = n1.y + (n2.y - n1.y) * p.progress;

        gCtx.fillStyle = '#ffffff';
        gCtx.shadowBlur = 8;
        gCtx.shadowColor = '#00f0ff';
        gCtx.beginPath();
        gCtx.arc(px, py, 2.5, 0, Math.PI * 2);
        gCtx.fill();
        gCtx.shadowBlur = 0;
      });
    });

    // Draw Nodes
    graphNodes.forEach(n => {
      if (n.isCore) {
        const coreGlow = gCtx.createRadialGradient(n.x, n.y, 0, n.x, n.y, 45);
        coreGlow.addColorStop(0, 'rgba(0, 240, 255, 0.4)');
        coreGlow.addColorStop(1, 'transparent');
        gCtx.fillStyle = coreGlow;
        gCtx.beginPath();
        gCtx.arc(n.x, n.y, 45, 0, Math.PI * 2);
        gCtx.fill();
      }

      gCtx.fillStyle = n.isCore ? '#ffffff' : 'rgba(10, 20, 36, 0.95)';
      gCtx.strokeStyle = n.color || (n.isCore ? '#00f0ff' : 'rgba(121, 221, 255, 0.6)');
      gCtx.lineWidth = n.isCore ? 3 : 1.5;
      gCtx.beginPath();
      gCtx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      gCtx.fill();
      gCtx.stroke();

      gCtx.font = n.isCore ? '600 12px Inter' : '400 11px Inter';
      gCtx.fillStyle = n.isCore ? '#ffffff' : '#c6d0df';
      gCtx.textAlign = 'center';
      gCtx.fillText(n.label, n.x, n.y + n.r + 16);

      if (n.type) {
        gCtx.font = '10px "JetBrains Mono"';
        gCtx.fillStyle = '#7e8c9f';
        gCtx.fillText(n.type, n.x, n.y + n.r + 28);
      }

      if (!n.isCore && n !== activeDragNode) {
        n.x += n.vx;
        n.y += n.vy;
        const dx = n.x - cx;
        const dy = n.y - cy;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist > 280) {
          n.vx -= (dx / dist) * 0.02;
          n.vy -= (dy / dist) * 0.02;
        }
      }
    });

    requestAnimationFrame(renderGraph);
  }
  renderGraph();

  graphCanvas.addEventListener('mousedown', (e) => {
    const rect = graphCanvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    graphNodes.forEach(n => {
      const d = Math.hypot(n.x - mx, n.y - my);
      if (d < n.r + 12) {
        isGraphDragging = true;
        activeDragNode = n;
      }
    });
  });

  window.addEventListener('mousemove', (e) => {
    if (isGraphDragging && activeDragNode) {
      const rect = graphCanvas.getBoundingClientRect();
      activeDragNode.x = e.clientX - rect.left;
      activeDragNode.y = e.clientY - rect.top;
    }
  });

  window.addEventListener('mouseup', () => {
    isGraphDragging = false;
    activeDragNode = null;
  });

  window.addEventListener('resize', () => {
    if (!graphCanvas || !graphCanvas.parentElement) return;
    graphCanvas.width = graphCanvas.parentElement.clientWidth;
    graphCanvas.height = graphCanvas.parentElement.clientHeight;
  });
}

// =========================================================================
// 3. CELESTIAL AMBIENT SYNTHESIZER & CHIMES (Web Audio API)
// =========================================================================
let audioCtx = null;
let isSoundOn = false;
let padOscs = [];

function toggleCelestialSound() {
  const audioBtn = document.getElementById('audioBtn');
  const audioText = document.getElementById('audioText');

  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioCtx.state === 'suspended') {
    audioCtx.resume();
  }

  if (!isSoundOn) {
    const chord = [73.42, 110.00, 185.00, 277.18, 329.63];
    const master = audioCtx.createGain();
    master.gain.setValueAtTime(0.08, audioCtx.currentTime);
    master.connect(audioCtx.destination);

    padOscs = chord.map((freq, i) => {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      const filter = audioCtx.createBiquadFilter();

      osc.type = i === 0 ? 'triangle' : 'sine';
      osc.frequency.setValueAtTime(freq, audioCtx.currentTime);

      filter.type = 'lowpass';
      filter.frequency.setValueAtTime(320 + i * 80, audioCtx.currentTime);

      gain.gain.setValueAtTime(0.03, audioCtx.currentTime);

      osc.connect(filter);
      filter.connect(gain);
      gain.connect(master);
      osc.start();
      return { osc, gain };
    });

    isSoundOn = true;
    if (audioBtn) audioBtn.classList.add('active');
    if (audioText) audioText.textContent = "Sound: On";
    showToast("Celestial ambient soundscape active", "info");
  } else {
    padOscs.forEach(o => {
      try { o.osc.stop(); } catch(e){}
    });
    padOscs = [];
    isSoundOn = false;
    if (audioBtn) audioBtn.classList.remove('active');
    if (audioText) audioText.textContent = "Sound";
  }
}

function playChime() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioCtx.state === 'suspended') audioCtx.resume();

  const osc = audioCtx.createOscillator();
  const g = audioCtx.createGain();
  osc.type = 'sine';
  osc.frequency.setValueAtTime(587.33, audioCtx.currentTime);
  osc.frequency.exponentialRampToValueAtTime(1174.66, audioCtx.currentTime + 0.6);
  g.gain.setValueAtTime(0.12, audioCtx.currentTime);
  g.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.6);
  osc.connect(g);
  g.connect(audioCtx.destination);
  osc.start();
  osc.stop(audioCtx.currentTime + 0.6);
}

// =========================================================================
// 4. SENSORS FLEET TELEMETRY & HARDWARE MATRIX
// =========================================================================
const SENSORS_DATA = [
  { id: 'node_1', name: 'Living Room DHT22', location: 'Zone 1 - Main Salon', type: 'temp', value: 23.4, unit: '°C', status: 'online', history: [22.8, 23.0, 23.1, 23.3, 23.4], min: 18, max: 32, icon: 'thermometer', accent: '#79ddff' },
  { id: 'node_2', name: 'Ambient Air Quality', location: 'Zone 1 - Main Salon', type: 'air', value: 412, unit: 'ppm', status: 'online', history: [390, 400, 405, 410, 412], min: 300, max: 1000, icon: 'wind', accent: '#ffaa00' },
  { id: 'node_3', name: 'Master Bedroom Temp', location: 'Zone 2 - Bedroom Suite', type: 'temp', value: 21.8, unit: '°C', status: 'online', history: [22.0, 21.9, 21.9, 21.8, 21.8], min: 18, max: 32, icon: 'thermometer', accent: '#79ddff' },
  { id: 'node_4', name: 'Humidity Master', location: 'Zone 2 - Bedroom Suite', type: 'humidity', value: 52, unit: '%', status: 'online', history: [48, 50, 51, 51, 52], min: 20, max: 80, icon: 'droplet', accent: '#00f0ff' },
  { id: 'node_5', name: 'Server Rack BMP280', location: 'Zone 4 - Server Vault', type: 'temp', value: 36.2, unit: '°C', status: 'standby', history: [34.0, 35.1, 35.8, 36.0, 36.2], min: 20, max: 65, icon: 'cpu', accent: '#a855f7' },
  { id: 'node_6', name: 'Atmospheric Pressure', location: 'Zone 4 - Server Vault', type: 'power', value: 1014, unit: 'hPa', status: 'online', history: [1013, 1013, 1014, 1014, 1014], min: 980, max: 1040, icon: 'activity', accent: '#00f59b' },
  { id: 'node_7', name: 'Main Power Rail', location: 'Zone 0 - Power Ingress', type: 'power', value: 230.5, unit: 'V', status: 'online', history: [229.8, 230.1, 230.2, 230.4, 230.5], min: 210, max: 245, icon: 'zap', accent: '#00f59b' }
];

let isLive = true;
let updateInterval = null;
let currentFilter = 'all';
let sseConnection = null;

async function ensureDashboardSession() {
  try {
    const res = await fetch('/api/v1/device/stats');
    if (!res.ok) throw new Error('Gateway Offline');
  } catch (e) {
    console.warn('Dashboard session note:', e.message);
  }
}

function renderSensors() {
  const grid = document.getElementById('sensorsGrid');
  if (!grid) return;

  const searchVal = (document.getElementById('searchSensorsInput')?.value || '').toLowerCase();

  const filtered = SENSORS_DATA.filter(s => {
    const matchFilter = currentFilter === 'all' || s.type === currentFilter;
    const matchSearch = !searchVal || s.name.toLowerCase().includes(searchVal) || s.location.toLowerCase().includes(searchVal);
    return matchFilter && matchSearch;
  });

  if (filtered.length === 0) {
    grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px 0; color: var(--text-muted); font-size: 14px;">No telemetry nodes matching criteria.</div>`;
    return;
  }

  grid.innerHTML = '';
  filtered.forEach(s => {
    const pct = Math.min(100, Math.max(0, ((s.value - s.min) / (s.max - s.min)) * 100));
    const card = document.createElement('div');
    card.className = 'sensor-card';
    card.style.setProperty('--sensor-accent', s.accent || 'var(--cyan-core)');

    card.innerHTML = `
      <div class="sensor-card-top">
        <div class="sensor-title-group">
          <div class="sensor-avatar">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 14 14"></polyline></svg>
          </div>
          <div>
            <div class="sensor-name">${escapeHtml(s.name)}</div>
            <div class="sensor-loc">${escapeHtml(s.location)}</div>
          </div>
        </div>
        <span class="status-tag ${s.status}">${s.status}</span>
      </div>

      <div class="sensor-data-row">
        <div class="sensor-main-val">
          <span class="sensor-num" id="val_${s.id}">${typeof s.value === 'number' ? s.value.toFixed(1) : s.value}</span>
          <span class="sensor-unit">${escapeHtml(s.unit)}</span>
        </div>
        <div class="sparkline-box">
          <canvas id="spark_${s.id}" width="100" height="38"></canvas>
        </div>
      </div>

      <div class="sensor-bar-wrap">
        <div class="sensor-bar-progress" id="bar_${s.id}" style="width: ${pct}%;"></div>
      </div>

      <div class="sensor-meta-grid">
        <div class="sensor-meta-item">
          <span class="lbl">MIN / MAX</span>
          <span class="val">${s.min} / ${s.max}</span>
        </div>
        <div class="sensor-meta-item">
          <span class="lbl">SIGNAL</span>
          <span class="val" style="color:var(--emerald);">100% OK</span>
        </div>
        <div class="sensor-meta-item">
          <span class="lbl">REFRESH</span>
          <span class="val">1.5s</span>
        </div>
      </div>
    `;

    grid.appendChild(card);
    drawSparkline(`spark_${s.id}`, s.history, s.accent || '#79ddff');
  });
}

function drawSparkline(canvasId, data, strokeColor) {
  const c = document.getElementById(canvasId);
  if (!c) return;
  const ctx = c.getContext('2d');
  const w = c.width;
  const h = c.height;

  ctx.clearRect(0, 0, w, h);
  if (!data || data.length < 2) return;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;

  ctx.strokeStyle = strokeColor;
  ctx.lineWidth = 1.8;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';

  ctx.beginPath();
  data.forEach((val, i) => {
    const x = (i / (data.length - 1)) * (w - 8) + 4;
    const y = h - ((val - min) / range) * (h - 10) - 5;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Glow fill
  ctx.lineTo(w - 4, h);
  ctx.lineTo(4, h);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, strokeColor.replace(')', ', 0.25)').replace('rgb', 'rgba'));
  grad.addColorStop(1, 'transparent');
  ctx.fillStyle = grad;
  ctx.fill();
}

function updateTopMetrics() {
  const temps = SENSORS_DATA.filter(s => s.type === 'temp');
  const hums = SENSORS_DATA.filter(s => s.type === 'humidity');

  const avgTemp = temps.length ? (temps.reduce((a, b) => a + b.value, 0) / temps.length).toFixed(1) : '22.4';
  const avgHum = hums.length ? Math.round(hums.reduce((a, b) => a + b.value, 0) / hums.length) : '48';

  const elTemp = document.getElementById('topAvgTemp');
  const elHum = document.getElementById('topAvgHumidity');
  if (elTemp) elTemp.textContent = `${avgTemp} °C`;
  if (elHum) elHum.textContent = `${avgHum} %`;
}

function simulateTelemetryTick() {
  SENSORS_DATA.forEach(s => {
    if (s.status === 'online') {
      const delta = (Math.random() - 0.49) * 0.3;
      s.value = Math.max(s.min, Math.min(s.max, Number((s.value + delta).toFixed(1))));
      s.history.push(s.value);
      if (s.history.length > 12) s.history.shift();

      const valEl = document.getElementById(`val_${s.id}`);
      if (valEl) valEl.textContent = s.value.toFixed(1);

      const barEl = document.getElementById(`bar_${s.id}`);
      if (barEl) {
        const pct = Math.min(100, Math.max(0, ((s.value - s.min) / (s.max - s.min)) * 100));
        barEl.style.width = `${pct}%`;
      }

      drawSparkline(`spark_${s.id}`, s.history, s.accent || '#79ddff');
    }
  });

  updateTopMetrics();
}

function updateClock() {
  const el = document.getElementById('timeDisplay');
  if (!el) return;
  const now = new Date();
  el.textContent = now.toTimeString().split(' ')[0] + ' UTC';
}

function logEvent(msg, type = 'info') {
  const feed = document.getElementById('eventLogFeed');
  if (!feed) return;

  const now = new Date();
  const timeStr = now.toTimeString().split(' ')[0];

  const entry = document.createElement('div');
  entry.className = `event-entry ${type}`;
  entry.innerHTML = `
    <span class="event-time">[${timeStr}]</span>
    <span class="event-msg">${escapeHtml(msg)}</span>
  `;

  feed.insertBefore(entry, feed.firstChild);
  if (feed.children.length > 50) {
    feed.removeChild(feed.lastChild);
  }
}

// =========================================================================
// 5. HARDWARE HUB & LOCAL NETWORK TOOLS
// =========================================================================
async function checkEsp32Connection(manual = true) {
  const badge = document.getElementById('esp32Badge');
  const text = document.getElementById('esp32StatusText');
  const meta = document.getElementById('esp32MetaText');
  const respBox = document.getElementById('netQueryResponseBox');
  const respText = document.getElementById('netQueryText');
  const respTime = document.getElementById('netQueryTime');

  if (manual && respBox && respText) {
    respBox.style.display = 'block';
    respText.textContent = 'Contacting XiaoZhi ESP32 Hardware Node & Gateway...';
    if (respTime) respTime.textContent = new Date().toLocaleTimeString();
  }

  try {
    const res = await fetch('/api/v1/device/stats');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (badge) {
      badge.style.background = 'rgba(0, 245, 155, 0.1)';
      badge.style.borderColor = 'rgba(0, 245, 155, 0.3)';
      badge.style.color = 'var(--emerald)';
    }
    if (text) text.textContent = 'ESP32 Online (GCP)';
    if (meta) meta.textContent = `IP: 192.168.1.105 | RSSI: -52 dBm | Events: ${data.total_events || 0}`;

    if (manual) {
      if (respText) respText.textContent = `ESP32 Status: ONLINE\nGateway: Active\nRegistered Categories: ${JSON.stringify(data.categories || [])}\nTotal Telemetry Records: ${data.total_events || 0}`;
      showToast('ESP32 hardware client is connected and active', 'info');
      logEvent('ESP32 health check verified nominal.', 'info');
    }
  } catch (err) {
    if (badge) {
      badge.style.background = 'rgba(255, 46, 91, 0.15)';
      badge.style.borderColor = 'rgba(255, 46, 91, 0.4)';
      badge.style.color = 'var(--rose)';
    }
    if (text) text.textContent = 'ESP32 Standby';
    if (meta) meta.textContent = 'Reconnecting to cloud gateway...';
    if (manual && respText) {
      respText.textContent = `ESP32 Health Error: ${err.message}`;
    }
  }
}

async function executePingTest() {
  const preset = document.getElementById('pingTargetPreset');
  const custom = document.getElementById('pingCustomTarget');
  const tag = document.getElementById('pingValTag');
  const respBox = document.getElementById('netQueryResponseBox');
  const respText = document.getElementById('netQueryText');
  const respTime = document.getElementById('netQueryTime');

  let target = preset ? preset.value : '127.0.0.1';
  if (target === 'custom' && custom) {
    target = custom.value.trim() || '127.0.0.1';
  }

  if (tag) tag.textContent = 'Pinging...';
  if (respBox && respText) {
    respBox.style.display = 'block';
    respText.textContent = `Executing ICMP ping to ${target}...`;
    if (respTime) respTime.textContent = new Date().toLocaleTimeString();
  }

  try {
    const res = await fetch(`/api/v1/network/ping?target=${encodeURIComponent(target)}&count=2`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (tag) {
      tag.textContent = `${data.latency_ms} ms`;
      tag.className = `ping-result-tag ${data.reachable ? 'online' : 'offline'}`;
    }

    if (respText) {
      respText.textContent = `Ping to ${target}: ${data.reachable ? 'SUCCESS' : 'FAILED'}\nRound-trip Latency: ${data.latency_ms} ms\nStatus: ${data.status || 'OK'}`;
    }

    logEvent(`Ping to ${target}: ${data.latency_ms}ms (${data.reachable ? 'reachable' : 'unreachable'})`, data.reachable ? 'info' : 'warn');
  } catch (err) {
    if (tag) {
      tag.textContent = 'Error';
      tag.className = 'ping-result-tag offline';
    }
    if (respText) respText.textContent = `Ping Error: ${err.message}`;
  }
}

async function checkServerDiskSpace(manual = true) {
  const meta = document.getElementById('diskUsageMeta');
  const bar = document.getElementById('diskProgressBar');
  const respBox = document.getElementById('netQueryResponseBox');
  const respText = document.getElementById('netQueryText');

  try {
    const res = await fetch('/api/v1/network/disk');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (meta) meta.textContent = `Used: ${data.used_gb} GB / ${data.total_gb} GB (${data.percent_used}%)`;
    if (bar) bar.style.width = `${data.percent_used}%`;

    if (manual && respBox && respText) {
      respBox.style.display = 'block';
      respText.textContent = `Server Disk Stats:\nFree Space: ${data.free_gb} GB\nUsed Space: ${data.used_gb} GB / ${data.total_gb} GB (${data.percent_used}% used)`;
      showToast('Disk usage updated', 'info');
    }
  } catch (err) {
    if (manual && respText) respText.textContent = `Disk Check Error: ${err.message}`;
  }
}

// =========================================================================
// 6. SYNCHRONIZED TO-DO TASK MANAGEMENT (Backend /api/v1/todos)
// =========================================================================
const DASHBOARD_TODO_KEY = 'sensorshub_cortesa_todos';
let dashboardTodos = [];
let currentTodoFilter = 'all';

async function initDashboardTodos() {
  const btnAdd = document.getElementById('btnAddTodo');
  const input = document.getElementById('newTodoInput');
  const selectPrio = document.getElementById('todoPrioritySelect');

  if (btnAdd && input) {
    btnAdd.addEventListener('click', () => {
      const text = (input.value || '').trim();
      const prio = selectPrio ? selectPrio.value : 'medium';
      if (text) {
        addDashboardTodo(text, prio);
        input.value = '';
      }
    });

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const text = (input.value || '').trim();
        const prio = selectPrio ? selectPrio.value : 'medium';
        if (text) {
          addDashboardTodo(text, prio);
          input.value = '';
        }
      }
    });
  }

  const btnAll = document.getElementById('btnFilterAll');
  const btnActive = document.getElementById('btnFilterActive');
  const btnDone = document.getElementById('btnFilterDone');
  const btnClearDone = document.getElementById('btnClearDoneTodos');

  if (btnAll) btnAll.addEventListener('click', () => setTodoFilter('all', btnAll));
  if (btnActive) btnActive.addEventListener('click', () => setTodoFilter('active', btnActive));
  if (btnDone) btnDone.addEventListener('click', () => setTodoFilter('done', btnDone));
  if (btnClearDone) btnClearDone.addEventListener('click', clearCompletedDashboardTodos);

  await fetchDashboardTodos();
}

function setTodoFilter(filterName, activeBtn) {
  currentTodoFilter = filterName;
  document.querySelectorAll('#tasks .pill-btn').forEach(b => {
    if (b.id.startsWith('btnFilter')) b.classList.remove('active');
  });
  if (activeBtn) activeBtn.classList.add('active');
  renderDashboardTodos();
}

async function fetchDashboardTodos() {
  try {
    const res = await fetch('/api/v1/todos');
    if (res.ok) {
      const data = await res.json();
      if (data.todos && Array.isArray(data.todos)) {
        dashboardTodos = data.todos;
        localStorage.setItem(DASHBOARD_TODO_KEY, JSON.stringify(dashboardTodos));
        renderDashboardTodos();
        return;
      }
    }
  } catch (e) {}

  try {
    const stored = localStorage.getItem(DASHBOARD_TODO_KEY);
    dashboardTodos = stored ? JSON.parse(stored) : [];
  } catch (e) {
    dashboardTodos = [];
  }
  renderDashboardTodos();
}

function renderDashboardTodos() {
  const listEl = document.getElementById('todosList');
  if (!listEl) return;

  const total = dashboardTodos.length;
  const pending = dashboardTodos.filter(t => !t.completed).length;
  const done = dashboardTodos.filter(t => t.completed).length;

  const statTotal = document.getElementById('statTotalTodos');
  const statPending = document.getElementById('statPendingTodos');
  const statDone = document.getElementById('statDoneTodos');

  if (statTotal) statTotal.textContent = total;
  if (statPending) statPending.textContent = pending;
  if (statDone) statDone.textContent = done;

  const filtered = dashboardTodos.filter(t => {
    if (currentTodoFilter === 'active') return !t.completed;
    if (currentTodoFilter === 'done') return t.completed;
    return true;
  });

  if (filtered.length === 0) {
    listEl.innerHTML = `
      <div style="text-align: center; padding: 32px 0; color: var(--text-muted); font-size: 13px;">
        No tasks in this view. Add one above or command your XiaoZhi voice assistant!
      </div>
    `;
    return;
  }

  listEl.innerHTML = '';
  filtered.forEach(todo => {
    const item = document.createElement('div');
    item.className = `todo-item ${todo.completed ? 'completed' : ''}`;
    const prio = (todo.priority || 'medium').toLowerCase();

    item.innerHTML = `
      <div class="todo-left">
        <button type="button" class="todo-check-btn ${todo.completed ? 'checked' : ''}" title="${todo.completed ? 'Mark pending' : 'Mark completed'}">
          ${todo.completed ? '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5"><polyline points="20 6 9 17 4 12"></polyline></svg>' : ''}
        </button>
        <span class="todo-text">${escapeHtml(todo.text)}</span>
      </div>
      <div style="display: flex; align-items: center; gap: 10px;">
        <span class="todo-priority-badge ${prio}">${escapeHtml(todo.priority || 'medium')}</span>
        <button type="button" class="todo-del-btn" title="Delete task" style="background: none; border: none; color: var(--text-dim); cursor: pointer; padding: 4px;">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="3 6 5 6 21 6"></polyline>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
          </svg>
        </button>
      </div>
    `;

    const checkBtn = item.querySelector('.todo-check-btn');
    checkBtn.addEventListener('click', () => toggleDashboardTodo(todo.id));

    const delBtn = item.querySelector('.todo-del-btn');
    delBtn.addEventListener('click', () => deleteDashboardTodo(todo.id));

    listEl.appendChild(item);
  });
}

async function addDashboardTodo(text, priority = 'medium') {
  const trimmed = text.trim();
  if (!trimmed) return;

  try {
    const res = await fetch('/api/v1/todos', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: trimmed, priority: priority }),
    });
    if (res.ok) {
      const data = await res.json();
      if (data.todo) {
        dashboardTodos.unshift(data.todo);
        localStorage.setItem(DASHBOARD_TODO_KEY, JSON.stringify(dashboardTodos));
        renderDashboardTodos();
        showToast(`Task added: "${trimmed}"`, 'info');
        logEvent(`To-Do Created: "${trimmed}" [${priority}]`, 'info');
        return;
      }
    }
  } catch (e) {}

  const newTodo = {
    id: 'todo_' + Date.now(),
    text: trimmed,
    priority: priority,
    completed: false,
    createdAt: new Date().toISOString(),
  };
  dashboardTodos.unshift(newTodo);
  localStorage.setItem(DASHBOARD_TODO_KEY, JSON.stringify(dashboardTodos));
  renderDashboardTodos();
  showToast(`Task added: "${trimmed}"`, 'info');
}

async function toggleDashboardTodo(id) {
  const target = dashboardTodos.find(t => t.id === id);
  if (!target) return;
  const newStatus = !target.completed;
  target.completed = newStatus;

  try {
    await fetch(`/api/v1/todos/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ completed: newStatus }),
    });
  } catch (e) {}

  localStorage.setItem(DASHBOARD_TODO_KEY, JSON.stringify(dashboardTodos));
  renderDashboardTodos();
}

async function deleteDashboardTodo(id) {
  dashboardTodos = dashboardTodos.filter(t => t.id !== id);
  try {
    await fetch(`/api/v1/todos/${encodeURIComponent(id)}`, { method: 'DELETE' });
  } catch (e) {}

  localStorage.setItem(DASHBOARD_TODO_KEY, JSON.stringify(dashboardTodos));
  renderDashboardTodos();
  showToast('Task deleted', 'info');
}

async function clearCompletedDashboardTodos() {
  const completedIds = dashboardTodos.filter(t => t.completed).map(t => t.id);
  dashboardTodos = dashboardTodos.filter(t => !t.completed);
  localStorage.setItem(DASHBOARD_TODO_KEY, JSON.stringify(dashboardTodos));
  renderDashboardTodos();

  for (const id of completedIds) {
    try {
      fetch(`/api/v1/todos/${encodeURIComponent(id)}`, { method: 'DELETE' });
    } catch (e) {}
  }
  showToast('Completed tasks cleared', 'info');
}

// =========================================================================
// 7. REAL-TIME SSE & ESP32 ALERT DISPATCHER
// =========================================================================
function connectToEsp32Sse() {
  try {
    sseConnection = new EventSource('/api/v1/events/stream');

    sseConnection.onopen = () => {
      logEvent('Real-time SSE telemetry link established with ESP32 gateway.', 'info');
    };

    sseConnection.onmessage = (e) => {
      try {
        const payload = JSON.parse(e.data);
        if (payload.type === 'esp32_data') {
          logEvent(`ESP32 Voice Ingestion: [${payload.category || 'transcript'}] ${JSON.stringify(payload.data || {})}`, 'info');
          playChime();
        } else if (payload.type === 'gateway_status') {
          updateGatewayUI(payload);
        } else if (payload.type === 'todo_created' || payload.type === 'todo_deleted' || payload.type === 'todo_updated') {
          fetchDashboardTodos();
        }
      } catch (err) {}
    };
  } catch (e) {
    console.warn('SSE connection skipped:', e);
  }
}

function openAlertModal() {
  const modal = document.getElementById('alertModal');
  if (modal) modal.classList.add('open');
}

function closeAlertModal() {
  const modal = document.getElementById('alertModal');
  if (modal) modal.classList.remove('open');
}

function openAddSensorModal() {
  const modal = document.getElementById('addSensorModal');
  if (modal) modal.classList.add('open');
}

function closeAddSensorModal() {
  const modal = document.getElementById('addSensorModal');
  if (modal) modal.classList.remove('open');
}

async function handlePushAlertSubmit(e) {
  e.preventDefault();
  const title = (document.getElementById('alertTitleInput')?.value || 'Server Notice').trim();
  const message = (document.getElementById('alertMsgInput')?.value || '').trim();
  const emotion = document.getElementById('alertEmotionSelect')?.value || 'notice';

  if (!message) return;

  try {
    const res = await fetch('/api/v1/device/notify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        device_id: 'mo-project-c3',
        title: title,
        message: message,
        emotion: emotion
      })
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    closeAlertModal();
    playChime();
    showToast(`Alert pushed to ESP32: "${message.substring(0, 35)}..."`, 'info');
    logEvent(`Hardware Alert Transmitted [${emotion}]: "${message}" via ${data.channel || 'WebSocket'}`, 'warn');
  } catch (err) {
    showToast(`Failed to push alert: ${err.message}`, 'danger');
  }
}

// Toast Helper
function showToast(msg, type = 'info') {
  const stack = document.getElementById('toastStack');
  if (!stack) return;

  const toast = document.createElement('div');
  toast.className = `toast-item ${type}`;
  toast.innerHTML = `
    <span>✦</span>
    <span>${escapeHtml(msg)}</span>
  `;

  stack.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

function escapeHtml(str) {
  if (typeof str !== 'string') return str;
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// =========================================================================
// 8. GATEWAY MASTER ACTIVE / STANDBY CONTROL
// =========================================================================
let isGatewayActive = true;

async function fetchGatewayStatus() {
  try {
    const res = await fetch('/api/v1/gateway/status');
    if (!res.ok) throw new Error('Status unreachable');
    const data = await res.json();
    isGatewayActive = data.active !== false;
    updateGatewayUI(data);
  } catch (e) {
    // If backend is completely offline, set UI gracefully
    updateGatewayUI({ active: false, status_text: 'STANDBY (Offline)', mcp_connected: false });
  }
}

async function toggleGateway() {
  try {
    const res = await fetch('/api/v1/gateway/toggle', { method: 'POST' });
    if (!res.ok) throw new Error('Toggle failed');
    const data = await res.json();
    isGatewayActive = data.active !== false;
    updateGatewayUI(data);

    playChime();
    if (isGatewayActive) {
      showToast('XiaoZhi Cloud MCP & Hardware Gateway ACTIVE', 'info');
      logEvent('Gateway activated: Cloud MCP WebSocket and ESP32 uplink connected.', 'info');
    } else {
      showToast('Gateway in STANDBY mode (0% background CPU / MCP paused)', 'warn');
      logEvent('Gateway standby: Cloud MCP disconnected, AI background loops paused.', 'warn');
    }
  } catch (e) {
    // Local toggle fallback
    isGatewayActive = !isGatewayActive;
    updateGatewayUI({ active: isGatewayActive, status_text: isGatewayActive ? 'ONLINE (Local)' : 'STANDBY (Local)', mcp_connected: false });
    showToast(`Gateway set to ${isGatewayActive ? 'ACTIVE' : 'STANDBY'}`, 'info');
  }
}

function updateGatewayUI(data) {
  const topBtn = document.getElementById('btnGatewayToggle');
  const topDot = document.getElementById('gatewayPulseDot');
  const topText = document.getElementById('gatewayStatusText');

  const panelBtn = document.getElementById('btnGatewayTogglePanel');
  const panelDot = document.getElementById('gatewayPanelPulseDot');
  const panelText = document.getElementById('gatewayPanelText');

  const active = data.active !== false;

  if (topText) topText.textContent = active ? 'Gateway: ACTIVE' : 'Gateway: STANDBY';
  if (topDot) {
    if (active) {
      topDot.classList.remove('paused');
      topDot.style.background = 'var(--emerald)';
      topDot.style.boxShadow = '0 0 10px var(--emerald)';
    } else {
      topDot.classList.add('paused');
      topDot.style.background = 'var(--amber)';
      topDot.style.boxShadow = '0 0 8px var(--amber)';
    }
  }
  if (topBtn) {
    if (active) topBtn.classList.add('active');
    else topBtn.classList.remove('active');
  }

  if (panelText) panelText.textContent = active ? 'GATEWAY: ACTIVE' : 'GATEWAY: STANDBY';
  if (panelDot) {
    if (active) {
      panelDot.classList.remove('paused');
      panelDot.style.background = 'var(--emerald)';
      panelDot.style.boxShadow = '0 0 10px var(--emerald)';
    } else {
      panelDot.classList.add('paused');
      panelDot.style.background = 'var(--amber)';
      panelDot.style.boxShadow = '0 0 8px var(--amber)';
    }
  }
  if (panelBtn) {
    if (active) {
      panelBtn.className = 'btn-cyber primary';
    } else {
      panelBtn.className = 'btn-cyber amber';
    }
  }
}

// =========================================================================
// 9. INITIALIZATION & EVENT BINDINGS
// =========================================================================
function bindEvents() {
  const audioBtn = document.getElementById('audioBtn');
  if (audioBtn) audioBtn.addEventListener('click', toggleCelestialSound);

  const btnGwTop = document.getElementById('btnGatewayToggle');
  if (btnGwTop) btnGwTop.addEventListener('click', toggleGateway);

  const btnGwPanel = document.getElementById('btnGatewayTogglePanel');
  if (btnGwPanel) btnGwPanel.addEventListener('click', toggleGateway);

  const pauseBtn = document.getElementById('btnPauseResume');
  const feedStatusText = document.getElementById('feedStatusText');
  const pulseDot = pauseBtn ? pauseBtn.querySelector('.pulse-dot') : null;

  if (pauseBtn) {
    pauseBtn.addEventListener('click', () => {
      isLive = !isLive;
      if (feedStatusText) feedStatusText.textContent = isLive ? 'LIVE STREAM' : 'FEED PAUSED';
      if (pulseDot) {
        if (isLive) pulseDot.classList.remove('paused');
        else pulseDot.classList.add('paused');
      }
      showToast(isLive ? 'Live telemetry stream resumed' : 'Telemetry stream paused', 'info');
    });
  }

  const addSensorBtn = document.getElementById('btnAddSensorBtn');
  if (addSensorBtn) addSensorBtn.addEventListener('click', openAddSensorModal);

  const pushAlertForm = document.getElementById('pushAlertForm');
  if (pushAlertForm) pushAlertForm.addEventListener('submit', handlePushAlertSubmit);

  const btnCheckEsp32 = document.getElementById('btnCheckEsp32');
  if (btnCheckEsp32) btnCheckEsp32.addEventListener('click', () => checkEsp32Connection(true));

  const btnPing = document.getElementById('btnExecutePing');
  if (btnPing) btnPing.addEventListener('click', executePingTest);

  const pingPreset = document.getElementById('pingTargetPreset');
  const pingCustom = document.getElementById('pingCustomTarget');
  if (pingPreset && pingCustom) {
    pingPreset.addEventListener('change', () => {
      pingCustom.style.display = pingPreset.value === 'custom' ? 'block' : 'none';
    });
  }

  const btnDisk = document.getElementById('btnRefreshDisk');
  if (btnDisk) btnDisk.addEventListener('click', () => checkServerDiskSpace(true));

  const btnClearLog = document.getElementById('btnClearTerminalBtn');
  if (btnClearLog) {
    btnClearLog.addEventListener('click', () => {
      const feed = document.getElementById('eventLogFeed');
      if (feed) feed.innerHTML = '';
    });
  }

  // Filter pills
  document.querySelectorAll('.filter-bar .pill-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const filter = btn.getAttribute('data-filter');
      if (filter) {
        currentFilter = filter;
        document.querySelectorAll('.filter-bar .pill-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        renderSensors();
      }
    });
  });

  const searchInput = document.getElementById('searchSensorsInput');
  if (searchInput) searchInput.addEventListener('input', renderSensors);

  // Year in footer
  const yearEl = document.getElementById('year');
  if (yearEl) yearEl.textContent = new Date().getFullYear();
}

async function initApp() {
  await ensureDashboardSession();
  initThreeBackground();
  initSignalGraph();
  bindEvents();
  initDashboardTodos();
  renderSensors();
  updateTopMetrics();
  updateClock();
  connectToEsp32Sse();
  checkEsp32Connection(false);
  checkServerDiskSpace(false);
  fetchGatewayStatus();

  logEvent('SensorsHub Cortesa engine active. Reconciled with Cloud MCP.', 'info');

  updateInterval = setInterval(() => {
    if (isLive) {
      simulateTelemetryTick();
    }
    updateClock();
  }, 1500);
}

document.addEventListener('DOMContentLoaded', () => {
  initApp();
});
