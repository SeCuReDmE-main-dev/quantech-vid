# Synthetic avatar style presets

The Studio offers two named styles for its existing `synthetic-avatar`
procedural fixture: **Orbital guide** and **Signal mosaic**. They are fictional
geometric presentations, not representations of a person.

Each preset changes only fields already present in `visual_3d`: `kind`, `accent`,
`animation`, and `lines`. No photo, uploaded model, face, likeness, identity,
voice, provider, credential, or new schema field is involved. The fixed Studio
notice states that no real-person likeness or identity is created.

Selecting a preset uses the normal validated draft edit path. It is persisted
with the project document, appears in the existing 3D preview, supports undo and
redo, and invalidates any previously reviewed or approved render plan. Applying a
preset does not save, prepare a plan, approve, or render automatically. The
operator must review the fictional fixture, save a new revision, prepare a fresh
plan, and approve it through the existing production controls.

This is the generic synthetic portion of the avatar action only. It does not
deliver personal avatars, VRM import, model rigging, facial or body inference,
lip synchronization, cloned voices, or identity consent/revocation workflows.
Those capabilities remain unavailable and require separate contracts and proof.

Targeted coverage is in `apps/studio/tests/avatar-style.test.tsx`. It verifies the
exact preset values, absence on non-avatar representations, reuse of the bounded
history reducer, persisted project content, approval invalidation, undo, and the
absence of an automatic render call.
