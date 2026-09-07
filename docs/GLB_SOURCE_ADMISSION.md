# Plain GLB source admission

The studio can explicitly admit a local `.glb` file as a static visual source.
This does **not** implement an editable imported mesh, an animated avatar, VRM,
or texture import. These remain separate capabilities.

The existing human-only upload route requires pairing, CSRF and exact origin,
a selected filename, and an operator declaration of rights with a reference.
File selection alone does not send content. Agents cannot use this route.
Declarations are not independent licence verification or permission to use a
person's likeness.

The source is capped at 20,000,000 bytes. The strict GLB parser and supervised
poster renderer accept only the documented self-contained geometry subset.
No URI, texture, animation, skin, extension or VRM is accepted. One preview runs
at a time with a 30-second worker budget; this is not an OS sandbox. A client
disconnect does not currently cancel an in-flight preview, but the worker's
deadline still applies.

Four fixed camera views become a 1024-square PNG. Only that derivative is exposed
to the preview and video renderer; the original is retained privately. Its exact
hash, size, media type and transformation are stored in structured provenance.
The renderer-bundle binding is recorded in the provenance note. Existing render
plans bind the original and derivative hashes, and source-integrity checks are
unchanged. Source admission creates no project, render plan, production grant or
video. Human review and authorization of the exact plan remain necessary.

## Existing local databases

The known `source_originals` constraint is migrated transactionally to admit
`glb-four-view-png-v1`. Before rewriting the table, the complete SQLite database
is backed up privately beside it with a unique `.before-glb-v1-*.bak` suffix.
Rows, original files and foreign-key checks are preserved. Custom triggers,
indexes, inbound references or an unexpected legacy schema stop automatic
migration. Failures roll back rather than clearing stored work. A repeat start
does not generate another backup after successful migration.

Backups can contain private session and source metadata. They must not enter a
public repository or evidence upload. Windows filesystem ACL hardening and the
complete retention/deletion UI remain separate outstanding work.

## Evidence and limits

Tests cover actual headless GLB-to-PNG admission, retained bytes, filtered malformed
input, missing rights, agent refusal, absence of production authority, frontend
size limits and explicit static-source labels. Migration tests cover backup,
row preservation, repeated startup, custom-schema refusal and rollback.

See [parser profile](MODEL3D_IMPORT.md) and [renderer boundary](MODEL3D_POSTER.md).
An isolated Chrome QA run selected an original synthetic tetrahedron through the
file chooser, admitted it, saved a two-second scene, reviewed and approved its
plan, then played the resulting 1280×720 MP4 to completion. MP4/WebM QA passed;
nine receipts included original-to-derivative lineage. This verifies the static
source workflow only, not interactive mesh editing or imported animation.
