const $ = (id) => document.getElementById(id);
const viewport = $('viewport');
const enabledControls = 'button[data-view], #fit, #screenshot, #show-all, #object-search, #grid-toggle, #outline-toggle, #lighting';
let THREE, renderer, scene, camera, controls, board, grid, selectionBox;
let meshes = [], objects = [], rows = [], selected = null, frameRequested = false, view = 'iso';
let boardRadius = 1, upAxis = 'Y', metadata;
let orbitInteracting = false;
const lights = [];

function fail(title, detail) {
  $('state-panel').hidden = false;
  $('state-panel').classList.add('error');
  $('state-title').textContent = title;
  $('state-detail').textContent = detail;
  $('retry').hidden = false;
  $('render-status').textContent = 'Viewer unavailable';
  $('render-status').dataset.ready = 'false';
  document.querySelectorAll(enabledControls).forEach((element) => { element.disabled = true; });
}

$('retry').addEventListener('click', () => location.reload());

function requestRender() {
  if (frameRequested) return;
  frameRequested = true;
  requestAnimationFrame(() => {
    frameRequested = false;
    controls.update();
    positionReferenceGrid();
    renderer.render(scene, camera);
  });
}

function positionReferenceGrid() {
  if (!grid) return;
  const axis = upAxis.toLowerCase();
  grid.position[axis] = Math.abs(grid.position[axis]) * (camera.position[axis] >= 0 ? -1 : 1);
}

function resize() {
  const width = viewport.clientWidth, height = viewport.clientHeight;
  camera.aspect = width / Math.max(height, 1);
  camera.updateProjectionMatrix();
  renderer.setSize(width, height);
  requestRender();
}

function setCamera(nextView = null) {
  const direction = camera.position.clone().sub(controls.target).normalize();
  const damping = controls.enableDamping;
  controls.enableDamping = false;
  controls.update();
  controls.enableDamping = damping;
  if (nextView) view = nextView;
  const halfFov = Math.atan(Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * Math.min(1, camera.aspect));
  const distance = boardRadius / Math.sin(halfFov) * 1.18;
  const directions = upAxis === 'Z'
    ? { iso: [1, -1.2, 1.05], top: [0, -0.0001, 1], bottom: [0, 0.0001, -1] }
    : { iso: [1, 1.15, 1.25], top: [0, 1, 0.0001], bottom: [0, -1, -0.0001] };
  camera.up.set(...(upAxis === 'Z' ? [0, 0, 1] : [0, 1, 0]));
  if (nextView) direction.set(...directions[nextView]).normalize();
  camera.position.copy(direction.multiplyScalar(distance));
  controls.target.set(0, 0, 0);
  camera.lookAt(controls.target);
  controls.update();
  document.querySelectorAll('[data-view]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.view === view)));
  $('view-label').textContent = ({ iso: 'ISOMETRIC', top: 'TOP · FRONT', bottom: 'BOTTOM · BACK', free: 'FREE ORBIT' })[view];
  requestRender();
}

function objectName(object, index) {
  const names = [object.userData.name || object.name || `Object ${index + 1}`];
  for (let ancestor = object.parent; ancestor && ancestor !== board; ancestor = ancestor.parent) {
    if (ancestor.userData.name) names.unshift(ancestor.userData.name);
  }
  return names.join(' / ');
}

function clearSelection() {
  selected = null;
  selectionBox.visible = false;
  rows.forEach(({ row }) => row.classList.remove('selected'));
  $('selection-hint').hidden = false;
  $('selection-info').hidden = true;
  $('clear-selection').hidden = true;
  requestRender();
}

function selectObject(object) {
  selected = object;
  selectionBox.setFromObject(object);
  selectionBox.visible = $('outline-toggle').checked && object.visible;
  $('selected-object').textContent = objectName(object, objects.indexOf(object));
  const materials = new Set();
  object.traverse((part) => {
    if (!part.isMesh) return;
    const partMaterials = Array.isArray(part.material) ? part.material : [part.material];
    partMaterials.forEach((material) => materials.add(material.name || material.type));
  });
  $('selected-material').textContent = [...materials].join(', ');
  $('selection-hint').hidden = true;
  $('selection-info').hidden = false;
  $('clear-selection').hidden = false;
  rows.forEach(({ row, object: rowObject }) => row.classList.toggle('selected', rowObject === object));
  requestRender();
}

function buildObjectList() {
  $('objects').replaceChildren();
  rows = objects.map((object, index) => {
    const name = objectName(object, index);
    const row = document.createElement('div');
    row.className = 'object-row';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = true;
    checkbox.setAttribute('aria-label', `Show ${name}`);
    checkbox.addEventListener('change', () => {
      object.visible = checkbox.checked;
      row.classList.toggle('dimmed', !object.visible);
      if (selected === object) selectionBox.visible = object.visible && $('outline-toggle').checked;
      updateStatus();
      requestRender();
    });
    const button = document.createElement('button');
    button.className = 'object-name';
    button.type = 'button';
    button.textContent = name;
    button.title = `Inspect ${name}`;
    button.addEventListener('click', () => selectObject(object));
    row.append(checkbox, button);
    $('objects').append(row);
    return { row, checkbox, object, search: name.toLowerCase() };
  });
}

function updateStatus() {
  const visible = objects.filter((object) => object.visible).length;
  $('render-status').textContent = visible === objects.length ? `${objects.length} native objects · Ready` : `${visible} / ${objects.length} objects visible`;
}

function populateMetadata(metadata) {
  document.title = `${metadata.board.name} · agent-ee 3D`;
  $('board-name').textContent = metadata.board.name;
  $('source-name').textContent = metadata.board.name;
  $('candidate-id').textContent = metadata.candidate_id || 'Source board';
  $('board-hash').textContent = metadata.board.sha256;
  $('board-path').textContent = metadata.board.path;
  const warnings = metadata.warnings || [];
  warnings.forEach((warning) => {
    const warningItem = document.createElement('li');
    warningItem.textContent = warning;
    $('warnings').append(warningItem);
  });
  $('warnings-section').hidden = warnings.length === 0;
}

function setupInteraction() {
  document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => setCamera(button.dataset.view)));
  $('fit').addEventListener('click', () => setCamera());
  $('clear-selection').addEventListener('click', clearSelection);
  $('show-all').addEventListener('click', () => {
    rows.forEach(({ row, checkbox, object }) => {
      object.visible = true;
      checkbox.checked = true;
      row.classList.remove('dimmed');
    });
    if (selected) selectionBox.visible = $('outline-toggle').checked;
    updateStatus();
    requestRender();
  });
  $('object-search').addEventListener('input', (event) => {
    const query = event.target.value.toLowerCase().trim();
    let count = 0;
    rows.forEach(({ row, search }) => {
      row.hidden = !search.includes(query);
      if (!row.hidden) count++;
    });
    $('object-empty').hidden = count > 0;
  });
  $('grid-toggle').addEventListener('change', (event) => { grid.visible = event.target.checked; requestRender(); });
  $('outline-toggle').addEventListener('change', (event) => { selectionBox.visible = event.target.checked && selected !== null && selected.visible; requestRender(); });
  $('lighting').addEventListener('input', (event) => {
    const percent = Number(event.target.value);
    lights.forEach(({ light, intensity }) => { light.intensity = intensity * percent / 100; });
    scene.environmentIntensity = percent / 100;
    $('lighting-value').textContent = `${percent}%`;
    requestRender();
  });
}

function setupScreenshot() {
  $('screenshot').addEventListener('click', () => {
    positionReferenceGrid();
    renderer.render(scene, camera);
    renderer.domElement.toBlob((blob) => {
      if (!blob) { $('render-status').textContent = 'PNG export failed. Try again.'; return; }
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${metadata.board.name.replace(/[^a-z0-9_-]/gi, '_')}-${view}.png`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      $('render-status').textContent = 'PNG downloaded';
    }, 'image/png');
  });
}

function setupPicking() {
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let down = null;
  renderer.domElement.addEventListener('pointerdown', (event) => { down = { x: event.clientX, y: event.clientY, button: event.button }; });
  renderer.domElement.addEventListener('pointercancel', () => { down = null; });
  renderer.domElement.addEventListener('pointerup', (event) => {
    if (!down || down.button !== 0 || Math.hypot(event.clientX - down.x, event.clientY - down.y) > 5) { down = null; return; }
    down = null;
    const bounds = renderer.domElement.getBoundingClientRect();
    pointer.set((event.clientX - bounds.left) / bounds.width * 2 - 1, -(event.clientY - bounds.top) / bounds.height * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(objects.filter((object) => object.visible), true)[0];
    let object = hit?.object;
    while (object && !objects.includes(object)) object = object.parent;
    if (object) selectObject(object); else clearSelection();
  });
  viewport.addEventListener('keydown', (event) => {
    if (event.key.toLowerCase() === 'f') { event.preventDefault(); setCamera(); }
    if (event.key === 'Escape') clearSelection();
  });
}

async function loadMetadata() {
  const response = await fetch('./viewer.json', { cache: 'no-store' });
  if (!response.ok) throw new Error(`Board metadata could not be loaded (HTTP ${response.status}). Restart the viewer command and reload this page.`);
  metadata = await response.json();
  if (metadata.schema !== 'agent-ee.viewer/1' || !metadata.board || !metadata.model) throw new Error('The board metadata is incomplete or uses an unsupported viewer format. Restart the viewer command.');
  populateMetadata(metadata);
  upAxis = metadata.coordinates?.up === 'Z' ? 'Z' : 'Y';
  document.querySelector('.axis-key').setAttribute('aria-label', `Model coordinates: ${upAxis} up`);
  document.querySelector('.axis-y').textContent = upAxis === 'Y' ? 'Y ↑' : 'Y';
  document.querySelector('.axis-z').textContent = upAxis === 'Z' ? 'Z ↑' : 'Z';
}

function initializeRenderer(OrbitControls) {
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
  } catch (error) {
    throw new Error(`WebGL could not start. Enable hardware acceleration or open this URL in a browser with WebGL support. ${error.message}`);
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;
  renderer.domElement.setAttribute('aria-label', 'Native KiCad 3D board rendering');
  renderer.domElement.setAttribute('role', 'img');
  renderer.domElement.addEventListener('webglcontextlost', (event) => { event.preventDefault(); fail('Graphics context lost', 'The browser stopped the 3D renderer. Reload the viewer to restore it.'); });
  viewport.prepend(renderer.domElement);
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(38, 1, 0.00001, 1000);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.12;
  controls.addEventListener('change', () => {
    if (orbitInteracting) {
      view = 'free';
      document.querySelectorAll('[data-view]').forEach((button) => button.setAttribute('aria-pressed', 'false'));
      $('view-label').textContent = 'FREE ORBIT';
    }
    requestRender();
  });
  controls.addEventListener('start', () => { orbitInteracting = true; });
  controls.addEventListener('end', () => { orbitInteracting = false; });
}

function initializeLighting() {
  const ambient = new THREE.HemisphereLight(0xc5e4f5, 0x687d84, 2.1);
  const key = new THREE.DirectionalLight(0xffffff, 3);
  key.position.set(2, 4, 3);
  const fill = new THREE.DirectionalLight(0xb8d3eb, 1.7);
  fill.position.set(-3, -2, -2);
  [ambient, key, fill].forEach((light) => { lights.push({ light, intensity: light.intensity }); scene.add(light); });
  // A neutral studio environment gives native metallic materials a reflection.
  const environmentScene = new THREE.Scene();
  environmentScene.background = new THREE.Color(0x9099a0);
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(environmentScene).texture;
  pmrem.dispose();
}

async function loadModel(GLTFLoader) {
  const loader = new GLTFLoader();
  const modelURL = new URL(metadata.model, location.href);
  if (modelURL.origin !== location.origin) throw new Error('The board model must be served by this local viewer.');
  const gltf = await loader.loadAsync(modelURL.href, (event) => {
    if (event.total) $('state-detail').textContent = `Loading native geometry · ${Math.round(event.loaded / event.total * 100)}%`;
  });
  return gltf;
}

function prepareBoard(gltf) {
  board = gltf.scene;
  board.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(board);
  const size = box.getSize(new THREE.Vector3());
  if (box.isEmpty() || !Number.isFinite(size.length()) || size.length() === 0) throw new Error('This export contains no visible board geometry. Check the board outline and KiCad export notes.');
  const center = box.getCenter(new THREE.Vector3());
  board.position.sub(center);
  board.updateMatrixWorld(true);
  scene.add(board);
  board.traverse((object) => { if (object.isMesh) meshes.push(object); });
  if (meshes.length === 0) throw new Error('This export contains no board meshes. Check the KiCad export notes.');
  const seenNodes = new Set();
  board.traverse((object) => {
    const nodeIndex = gltf.parser.associations.get(object)?.nodes;
    if (nodeIndex === undefined || seenNodes.has(nodeIndex)) return;
    if (gltf.parser.json.nodes[nodeIndex].mesh === undefined) return;
    objects.push(object);
    seenNodes.add(nodeIndex);
  });
  boardRadius = size.length() / 2;
  camera.near = boardRadius / 1000;
  camera.far = boardRadius * 1000;
  camera.updateProjectionMatrix();
  controls.minDistance = boardRadius / 20;
  controls.maxDistance = boardRadius * 50;
  return size;
}

function createSceneHelpers(size) {
  grid = new THREE.GridHelper(boardRadius * 4, 20, 0x425a65, 0x2e414b);
  if (upAxis === 'Z') {
    grid.rotation.x = Math.PI / 2;
    grid.position.z = -size.z / 2 - boardRadius * 0.012;
  } else grid.position.y = -size.y / 2 - boardRadius * 0.012;
  grid.material.transparent = true;
  grid.material.opacity = 0.5;
  scene.add(grid);
  selectionBox = new THREE.BoxHelper(undefined, 0x7cf8ce);
  selectionBox.visible = false;
  selectionBox.material.depthTest = false;
  selectionBox.material.transparent = true;
  selectionBox.material.opacity = 0.9;
  selectionBox.renderOrder = 2;
  scene.add(selectionBox);
}

async function start() {
  await loadMetadata();
  const modules = await Promise.all([import('three'), import('./vendor/GLTFLoader.js'), import('./vendor/OrbitControls.js')]);
  THREE = modules[0];
  initializeRenderer(modules[2].OrbitControls);
  initializeLighting();
  const gltf = await loadModel(modules[1].GLTFLoader);
  const size = prepareBoard(gltf);
  createSceneHelpers(size);
  $('mesh-count').textContent = String(meshes.length);
  const dimensions = upAxis === 'Z' ? [size.x, size.y, size.z] : [size.x, size.z, size.y];
  $('model-size').textContent = metadata.coordinates?.units === 'm'
    ? `${dimensions.map((value) => (value * 1000).toFixed(1)).join(' × ')} mm`
    : 'Native export units';
  buildObjectList();
  setupScreenshot();
  setupPicking();
  setupInteraction();
  document.querySelectorAll(enabledControls).forEach((element) => { element.disabled = false; });
  $('state-panel').hidden = true;
  resize();
  setCamera('iso');
  updateStatus();
  $('render-status').dataset.ready = 'true';
  new ResizeObserver(resize).observe(viewport);
}

start().catch((error) => {
  console.error('agent-ee viewer:', error);
  fail('Unable to load the 3D board', error.message || 'The native model could not be loaded. Check the viewer command output and reload.');
});
