import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

import {
  BRAND,
  SYNTHETIC_LABEL,
  SceneConfigSchema,
  SceneDimensionsSchema,
  createSceneGraph,
} from "../dist/index.js";

const kinds = ["title", "diagram", "annotated-object", "comparison", "code", "presentation"];

function config(kind, overrides = {}) {
  return {
    kind,
    title: `Bounded ${kind}`,
    lines: ["First local line", "Second local line"],
    accent: BRAND.teal,
    animation: "spin",
    duration: 4,
    ...overrides,
  };
}

test("all six templates build real bounded Three.js graphs with synthetic labels", () => {
  for (const kind of kinds) {
    const graph = createSceneGraph(config(kind));
    const labels = [];
    graph.scene.traverse((object) => {
      if (object.userData.label) labels.push(object.userData.label);
    });
    assert.equal(graph.root.name, `quantech-${kind}`);
    assert.ok(graph.objectCount > 4 && graph.objectCount <= 64);
    assert.ok(graph.geometryCount > 0 && graph.geometryCount <= 32);
    assert.ok(labels.includes(SYNTHETIC_LABEL));
    for (const line of config(kind).lines) assert.ok(labels.includes(line), `${kind} must not silently discard editable labels`);
    graph.dispose();
  }
});

test("every template retains all eight accepted labels within the resource budget", () => {
  const lines = Array.from({length: 8}, (_, i) => `Label ${i + 1}`);
  for (const kind of kinds) {
    const graph = createSceneGraph(config(kind, {lines}));
    const actual = [];
    graph.scene.traverse(object => { if (object.userData.label) actual.push(object.userData.label); });
    for (const line of lines) assert.ok(actual.includes(line));
    assert.ok(graph.geometryCount <= 32 && graph.objectCount <= 64);
    graph.dispose();
  }
});

test("animation state is deterministic for t0, t1, t0", () => {
  for (const animation of ["none", "spin", "pulse"]) {
    const graph = createSceneGraph(config("annotated-object", { animation }));
    graph.renderAt(0);
    const initial = [...graph.root.rotation.toArray(), ...graph.root.scale.toArray()];
    graph.renderAt(1);
    const advanced = [...graph.root.rotation.toArray(), ...graph.root.scale.toArray()];
    graph.renderAt(0);
    assert.deepEqual([...graph.root.rotation.toArray(), ...graph.root.scale.toArray()], initial);
    if (animation !== "none") assert.notDeepEqual(advanced, initial);
    graph.dispose();
  }
});

test("configuration, dimensions and explicit render time fail closed", () => {
  assert.throws(() => SceneConfigSchema.parse({ ...config("title"), unknown: true }));
  assert.throws(() => createSceneGraph(config("title", { accent: "teal" })));
  assert.throws(() => createSceneGraph(config("title", { duration: 0 })));
  assert.throws(() => createSceneGraph(config("title", { title: "x".repeat(201) })));
  assert.throws(() => createSceneGraph(config("title", { lines: Array(9).fill("line") })));
  assert.throws(() => createSceneGraph(config("title", { lines: ["x".repeat(201)] })));
  assert.deepEqual(SceneDimensionsSchema.parse({ width: 1920, height: 1080 }),
                   { width: 1920, height: 1080 });
  assert.throws(() => SceneDimensionsSchema.parse({ width: 1920, height: 1200 }));
  assert.throws(() => SceneDimensionsSchema.parse({ width: 319, height: 1080 }));
  const graph = createSceneGraph(config("title"));
  for (const invalid of [-1, 4.01, Number.NaN, Number.POSITIVE_INFINITY]) {
    assert.throws(() => graph.renderAt(invalid));
  }
  graph.dispose();
  assert.throws(() => graph.renderAt(0), /disposed/);
});

test("dispose releases every graph geometry, material and local texture exactly once", () => {
  const graph = createSceneGraph(config("code", { lines: Array(8).fill("const safe = true;") }));
  const resources = new Set();
  graph.scene.traverse((object) => {
    if (object.geometry) resources.add(object.geometry);
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    for (const material of materials) {
      if (!material) continue;
      resources.add(material);
      if (material.map) resources.add(material.map);
    }
  });
  let disposed = 0;
  for (const resource of resources) resource.addEventListener("dispose", () => { disposed += 1; });
  graph.dispose();
  assert.equal(graph.disposed, true);
  assert.equal(disposed, resources.size);
  graph.dispose();
  assert.equal(disposed, resources.size);
});

test("browser artifact is a standalone IIFE with the requested public API", () => {
  const browser = fs.readFileSync(new URL("../dist/browser.js", import.meta.url), "utf8");
  assert.match(browser, /var QuaNTechScene3D/);
  assert.match(browser, /createSceneGraph/);
  assert.match(browser, /createSceneRenderer/);
  assert.ok(browser.length > 100_000, "Three.js should be bundled rather than loaded remotely");
});
