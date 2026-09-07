import assert from "node:assert/strict";
import test from "node:test";

import { installBackground } from "../background.js";
import { browserAPI } from "../browser-api.js";
import { boundedSelection, makeTextBlob, MENU_ID, MESSAGE_TYPES, renderSelectedText } from "../companion-core.js";
import { MAX_SELECTION_CHARACTERS, STUDIO_URL } from "../config.js";

function event() {
  const listeners = [];
  return { listeners, addListener(listener) { listeners.push(listener); } };
}

function mockAPI() {
  const calls = { menus: [], tabs: [], panels: [], messages: [] };
  const api = {
    runtime: { id: "companion-id", lastError: null, onInstalled: event(), onMessage: event(),
      sendMessage(value) { calls.messages.push(value); } },
    contextMenus: {
      onClicked: event(), removeAll(callback) { callback(); },
      create(value) { calls.menus.push(value); },
    },
    tabs: { create(value, callback) { calls.tabs.push(value); callback({id:42}); } },
    sidePanel: { open(value) { calls.panels.push(value); return Promise.resolve(); } },
  };
  return { api, calls };
}

test("browser compatibility adapter prefers Chromium callbacks and adapts Firefox promises", async () => {
  const chromium = mockAPI().api;
  const firefoxMock = mockAPI().api;
  const firefox = {
    ...firefoxMock,
    runtime: { ...firefoxMock.runtime, sendMessage: async value => value },
    contextMenus: { ...firefoxMock.contextMenus, removeAll: async () => undefined },
    tabs: { create: async value => value },
  };
  assert.equal(browserAPI({ chrome: chromium, browser: firefox }), chromium);
  const adapted = browserAPI({ browser: firefox });
  let response;
  await adapted.runtime.sendMessage({ safe: true }, value => { response = value; });
  assert.deepEqual(response, { safe: true });
  await adapted.contextMenus.removeAll(() => { response = "removed"; });
  assert.equal(response, "removed");
  await adapted.tabs.create({ url: STUDIO_URL }, value => { response = value; });
  assert.deepEqual(response, { url: STUDIO_URL });
  assert.throws(() => browserAPI({}), /Required WebExtension APIs/);
});

function message(listener, value, sender = { id: "companion-id" }) {
  let response;
  const async = listener(value, sender, result => { response = result; });
  return { response, async };
}

test("only an explicit context-menu selection enters ephemeral memory", () => {
  const { api, calls } = mockAPI();
  const background = installBackground(api);
  api.runtime.onInstalled.listeners[0]();
  assert.deepEqual(calls.menus, [{ id: MENU_ID, title: "Prepare selected text for QuaNTecH-ViD", contexts: ["selection"] }]);
  api.contextMenus.onClicked.listeners[0]({ menuItemId: "other", selectionText: "ignored", pageUrl: "https://private.invalid" }, {});
  assert.equal(background.snapshot(), null);
  api.contextMenus.onClicked.listeners[0]({ menuItemId: MENU_ID, selectionText: "chosen <script>text</script>",
    pageUrl: "https://private.invalid" }, { windowId: 7, url: "https://private.invalid" });
  assert.equal(background.snapshot(), "chosen <script>text</script>");
  assert.deepEqual(calls.panels, [{ windowId: 7 }]);
  assert.doesNotMatch(JSON.stringify(background.snapshot()), /private\.invalid/);
});

test("selection bounds, clear, hostile inert rendering, and restart loss are closed", async () => {
  assert.equal(boundedSelection("x".repeat(MAX_SELECTION_CHARACTERS)).length, MAX_SELECTION_CHARACTERS);
  assert.equal(boundedSelection("x".repeat(MAX_SELECTION_CHARACTERS + 1)), null);
  const hostile = "<img src=x onerror=alert(1)> & <script>bad()</script>";
  const target = { textContent: "" };
  renderSelectedText(target, hostile);
  assert.equal(target.textContent, hostile);
  const blob = makeTextBlob("Résumé exact");
  assert.equal(blob.type, "text/plain;charset=utf-8");
  assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), new TextEncoder().encode("Résumé exact"));

  const first = mockAPI();
  const state = installBackground(first.api);
  first.api.contextMenus.onClicked.listeners[0]({ menuItemId: MENU_ID, selectionText: hostile }, {});
  const handler = first.api.runtime.onMessage.listeners[0];
  assert.equal(message(handler, { type: MESSAGE_TYPES.status }).response.selectedText, hostile);
  assert.equal(message(handler, { type: MESSAGE_TYPES.clear }).response.selectedText, null);
  assert.deepEqual(first.calls.messages.at(-1), { type: "selection_changed" });
  assert.equal(state.snapshot(), null);
  assert.equal(installBackground(mockAPI().api).snapshot(), null);
});

test("only the fixed studio URL opens and foreign messages are rejected", () => {
  const { api, calls } = mockAPI();
  installBackground(api);
  const handler = api.runtime.onMessage.listeners[0];
  assert.equal(message(handler, { type: MESSAGE_TYPES.open }, {}).response, undefined);
  assert.equal(message(handler, { type: MESSAGE_TYPES.open }, { id: "hostile-extension" }).response, undefined);
  const opened = message(handler, { type: MESSAGE_TYPES.open });
  assert.equal(opened.async, true);
  assert.deepEqual(opened.response, { ok: true });
  assert.deepEqual(calls.tabs, [{ url: STUDIO_URL, active: true }]);
  assert.equal(message(handler, { type: "open_studio", url: "https://evil.invalid" }).response, undefined);
});

test("a failed tab creation is never reported as a successful handoff", () => {
  const { api } = mockAPI();
  api.tabs.create = (_, callback) => callback(undefined);
  installBackground(api);
  const handler = api.runtime.onMessage.listeners[0];
  assert.deepEqual(message(handler, {type: MESSAGE_TYPES.open}).response, {ok:false});
});
