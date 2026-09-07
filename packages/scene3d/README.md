# `@quantech/scene3d`

A deterministic Three.js scene module for QuaNTecH-ViD. It builds six procedural templates: `title`, `diagram`, `annotated-object`, `comparison`, `code`, and `presentation`. Every template carries the visible label **Synthetic representation** and uses the SecuredMe navy, teal, and gold palette.

This package contains no human avatar, GLB/VRM loader, remote font, URL asset, network request, request-animation loop, provider call, or hidden timer. Text is drawn into a local `CanvasTexture` in browsers. The Node graph tests use a one-pixel `DataTexture` fallback because they do not claim to exercise WebGL or browser typography.

## API

```ts
import { createSceneGraph, createSceneRenderer } from "@quantech/scene3d";

const config = {
  kind: "diagram",
  title: "A bounded visual explanation",
  lines: ["Input", "Review", "Approved output"],
  accent: "#14B8A6",
  animation: "pulse",
  duration: 8,
};

const graph = createSceneGraph(config);
graph.renderAt(2.5);
graph.dispose();

const view = createSceneRenderer(canvas, config, { width: 1280, height: 720 });
view.renderAt(2.5); // the caller owns the clock and capture loop
view.resize({ width: 960, height: 540 });
view.dispose();
```

`renderAt` accepts only finite times from zero through the configured duration. Animation state is calculated directly from that time, so `t0 → t1 → t0` returns to the same state. Graphs are limited to 64 Three objects and 32 geometries. Output dimensions are 320–1920 per axis and at most 2,073,600 pixels.

The ESM build and declarations are `dist/index.js` and `dist/index.d.ts`. The standalone browser IIFE is `dist/browser.js` and exposes `globalThis.QuaNTechScene3D` when loaded as a classic script. Three.js is bundled into that artifact; it is not fetched at runtime.

## Limits

- These templates are procedural visual aids, not measurements or evidence.
- The synthetic label does not validate any statement in the scene.
- Source admission, evidence, provenance, rights and human render approval remain backend responsibilities.
- Node tests validate graph construction, deterministic state and disposal. Real WebGL pixels require a browser visual test.

## Sources and license

Implementation follows the official Three.js [Creating a scene](https://threejs.org/manual/en/creating-a-scene.html) manual and [API documentation](https://threejs.org/docs/). Three.js is MIT licensed; see its official [LICENSE](https://github.com/mrdoob/three.js/blob/dev/LICENSE). Dependency versions are pinned in `package-lock.json`, and esbuild bundles the applicable license notice into `dist/browser.js.LEGAL.txt`.
