# Dependency and renderer remediation — 2026-09-06

Scope: the existing local renderer and extension build chain, not delivery of the planned full studio, provider connectors or browser-store releases.

## Baseline

Starting commit: `fee4906d6da7e38426c03e3795fd6e128381c5cf`.

GitHub reported 93 open Dependabot alerts: 3 critical, 59 high, 26 medium and 5 low, across the extension lockfile (54), requirements-video.txt (20) and pyproject.toml (19). Five open Dependabot PRs target Pillow, postcss-selector-parser, fast-uri, nanoid and browserslist. Several alerts concern the same dependency in different manifests; this is not 93 independent defects.

The local extension baseline installed and built on Node 24.18.1 / npm 11.16.0, with one passing contract test. Its npm audit reported 41 affected dependency entries (7 critical). This metric differs from GitHub's advisory-by-manifest count. A first attempted npm command from the repository root was an operator cwd error, not a product defect. Recent GitHub CI logs confirm the Ubuntu renderer attempted to load `C:/Windows/Fonts/seguisb.ttf` and failed. Local success does not disprove a CI or platform-specific failure.

## Python/render changes

MoviePy is removed from both direct manifests. Frames and narration are encoded through shell-free FFmpeg commands; MP4, WebM, SRT, VTT, poster, provenance and QA outputs remain part of the contract. Python dependencies are exact pins and both manifests are checked for drift. Pillow, python-dotenv and pytest use the published patched versions selected from current advisories (12.3.0, 1.2.2 and 9.0.3).

Fonts are discovered on the current platform with a Pillow-provided fallback. Windows system fonts are not copied or redistributed. No assertion is made that platform font selection produces byte-identical typography.

Windows rendering owns a kill-on-close Job Object; restricted-host fallback uses a bounded system `taskkill /T`. Assignment follows process creation, leaving a narrow pre-assignment spawn race; this runner is not a sandbox for hostile executables. POSIX process-group cleanup also covers a leader that has already exited. Access-denying host policy can prevent complete cleanup and is not silently treated as a production capability. Duration QA is frame-aware and validates MP4 and WebM separately. Local Python qualification: 23 tests passed, with two upstream test-client deprecation warnings retained.

## Extension toolchain

Node 24.18.1 and npm 11.16.0 are used locally and pinned for CI. Babel remains on the compatible 7.x line (7.29.7), babel-loader 9.2.1, webpack 5.110.3, webpack-cli 5.1.4, copy-webpack-plugin 14.0.0 and web-ext 10.6.0. The two major tool upgrades were checked against their engine/peer requirements; the existing webpack configuration and packaged output are regression-tested. Installation lifecycle scripts are disabled in CI. This does not disable explicitly requested build/package commands.

Sources: [Node release](https://nodejs.org/en/blog/release/v24.18.1), [web-ext releases](https://github.com/mozilla/web-ext/releases), [copy-webpack-plugin releases](https://github.com/webpack/copy-webpack-plugin/releases), [webpack releases](https://github.com/webpack/webpack/releases). Registry metadata and lockfile integrity pin the actual artifacts.

### Narrow image parser replacement

After compatible updates, the remaining npm findings came from `web-ext → addons-linter → image-size@2.0.2`. The original repository is archived; the two current advisories list no published patched version. `npm audit fix --force` proposes downgrading web-ext to 5.5.0; that was not used. No advisory is suppressed and no audit threshold was weakened.

The only override replaces `image-size` with the MIT community fork `image-size-next@2.1.1`, with exact lockfile integrity. This is **not an official Mozilla or original-maintainer release**. Its runtime delta from upstream v2.0.2 was inspected: progress/bounds checks in ICNS and BMFF/JXL/HEIF/JP2 parsing, rejection of invalid JPEG segment length, guarded handler access and removal of an unused fs import. It has no runtime dependencies or install lifecycle hooks. This review is bounded and does not guarantee absence of all defects.

Sources: [ICNS advisory](https://github.com/advisories/GHSA-w3rx-r6r6-pgpr), [JXL/HEIF advisory](https://github.com/advisories/GHSA-5p2g-fcmc-qvqq), [reviewed fork delta](https://github.com/lcf2212dev/image-size-next/compare/v2.0.2...v2.1.1).

Tests check the actual parser resolved from Mozilla's linter (including nested npm installations), read a shipped PNG, enforce the reviewed lockfile alias, and exercise malformed ICNS/JXL/HEIF inputs in subprocesses with hard timeouts. Retest/remove the override when Mozilla adopts a maintained patched parser; never simply rename a vulnerable package to hide an advisory.

## Reproduction

Use Python 3.13 in a clean virtual environment; do not use the machine's default Python without checking its version.

```text
python -m pip install -e ".[dev]"
python -m pip check
python -m pytest -q
python -m pip install pip-audit==2.10.1 detect-secrets==1.5.0
python tools/check_repository_policy.py
python -m pip_audit -r requirements-video.txt --progress-spinner off
```

From `plugins/chrome`, using `.node-version` and npm 11.16.0:

```text
npm ci --ignore-scripts --no-fund
npm run test:contract
npm run package
npm audit --audit-level=low
```

The secret scan is offline (`--no-verify`), examines Git-tracked files rather than private/untracked runtime directories, and prints filenames/counts only if blocked. It is a current-tree scanner, not proof that all historical commits or external storage are free of secrets. Dependabot checks weekly without automatic merge. All new checks fail closed on detected findings.

Local audit status observed: pip-audit reports no known vulnerabilities; npm audit reports zero; offline tracked-file secret scan reports none. These are dated results, not a permanent security guarantee. Remote CI, branch protection and a fresh GitHub alert inventory after an allowed merge remain separate evidence. The locked main branch is not bypassed.
