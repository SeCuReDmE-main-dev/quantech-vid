# Validation baseline — 2026-08-26

## Selected snapshot

The public repository starts from the local-first v2 reconstruction. Legacy Node/MongoDB/Redis/Firebase layers and private Git history remain outside the publication snapshot.

## Local proof

| Check | Result |
|---|---|
| Python | 3.13.7 |
| Python tests | 9 passed |
| Real short render | passed |
| Chrome extension contract | 1 passed |
| Chrome Webpack build | passed |
| Integration catalog | 40 numbered entries |
| Private absolute paths | 0 |
| Secret-pattern file matches | 0 |
| Sensitive filenames | 0 |
| Git diff whitespace check | passed |

Five dependency deprecation warnings were observed during Python tests. They concern Starlette/httpx compatibility and MoviePy with NumPy 2.5; they are maintenance signals rather than test failures.

## Hosted CI

The GitHub Actions workflow is committed and ready. Its first hosted run was prevented before checkout by an account-level billing lock. Local results above remain the active baseline until the GitHub account can execute hosted runners.
