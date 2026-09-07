# Local Markdown source admission

The existing authenticated browser upload endpoint accepts `.md` and `.markdown`
files as explicit local selections. The request retains the same exact Host,
Origin, human-session, CSRF, rights declaration, provenance, streamed body, file
name, and path-traversal checks used by text and image uploads.

Markdown is decoded as strict UTF-8 and limited to 200,000 characters. Its
byte-exact original is retained under the private runtime source-original store;
the response reports its real size, SHA-256, original name, and
`media_type: text/markdown`. Failed validation removes the staged original.

QuaNTecH-ViD treats Markdown as untrusted literal text. It does not parse or
render HTML, execute JavaScript, resolve links or images, import URLs, fetch
network resources, or claim to convert the complete manuscript. The admitted
render asset is a bounded 1280×720 PNG excerpt produced by the same local Pillow
text-preview path as `.txt`. Its `SourceAsset` is explicitly the derived PNG and
has its own SHA-256; the response marks `derived: true`.

This slice adds no route, Markdown engine, browser renderer, network client, or
publication claim. The original rights and provenance remain operator
declarations rather than automated proof.

## Current qualification and limits

Chrome completed explicit selection, rights declaration and local admission of a
460-byte synthetic `.md` fixture. The stored original's SHA-256 matched the
selected file. Selection alone created no admitted asset. Text-like HTML and
remote-image syntax remained inert; no image element was inserted into the page.
Backend tests cover literal equivalence with TXT, strict UTF-8, size and path
refusals. Client tests cover both suffixes and transport of the selected bytes.

Original metadata is currently returned by the upload response and its hash is
recorded in the provenance note. The immutable render plan and rendered-file
provenance bind the derived PNG, not a structured original-source descriptor.
The studio currently shows the derived type after admission. Durable structured
lineage, visible original metadata and the retention/deletion lifecycle remain
separate work; this slice does not claim complete manuscript traceability.
