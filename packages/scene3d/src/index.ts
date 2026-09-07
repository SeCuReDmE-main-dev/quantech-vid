import * as THREE from "three";
import { z } from "zod";

export const SYNTHETIC_LABEL = "Synthetic representation";
export const AVATAR_LABEL = "Synthetic avatar - no real person";
export const BRAND = Object.freeze({ navy: "#071A2B", teal: "#14B8A6", gold: "#D4A72C" });

export const SceneConfigSchema = z.object({
  kind: z.enum(["title", "diagram", "annotated-object", "comparison", "code", "presentation", "synthetic-avatar"]),
  title: z.string().min(1).max(200),
  lines: z.array(z.string().min(1).max(200)).max(8).default([]),
  accent: z.string().regex(/^#[0-9A-Fa-f]{6}$/).default(BRAND.teal),
  animation: z.enum(["none", "spin", "pulse"]).default("none"),
  duration: z.number().finite().gt(0).max(60),
}).strict();

export const SceneDimensionsSchema = z.object({
  width: z.number().int().min(320).max(1920),
  height: z.number().int().min(320).max(1920),
}).strict().refine(({ width, height }) => width * height <= 2_073_600, {
  message: "total pixels must not exceed 2073600",
});

export type SceneConfig = z.infer<typeof SceneConfigSchema>;
export type SceneConfigInput = z.input<typeof SceneConfigSchema>;
export type SceneDimensions = z.infer<typeof SceneDimensionsSchema>;

type Disposable = { dispose(): void };

export interface SceneGraph {
  readonly scene: THREE.Scene;
  readonly camera: THREE.PerspectiveCamera;
  readonly root: THREE.Group;
  readonly objectCount: number;
  readonly geometryCount: number;
  readonly disposed: boolean;
  renderAt(seconds: number): void;
  dispose(): void;
}

export interface SceneRenderer {
  readonly graph: SceneGraph;
  readonly renderer: THREE.WebGLRenderer;
  readonly disposed: boolean;
  renderAt(seconds: number): void;
  resize(dimensions: SceneDimensions): void;
  dispose(): void;
}

function labelTexture(text: string, foreground = "#F8FAFC", background = BRAND.navy): THREE.Texture {
  if (typeof document !== "undefined") {
    const canvas = document.createElement("canvas");
    canvas.width = 1024;
    canvas.height = 128;
    const context = canvas.getContext("2d");
    if (context) {
      context.fillStyle = background;
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.fillStyle = foreground;
      context.font = "600 42px system-ui, sans-serif";
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(text.slice(0, 200), canvas.width / 2, canvas.height / 2, 960);
    }
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    return texture;
  }
  const color = new THREE.Color(background);
  const data = new Uint8Array([
    Math.round(color.r * 255), Math.round(color.g * 255), Math.round(color.b * 255), 255,
  ]);
  const texture = new THREE.DataTexture(data, 1, 1);
  texture.needsUpdate = true;
  return texture;
}

function planeLabel(text: string, width: number, height: number, y: number,
                    resources: Set<Disposable>, foreground?: string): THREE.Mesh {
  const geometry = new THREE.PlaneGeometry(width, height);
  const texture = labelTexture(text, foreground);
  const material = new THREE.MeshBasicMaterial({ map: texture, transparent: false });
  resources.add(geometry);
  resources.add(texture);
  resources.add(material);
  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = "local-canvas-label";
  mesh.userData.label = text;
  mesh.position.set(0, y, 0.08);
  return mesh;
}

function box(x: number, y: number, width: number, height: number, color: string,
             resources: Set<Disposable>, depth = 0.18): THREE.Mesh {
  const geometry = new THREE.BoxGeometry(width, height, depth);
  const material = new THREE.MeshStandardMaterial({ color, roughness: 0.55, metalness: 0.12 });
  resources.add(geometry);
  resources.add(material);
  const mesh = new THREE.Mesh(geometry, material);
  mesh.position.set(x, y, 0);
  return mesh;
}

function sphere(x: number, y: number, radius: number, color: string,
                resources: Set<Disposable>): THREE.Mesh {
  const geometry = new THREE.SphereGeometry(radius, 16, 12);
  const material = new THREE.MeshStandardMaterial({ color, roughness: 0.45 });
  resources.add(geometry);
  resources.add(material);
  const mesh = new THREE.Mesh(geometry, material);
  mesh.position.set(x, y, 0);
  return mesh;
}

function addTitle(root: THREE.Group, config: SceneConfig, resources: Set<Disposable>): void {
  root.add(box(0, 0, 8.2, 4.6, BRAND.navy, resources, 0.12));
  root.add(planeLabel(config.title, 7.2, 0.9, 0.55, resources, config.accent));
  root.add(planeLabel(SYNTHETIC_LABEL, 4.8, 0.45, -1.55, resources, BRAND.gold));
  const ringGeometry = new THREE.TorusGeometry(1.15, 0.08, 12, 48);
  const ringMaterial = new THREE.MeshStandardMaterial({ color: BRAND.gold });
  resources.add(ringGeometry);
  resources.add(ringMaterial);
  const ring = new THREE.Mesh(ringGeometry, ringMaterial);
  ring.position.set(0, -0.55, 0.25);
  root.add(ring);
  config.lines.forEach((line, index) => {
    const label = planeLabel(line, 3.0, 0.36, 0.15 - Math.floor(index / 2) * 0.45, resources);
    label.position.set(index % 2 ? 2.0 : -2.0, label.position.y, 0.5);
    root.add(label);
  });
}

function addDiagram(root: THREE.Group, config: SceneConfig, resources: Set<Disposable>): void {
  const count = Math.max(3, Math.min(8, config.lines.length || 4));
  for (let index = 0; index < count; index += 1) {
    const angle = (index / count) * Math.PI * 2;
    const x = Math.cos(angle) * 2.5, y = Math.sin(angle) * 0.85;
    root.add(sphere(x, y, 0.36,
                    index % 2 ? config.accent : BRAND.gold, resources));
    const label = planeLabel(config.lines[index] ?? `Node ${index + 1}`, 2.0, 0.38, y + 0.5, resources);
    label.position.set(x, y + 0.5, 0.6);
    root.add(label);
  }
  root.add(sphere(0, 0, 0.62, BRAND.teal, resources));
  root.add(planeLabel(config.title, 6.5, 0.65, 2.25, resources));
  root.add(planeLabel(SYNTHETIC_LABEL, 4.5, 0.38, -2.15, resources, BRAND.gold));
}

function addAnnotatedObject(root: THREE.Group, config: SceneConfig,
                            resources: Set<Disposable>): void {
  const geometry = new THREE.IcosahedronGeometry(1.35, 1);
  const material = new THREE.MeshStandardMaterial({ color: config.accent, flatShading: true });
  resources.add(geometry);
  resources.add(material);
  root.add(new THREE.Mesh(geometry, material));
  const lines = config.lines;
  lines.forEach((line, index) => {
    const side = index % 2 ? 1 : -1;
    const label = planeLabel(line, 2.6, 0.42, 1.25 - Math.floor(index / 2) * 0.65, resources);
    label.position.x = side * 2.6;
    root.add(label);
  });
  root.add(planeLabel(config.title, 6.5, 0.65, 2.35, resources));
  root.add(planeLabel(SYNTHETIC_LABEL, 4.5, 0.38, -2.25, resources, BRAND.gold));
}

function addComparison(root: THREE.Group, config: SceneConfig, resources: Set<Disposable>): void {
  root.add(box(-2.15, 0, 3.6, 3.3, BRAND.teal, resources));
  root.add(box(2.15, 0, 3.6, 3.3, config.accent, resources));
  (config.lines.length ? config.lines : ["Option A", "Option B"]).forEach((line, index) => {
    const label = planeLabel(line, 3.1, 0.42, 0.9 - Math.floor(index / 2) * 0.58, resources);
    label.position.set(index % 2 ? 2.15 : -2.15, label.position.y, 0.2);
    root.add(label);
  });
  root.add(planeLabel(config.title, 6.5, 0.65, 2.35, resources));
  root.add(planeLabel(SYNTHETIC_LABEL, 4.5, 0.38, -2.25, resources, BRAND.gold));
}

function addCode(root: THREE.Group, config: SceneConfig, resources: Set<Disposable>): void {
  root.add(box(0, -0.1, 7.4, 4.1, "#0B1220", resources, 0.12));
  config.lines.slice(0, 8).forEach((line, index) => {
    root.add(planeLabel(line, 6.7, 0.36, 1.45 - index * 0.43, resources,
                        index % 2 ? "#E2E8F0" : config.accent));
  });
  root.add(planeLabel(config.title, 6.5, 0.62, 2.35, resources));
  root.add(planeLabel(SYNTHETIC_LABEL, 4.5, 0.36, -2.25, resources, BRAND.gold));
}

function addPresentation(root: THREE.Group, config: SceneConfig,
                         resources: Set<Disposable>): void {
  root.add(box(0, -1.65, 8.2, 0.4, BRAND.gold, resources));
  const cards = Math.max(1, config.lines.length || 3);
  for (let index = 0; index < cards; index += 1) {
    const columns = Math.min(4, cards), x = ((index % 4) - (columns - 1) / 2) * 1.85;
    const y = cards > 4 ? 0.7 - Math.floor(index / 4) * 1.5 : 0;
    root.add(box(x, y, 1.5, cards > 4 ? 1.3 : 2.2,
                 index % 2 ? BRAND.teal : config.accent, resources));
    const label = planeLabel(config.lines[index] ?? `Card ${index + 1}`, 1.4, 0.4, y, resources);
    label.position.set(x, y, 0.2); root.add(label);
  }
  root.add(planeLabel(config.title, 6.5, 0.7, 2.25, resources));
  root.add(planeLabel(SYNTHETIC_LABEL, 4.5, 0.38, -2.3, resources, BRAND.gold));
}

function addSyntheticAvatar(root: THREE.Group, config: SceneConfig,
                            resources: Set<Disposable>): void {
  // Original geometric fixture: no scan, face, external asset, or biometric input.
  root.add(sphere(0, 0.9, 0.48, config.accent, resources));
  root.add(box(0, -0.05, 0.86, 1.05, config.accent, resources, 0.4));
  for (const side of [-1, 1]) {
    root.add(box(side * 0.64, -0.12, 0.28, 0.95, BRAND.gold, resources, 0.28));
    root.add(box(side * 0.25, -0.96, 0.32, 0.7, config.accent, resources, 0.3));
    const eye = sphere(side * 0.16, 0.99, 0.07, BRAND.navy, resources);
    eye.position.z = 0.43;
    root.add(eye);
  }
  root.add(planeLabel(config.title, 6.5, 0.6, 2.1, resources));
  root.add(planeLabel(SYNTHETIC_LABEL, 4.5, 0.36, -1.67, resources, BRAND.gold));
  config.lines.forEach((line, index) => {
    const label = planeLabel(line, 2.35, 0.38, 1.2 - Math.floor(index / 2) * 0.58, resources);
    label.position.x = index % 2 ? 2.55 : -2.55;
    root.add(label);
  });
}

const BUILDERS = {
  title: addTitle,
  diagram: addDiagram,
  "annotated-object": addAnnotatedObject,
  comparison: addComparison,
  code: addCode,
  presentation: addPresentation,
  "synthetic-avatar": addSyntheticAvatar,
} satisfies Record<SceneConfig["kind"],
  (root: THREE.Group, config: SceneConfig, resources: Set<Disposable>) => void>;

export function createSceneGraph(input: SceneConfigInput): SceneGraph {
  const config = SceneConfigSchema.parse(input);
  const resources = new Set<Disposable>();
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(BRAND.navy);
  const camera = new THREE.PerspectiveCamera(42, 16 / 9, 0.1, 100);
  camera.position.set(0, 0, 10.5);
  const root = new THREE.Group();
  root.name = `quantech-${config.kind}`;
  scene.add(root);
  scene.add(new THREE.AmbientLight("#FFFFFF", 1.8));
  const key = new THREE.DirectionalLight("#FFFFFF", 2.2);
  key.position.set(3, 5, 7);
  scene.add(key);
  BUILDERS[config.kind](root, config, resources);
  if (config.kind === "synthetic-avatar") {
    // Disclosure remains facing the camera even when the avatar root rotates.
    const disclosure = planeLabel(AVATAR_LABEL, 6.4, 0.42, 1.65, resources, BRAND.gold);
    disclosure.name = "persistent-avatar-disclosure";
    scene.add(disclosure);
  }
  const objectCount = (() => { let count = 0; scene.traverse(() => { count += 1; }); return count; })();
  const geometryCount = [...resources].filter((value) => value instanceof THREE.BufferGeometry).length;
  if (objectCount > 64 || geometryCount > 32) {
    resources.forEach((resource) => resource.dispose());
    throw new Error("scene resource bound exceeded");
  }
  let disposed = false;
  const renderAt = (seconds: number): void => {
    if (disposed) throw new Error("scene graph is disposed");
    if (!Number.isFinite(seconds) || seconds < 0 || seconds > config.duration) {
      throw new RangeError("seconds must be finite and within the configured duration");
    }
    const phase = seconds / config.duration;
    root.rotation.set(0, config.animation === "spin" ? (phase * Math.PI * 2) % (Math.PI * 2) : 0, 0);
    const scale = config.animation === "pulse" ? 1 + Math.sin(phase * Math.PI * 2) * 0.04 : 1;
    root.scale.setScalar(scale);
  };
  renderAt(0);
  return {
    scene, camera, root, objectCount, geometryCount, renderAt,
    dispose(): void {
      if (disposed) return;
      disposed = true;
      resources.forEach((resource) => resource.dispose());
      root.clear();
      scene.clear();
    },
    get disposed(): boolean { return disposed; },
  };
}

export function createSceneRenderer(canvas: HTMLCanvasElement, input: SceneConfigInput,
                                    dimensionsInput: SceneDimensions): SceneRenderer {
  if (!canvas || typeof canvas.getContext !== "function") {
    throw new TypeError("a canvas element is required");
  }
  const initialDimensions = SceneDimensionsSchema.parse(dimensionsInput);
  const graph = createSceneGraph(input);
  let renderer: THREE.WebGLRenderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
  } catch (error) {
    graph.dispose();
    throw error;
  }
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  let disposed = false;
  const resize = (next: SceneDimensions): void => {
    if (disposed) throw new Error("scene renderer is disposed");
    const dimensions = SceneDimensionsSchema.parse(next);
    renderer.setPixelRatio(1);
    renderer.setSize(dimensions.width, dimensions.height, false);
    graph.camera.aspect = dimensions.width / dimensions.height;
    graph.camera.updateProjectionMatrix();
  };
  resize(initialDimensions);
  return {
    graph, renderer,
    renderAt(seconds: number): void {
      if (disposed) throw new Error("scene renderer is disposed");
      graph.renderAt(seconds);
      renderer.render(graph.scene, graph.camera);
    },
    resize,
    dispose(): void {
      if (disposed) return;
      disposed = true;
      graph.dispose();
      renderer.dispose();
    },
    get disposed(): boolean { return disposed; },
  };
}
