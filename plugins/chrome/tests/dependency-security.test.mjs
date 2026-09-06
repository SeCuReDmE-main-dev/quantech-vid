import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const root = new URL("../", import.meta.url);
const linterRequire = createRequire(require.resolve("addons-linter"));
const parserPath = linterRequire.resolve("image-size");

test("manifest and lockfile retain the reviewed build toolchain", () => {
  const pkg = JSON.parse(readFileSync(new URL("package.json", root)));
  const lock = JSON.parse(readFileSync(new URL("package-lock.json", root)));
  assert.deepEqual(lock.packages[""].devDependencies, pkg.devDependencies);
  assert.deepEqual(lock.packages[""].engines, pkg.engines);
  assert.equal(pkg.packageManager, "npm@11.16.0");
  assert.equal(readFileSync(new URL("../../.node-version", root), "utf8").trim(), "24.18.1");
  assert.equal(pkg.overrides["image-size"], "npm:image-size-next@2.1.1");
  const parsers = Object.entries(lock.packages).filter(([path]) => path.endsWith("/image-size"));
  assert.ok(parsers.length > 0);
  for (const [, parser] of parsers) {
    assert.equal(parser.name, "image-size-next");
    assert.equal(parser.version, "2.1.1");
    assert.ok(parser.integrity.startsWith("sha512-"));
  }
});

test("Mozilla linter resolves the reviewed parser and reads the shipped PNG icon", () => {
  const { imageSize } = linterRequire("image-size");
  const size = imageSize(readFileSync(new URL("icons/icon128.png", root)));
  assert.equal(size.width, 128);
  assert.equal(size.height, 128);
});

// Separate processes and a hard deadline prevent a parser regression from hanging CI.
for (const [name, hex] of [
  ["ICNS zero-length entry", "69636e73000000106963703700000000"],
  ["JXL zero-size partial stream", "0000000c4a584c200d0a870a000000006a786c7000000000"],
  ["HEIF zero-size box", "000000186674797068656963000000006d69663168656963000000006d657461"],
]) {
  test(`malformed ${name} terminates within a bounded child process`, () => {
    const code = `const {imageSize}=require(${JSON.stringify(parserPath)}); try { imageSize(Buffer.from(${JSON.stringify(hex)},'hex')); } catch (error) { if (!(error instanceof Error)) process.exit(2); }`;
    const result = spawnSync(process.execPath, ["-e", code], {
      cwd: root, shell: false, timeout: 3000, maxBuffer: 65536, windowsHide: true,
    });
    assert.ifError(result.error);
    assert.equal(result.status, 0);
  });
}
