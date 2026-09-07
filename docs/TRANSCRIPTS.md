# Manual transcript and caption contract

QuaNTecH-ViD supports operator-authored or reviewed timed caption segments on each
v2 locale track. Manual editing remains available without a model. A separately
configured experimental local ASR path can propose text from an admitted WAV;
see ASR_ADMISSION.md. Neither path validates truth or proves ownership of words.

`TrackV2.segments` is optional, limited to 128 entries, and omitted when empty.
That omission preserves the canonical serialization and hash of project revisions
created before this field existed. A segment is a closed object with:

- `id`: 1–64 ASCII letters, digits, `_`, or `-`;
- finite `start` and `end` seconds satisfying `0 <= start < end`;
- `text`: 1–1000 characters, preserved verbatim in the canonical revision;
- an admitted `source_asset_id` and a 1–160 character `source_locator`.

`text` and `source_locator` must each contain at least one character that is
neither Unicode whitespace nor a Unicode category `C` control/format character.
Validation never trims or rewrites an accepted original. JSON `start` and `end`
values are strict numbers (integer values remain valid); numeric strings and
booleans are rejected.

Within each track, IDs are unique, segments are supplied in chronological order,
do not overlap, and end no later than the sum of scene durations. Evidence source
IDs must be present in `document.sources`. These are author declarations attached
to admitted bytes, not independent verification.

Times are exported at millisecond precision using decimal half-up rounding. A
segment is rejected unless `round_half_up(end * 1000) > round_half_up(start * 1000)`;
for example, `0.0001..0.0002` is invalid because both endpoints serialize to zero.

On an approved render, explicit segments replace narration-derived cue timing for
the selected locale only. SRT and WebVTT timestamps use the declared intervals.
Derived cue text is reduced to a single inert line and HTML/WebVTT delimiters are
escaped; non-text control bytes become the Unicode replacement character. This
prevents embedded blank blocks, timing arrows, markup, or controls from creating
new cues. The canonical project retains the original text unchanged. Tracks without
segments continue to use the existing narration sentence-weighting behavior.

When the rendered locale has segments, production also emits
`<slug>-<locale>-<profile>-transcript-provenance.json` with receipt role
`transcript-provenance`. It binds the project ID, revision and canonical hash,
locale, segment intervals, source IDs, source SHA-256 values, locators, and a hash
of each original text. New sidecars use `quantech.transcript-provenance.v2` and
declare `method: operator-authored-or-reviewed`,
`automatic_transcription_during_export: false`,
`upstream_transcription_method: not_attested`, and
`independent_verification: false`. It contains no internal paths, session tokens,
pairing codes, grants, or source bytes.

The export stage serializes reviewed project segments and does not run ASR.
This does not certify how the operator obtained their text: an earlier machine
proposal may have been revised. Historical v1 receipts remain unchanged; the
new wording avoids falsely asserting that no upstream transcription occurred.

Editing segments uses the existing human-only CAS project revision endpoint. As
with every document edit, a changed canonical hash makes an older plan/grant stale;
approval must target the new revision.

## Visible editor and player

The English studio editor stages caption changes until Apply or Cancel. Pending
caption, statement and 3D-detail forms block saving, scene navigation, tool execution
and even an already-approved render. Applying a change invalidates the displayed
plan; deleting all segments omits the optional field again. Undo and local draft
recovery use the same canonical project document.

After inspecting a verified video file, **Load verified captions** separately
retrieves its WebVTT receipt through the authenticated byte/hash checker. Files
larger than 1 MB are not attached. The resulting local Blob track is loaded by the
browser, its cue count is reported, and its object URL is revoked on close. Closing
while a request is pending cannot attach its late result to another film.

Windows/Chrome qualification on 6 September 2026: two manual intervals
`0.250–1.750` and `2.250–3.750` were entered in a four-second fixture. The overlap
error was exercised; Apply removed the old approval surface; revision2 required a
new plan and approval. MP4/WebM passed media QA. SRT/VTT exported these exact
timings and matched their receipt SHA-256 values. Chrome reported two loaded cues.
This does not qualify automatic transcription, audio import, translation accuracy,
caption readability across every device, or the truth of the annotations.
