import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const SIZE = 1024;
const CELL = SIZE / 2;
const MAX_OBJECTS = 512;
const MAX_GEOMETRIES = 128;
const MAX_MATERIALS = 64;
const MAX_VERTICES = 200_000;
const MAX_TRIANGLES = 400_000;
const MAX_FLOAT_MAGNITUDE = 1_000_000;
const ANGLES = Object.freeze(["front", "right", "back", "left"] as const);

type Disposable = { dispose(): void };

export interface ModelPosterStats {
  readonly width: 1024;
  readonly height: 1024;
  readonly angles: readonly ["front", "right", "back", "left"];
  readonly objectCount: number;
  readonly geometryCount: number;
  readonly materialCount: number;
  readonly vertexCount: number;
  readonly triangleCount: number;
}

let activeResources: Set<Disposable> | null = null;
let activeRenderer: THREE.WebGLRenderer | null = null;

function fail(): never {
  throw new Error("MODEL3D_POSTER_RUNTIME_INVALID");
}

function finite(values: ArrayLike<number>): void {
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (value === undefined || !Number.isFinite(value) || Math.abs(value) > MAX_FLOAT_MAGNITUDE) fail();
  }
}

function decodeBase64(value: string): ArrayBuffer {
  if (typeof value !== "string" || value.length < 1 || value.length > 26_666_672
      || !/^[A-Za-z0-9+/]*={0,2}$/.test(value)) fail();
  const decoded = atob(value);
  if (decoded.length < 20 || decoded.length > 20_000_000) fail();
  const bytes = new Uint8Array(decoded.length);
  for (let index = 0; index < decoded.length; index += 1) bytes[index] = decoded.charCodeAt(index);
  return bytes.buffer;
}

function materialsFor(mesh: THREE.Mesh): THREE.Material[] {
  const value = mesh.material;
  return Array.isArray(value) ? value : [value];
}

function validateRuntimeGraph(scene: THREE.Object3D, animations: readonly THREE.AnimationClip[]): ModelPosterStats {
  if (animations.length !== 0) fail();
  let objectCount = 0;
  let vertexCount = 0;
  let triangleCount = 0;
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  scene.traverse((object) => {
    objectCount += 1;
    if (objectCount > MAX_OBJECTS || object instanceof THREE.Camera || object instanceof THREE.Light
        || object instanceof THREE.SkinnedMesh) fail();
    finite(object.position.toArray());
    finite(object.quaternion.toArray());
    finite(object.scale.toArray());
    finite(object.matrix.elements);
    if (!(object instanceof THREE.Mesh)) return;
    if (Object.keys(object.morphTargetInfluences ?? {}).length !== 0) fail();
    const geometry = object.geometry;
    if (!(geometry instanceof THREE.BufferGeometry) || Object.keys(geometry.morphAttributes).length !== 0) fail();
    geometries.add(geometry);
    if (geometries.size > MAX_GEOMETRIES) fail();
    const position = geometry.getAttribute("position");
    if (!(position instanceof THREE.BufferAttribute) || position.itemSize !== 3 || position.normalized) fail();
    finite(position.array);
    vertexCount += position.count;
    if (vertexCount > MAX_VERTICES) fail();
    const index = geometry.getIndex();
    if (index !== null) {
      if (index.itemSize !== 1 || index.normalized || index.count % 3 !== 0) fail();
      for (let item = 0; item < index.count; item += 1) {
        const value = index.getX(item);
        if (!Number.isInteger(value) || value < 0 || value >= position.count) fail();
      }
      triangleCount += index.count / 3;
    } else {
      if (position.count % 3 !== 0) fail();
      triangleCount += position.count / 3;
    }
    if (triangleCount > MAX_TRIANGLES) fail();
    for (const material of materialsFor(object)) {
      if (!(material instanceof THREE.Material)) fail();
      materials.add(material);
      if (materials.size > MAX_MATERIALS) fail();
      for (const value of Object.values(material)) {
        if (value instanceof THREE.Texture) fail();
      }
    }
  });
  if (objectCount < 2 || geometries.size < 1 || vertexCount < 3 || triangleCount < 1) fail();
  return Object.freeze({
    width: SIZE, height: SIZE, angles: ANGLES,
    objectCount, geometryCount: geometries.size, materialCount: materials.size,
    vertexCount, triangleCount,
  });
}

function disposeScene(scene: THREE.Object3D, resources: Set<Disposable>): void {
  scene.traverse((object) => {
    if (!(object instanceof THREE.Mesh)) return;
    resources.add(object.geometry);
    for (const material of materialsFor(object)) {
      resources.add(material);
      for (const value of Object.values(material)) {
        if (value instanceof THREE.Texture) resources.add(value);
      }
    }
  });
}

export function disposeModelPoster(): void {
  if (activeResources !== null) {
    for (const resource of activeResources) resource.dispose();
    activeResources.clear();
  }
  if (activeRenderer !== null) {
    activeRenderer.dispose();
    activeRenderer.forceContextLoss();
  }
  activeResources = null;
  activeRenderer = null;
}

export async function renderModelPoster(canvas: HTMLCanvasElement, payloadBase64: string): Promise<ModelPosterStats> {
  disposeModelPoster();
  if (!(canvas instanceof HTMLCanvasElement)) fail();
  canvas.width = SIZE;
  canvas.height = SIZE;
  const manager = new THREE.LoadingManager();
  manager.setURLModifier(() => { throw new Error("MODEL3D_POSTER_EXTERNAL_RESOURCE"); });
  const loader = new GLTFLoader(manager);
  const gltf = await loader.parseAsync(decodeBase64(payloadBase64), "");
  if (gltf.scenes.length !== 1 || gltf.scene !== gltf.scenes[0]) fail();
  const resources = new Set<Disposable>();
  disposeScene(gltf.scene, resources);
  activeResources = resources;
  const stats = validateRuntimeGraph(gltf.scene, gltf.animations);

  const bounds = new THREE.Box3().setFromObject(gltf.scene);
  if (bounds.isEmpty()) fail();
  finite(bounds.min.toArray());
  finite(bounds.max.toArray());
  const center = bounds.getCenter(new THREE.Vector3());
  const size = bounds.getSize(new THREE.Vector3());
  const radius = size.length() / 2;
  if (!Number.isFinite(radius) || radius <= 1e-9 || radius > MAX_FLOAT_MAGNITUDE) fail();

  const display = new THREE.Group();
  gltf.scene.position.sub(center);
  display.add(gltf.scene);
  display.scale.setScalar(2.7 / radius);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color("#071A2B");
  scene.add(display);
  scene.add(new THREE.HemisphereLight("#FFFFFF", "#0B2538", 2.0));
  const light = new THREE.DirectionalLight("#FFFFFF", 2.4);
  light.position.set(3, 5, 4);
  scene.add(light);

  const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: false, preserveDrawingBuffer: true });
  activeRenderer = renderer;
  renderer.setPixelRatio(1);
  renderer.setSize(SIZE, SIZE, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1;
  renderer.setScissorTest(true);
  const azimuths = [0, Math.PI / 2, Math.PI, -Math.PI / 2];
  for (const [index, azimuth] of azimuths.entries()) {
    camera.position.set(Math.sin(azimuth) * 8, 2.1, Math.cos(azimuth) * 8);
    camera.lookAt(0, 0, 0);
    camera.updateMatrixWorld(true);
    const x = (index % 2) * CELL;
    const y = index < 2 ? CELL : 0;
    renderer.setViewport(x, y, CELL, CELL);
    renderer.setScissor(x, y, CELL, CELL);
    renderer.setClearColor(index % 2 ? "#0B2538" : "#071A2B", 1);
    renderer.clear(true, true, true);
    renderer.render(scene, camera);
  }
  renderer.setScissorTest(false);
  return stats;
}
