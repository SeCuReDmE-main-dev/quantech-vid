import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);
async function text(name) { return readFile(new URL(name, root), "utf8"); }
async function json(name) { return JSON.parse(await text(name)); }

test("Chromium manifest has only the visible companion permissions", async () => {
  const manifest = await json("manifest.json");
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions, ["contextMenus", "sidePanel"]);
  assert.equal(manifest.host_permissions, undefined);
  assert.equal(manifest.background.service_worker, "background.js");
  assert.equal(manifest.background.scripts, undefined);
  assert.equal(manifest.side_panel.default_path, "popup.html");
  assert.equal(manifest.action.default_popup, "popup.html");
  assert.equal(manifest.incognito, "not_allowed");
});

test("Firefox manifest uses an event page, sidebar, signing id, and honest data declaration", async () => {
  const manifest = await json("manifest.firefox.json");
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions, ["contextMenus"]);
  assert.equal(manifest.host_permissions, undefined);
  assert.deepEqual(manifest.background, { scripts: ["background.js"] });
  assert.equal(manifest.sidebar_action.default_panel, "popup.html");
  assert.match(manifest.browser_specific_settings.gecko.id, /@/);
  assert.deepEqual(manifest.browser_specific_settings.gecko.data_collection_permissions.required,
    ["websiteContent"]);
});

test("legacy capture, network, iframe, token, persistent storage, and page scanning paths are absent", async () => {
  const files = await Promise.all(["config.js", "browser-api.js", "companion-core.js", "background.js", "popup.js", "popup.html"].map(text));
  const joined = files.join("\n");
  assert.match(joined, /http:\/\/127\.0\.0\.1:7476\//);
  assert.doesNotMatch(joined, /api\/v1|captures|capturePage|fetch\s*\(|host_permissions|storage\.(?:local|sync|session)|authorization|bearer|token/i);
  assert.doesNotMatch(joined, /<iframe|innerHTML|outerHTML|insertAdjacentHTML/i);
  assert.doesNotMatch(joined, /pageUrl|frameUrl|linkUrl|srcUrl|tab\.url|document\.body|querySelectorAll/i);
  assert.match(joined, /selectionText/);
  assert.match(joined, /textContent/);
  assert.match(joined, /engines: 'not_connected'/);
});

test("package scripts produce distinct Chrome, Edge, and Firefox artifacts", async () => {
  const pkg = await json("package.json");
  for (const browser of ["chrome", "edge", "firefox"]) {
    assert.match(pkg.scripts[`build:${browser}`], new RegExp(`browser=${browser}`));
    assert.match(pkg.scripts[`package:${browser}`], new RegExp(`releases/${browser}`));
  }
});
