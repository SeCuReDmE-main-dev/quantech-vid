# Cross-browser local companion

The QuaNTecH-ViD companion is a visible launcher and selected-text handoff. It
does not capture pages, inspect page DOM, call an API, hold credentials, embed
the studio, or connect an external engine.

## Authority and data boundary

The only studio destination is the top-level URL
`http://127.0.0.1:7476/`. The URL is a constant; runtime messages cannot supply
or alter it. Opening the studio uses the browser tabs API and needs no `tabs`
permission. There is no iframe, content script, host permission, fetch, URL
fragment, externally-connectable message surface, or background page scan.

The context-menu item exists only for the browser's `selection` context. After
the user clicks it, the browser supplies `selectionText`. The companion accepts
between 1 and 200,000 JavaScript characters and holds that string only in a
background variable. It never reads or retains the page, frame, link, image,
tab, or source URL. The selection can disappear whenever a Manifest V3 service
worker, Firefox event page, extension, or browser stops or restarts.

The popup/side panel renders selected text only through `textContent`. The user
may clear it or explicitly download a byte-exact UTF-8
`quantech-selected-text.txt` Blob. Downloading does not import it. The user must
then open the top-level studio, use its visible file selector, review the text,
and complete the source-rights declaration. The companion never uploads or
mutates the current page.

## Browser manifests

Chrome and Edge use `manifest.json`: Manifest V3 service worker, action popup,
and `side_panel`. Its complete permission set is `contextMenus` and
`sidePanel`. Firefox uses `manifest.firefox.json`: Manifest V3
`background.scripts`, action popup, `sidebar_action`, a fixed Gecko signing ID,
and a required `websiteContent` data declaration because explicitly selected
page text can be exported from the extension. Its complete permission set is
`contextMenus`.

Neither manifest has `host_permissions`. Both disable incognito operation and
reuse the existing synthetic icons. The Firefox sidebar and Chromium side panel
host only the packaged companion UI; neither embeds the studio.

## Tools and engines

The top-level studio remains the authority surface. After local pairing and a
human-selected saved project, it can register its existing eight scoped tools
where native WebMCP is actually present. The companion does not register,
proxy, or emulate those tools, and it cannot approve a plan.

OpenAI Codex, GitHub Copilot, and Google Antigravity (`agy`) are shown as **not
connected** by this companion. Installation, authentication, native tool
restriction, execution, and production qualification remain separate evidence.
No provider login, prompt, paid call, or browser automation is performed.

## Build, test, and package

From `plugins/chrome` with the repository's pinned Node/npm versions:

```powershell
npm ci
npm run test:contract
npm run build
npm run lint:firefox
npm run package
```

Build outputs are isolated under `dist/chrome`, `dist/edge`, and
`dist/firefox`. Unsigned ZIP artifacts are isolated under `releases/chrome`,
`releases/edge`, and `releases/firefox`. Packaging is not browser installation,
store signing, submission, or runtime qualification.

Contract tests verify manifest differences, absence of network and persistent
data permissions, exact top-level URL opening, selection bounds, explicit
clearing, inert hostile text, byte-exact UTF-8 export, and loss of in-memory
selection when the background is recreated. Browser-specific installation and
end-to-end journeys still require separate visible QA before any cross-browser
release claim.

## Primary browser references

- [Chrome context menus](https://developer.chrome.com/docs/extensions/reference/api/contextMenus)
- [Chrome Tabs API permissions](https://developer.chrome.com/docs/extensions/reference/api/tabs)
- [Chrome Side Panel API](https://developer.chrome.com/docs/extensions/reference/api/sidePanel)
- [Microsoft Edge Manifest V3 format](https://learn.microsoft.com/en-us/microsoft-edge/extensions/getting-started/manifest-format)
- [Firefox cross-browser Manifest V3 backgrounds](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/manifest.json/background)
- [Firefox `sidebar_action`](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/manifest.json/sidebar_action)
- [Firefox browser-specific signing and data settings](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/manifest.json/browser_specific_settings)
