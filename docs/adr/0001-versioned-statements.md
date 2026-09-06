# ADR 0001: statements belong to immutable scene revisions

Status: implemented bounded slice, 6 September 2026.

## Context

Source-faithful production must distinguish an author's reported observation from an hypothesis, a conflict or a suspended conclusion. A file hash verifies bytes, not the truth of the words in those bytes. Article-derived design principles are not imported scientific findings.

## Decision

Keep bounded statements and their admitted-source references inside SceneProject v2 scene revisions. Reuse the existing CAS history, canonical hash and human render authorization. Empty optional statement collections do not rewrite legacy hashes. There is no second mutable claim database or automatic confidence score.

The human can prepare an edit, inspect it, apply it to a local draft and save a new revision. Agents only propose such changes. `observed` requires a cited source location but is explicitly author-declared, not independently certified. Hypothesis/disputed/suspended labels survive rendering as a concise notice; a receipted sidecar preserves the complete evidence references and hashes.

## Alternatives and consequences

- Rejected: inferring verification from a successful render, evidence count or model answer.
- Rejected: a separate claims service with independently changing state that could invalidate a prior approval invisibly.
- Chosen: optional, bounded scene data on the same revision boundary. This keeps manual, MCP and WebMCP changes aligned and permits future review tooling without replacing the authorization model.

A new evidence source or reassessment changes the revision and requires a new plan and approval. Old revisions remain inspectable. This is not a scientific truth engine. Revisit the schema if multilingual claim text or an independently attested evidence workflow is introduced; preserve source provenance and migration compatibility.

## Evidence

`tests/test_claims.py`, `apps/studio/tests/claims.test.tsx`, and `docs/CLAIM_PROVENANCE.md` cover bounds, closed schemas, source admission, old hashes, revision history, unresolved status preservation and exported receipts. Browser qualification remains recorded separately in `docs/STUDIO_QA.md`.
