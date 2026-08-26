import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function readJson(path) { return JSON.parse(await readFile(new URL(path, import.meta.url), "utf8")); }

test("extension is loopback-only and sends captures to the studio", async () => {
  const manifest = await readJson("../manifest.json");
  const config = await readFile(new URL("../config.js", import.meta.url), "utf8");
  const background = await readFile(new URL("../background.js", import.meta.url), "utf8");
  const popup = await readFile(new URL("../popup.js", import.meta.url), "utf8");
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.host_permissions, ["http://127.0.0.1:7476/*"]);
  assert.ok(manifest.permissions.includes("contextMenus"));
  assert.match(config, /http:\/\/127\.0\.0\.1:7476/);
  assert.match(background, /API_BASE_URL.*captures/);
  assert.match(background, /capture_page/);
  assert.match(popup, /studio_health/);
  assert.doesNotMatch(background, /github|oauth|subscription/i);
});
