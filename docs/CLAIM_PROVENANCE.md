# Claim provenance

QuaNTecH-ViD can carry bounded, author-declared claim statuses from an admitted source through a scene and into a production artifact. This feature preserves uncertainty; it does not decide scientific truth, validate a license, or independently verify the statement.

## Scene contract

`SceneV2.claims` is optional, limited to 16 entries, and omitted from serialized documents when empty so stored version 2 revisions created before this field keep their canonical hash.

Each claim contains:

- a scene-local identifier;
- a statement of at most 1,000 characters;
- one of `reported`, `observed`, `hypothesis`, `disputed`, or `suspended`;
- an author rationale of at most 500 characters;
- up to eight evidence locators referencing opaque source IDs already admitted in the project.

`observed` requires at least one evidence reference. That requirement proves only that a source locator was declared; it does not prove the observation. `disputed` and `suspended` may remain without evidence and are never converted automatically to true or false. Claim IDs must be unique within a scene. Unknown fields and evidence sources outside `document.sources` are rejected.

## Revisions and approval

Claims participate in the canonical project hash. Saving a changed claim through the existing CAS endpoint creates a new revision. Older revisions remain immutable and readable. Render plans and human grants remain bound to one exact revision and project hash; a revised claim requires a new plan and new approval.

Agent tools can include claims in storyboard or scene-change proposals, but proposals do not update the canonical project. Agents cannot mint approval.

## Rendered warning and sidecar

For `hypothesis`, `disputed`, and `suspended`, the renderer draws a dedicated top banner independently of the title/body panel. It does not replace or truncate the stored source body. The notice is capped at 500 characters and rendered in at most six lines, including at 320×320. Full statements remain in the sidecar.

Every approved render emits `<slug>-<locale>-<profile>-claims.json`. Its receipt role is `claim-provenance`. The sidecar contains:

- project ID, revision, and project hash;
- scene and claim data;
- evidence source IDs, source hashes, and locators;
- `independent_verification: false` and an explicit author-declaration limitation.

It contains no internal source path, session token, grant, or pairing credential. Its receipt hashes the actual sidecar bytes.

## Corpus-transfer boundary

This behavior implements the workflow lessons catalogued as C02, C03, C05, and C25: decisions are versioned, contradictions survive summarization, suspension is preserved for later reevaluation, and suspended is not treated as false. The referenced DMQC and quantum narratives remain private source material and are not embedded as product truth, formulas, or automatic classifiers.
