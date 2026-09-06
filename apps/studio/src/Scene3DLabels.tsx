import { useState } from 'react';
import { visual3DSchema, type Visual3D } from './contracts';

// Invalid/unapplied text never enters the saved project or an agent proposal.
export function Scene3DLabels({ visual, disabled, onEditingChange, onChange }: {
  visual: Visual3D; disabled: boolean; onEditingChange: (editing: boolean) => void; onChange: (visual: Visual3D) => void;
}) {
  const [draft, setDraft] = useState<{ labels: string; accent: string } | null>(null);
  const [error, setError] = useState('');
  function close() { setDraft(null); setError(''); onEditingChange(false); }
  if (!draft) return <section className="three-labels" aria-label="3D labels and accent">
    <p className="fine">{visual.lines.length} editable labels · {visual.accent}</p>
    <button disabled={disabled} onClick={() => {
      setDraft({ labels: visual.lines.join('\n'), accent: visual.accent }); onEditingChange(true);
    }}>Edit 3D labels and accent</button>
  </section>;
  return <form className="three-labels" aria-label="Edit 3D labels" onSubmit={e => {
    e.preventDefault(); if (disabled) return;
    const lines = draft.labels.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    const parsed = visual3DSchema.safeParse({ ...visual, lines, accent: draft.accent });
    if (!parsed.success) { setError('Use at most eight non-empty lines of 200 characters and a six-digit hex color.'); return; }
    onChange(parsed.data); close();
  }}>
    <fieldset disabled={disabled}><legend>3D labels and accent — unapplied draft</legend>
      <label>3D labels, one per line<textarea rows={8} maxLength={2000} value={draft.labels}
        onChange={e => setDraft({ ...draft, labels: e.target.value })} /></label>
      <label>3D accent color<input type="color" value={draft.accent}
        onChange={e => setDraft({ ...draft, accent: e.target.value })} /></label>
      <p className="fine">Up to eight labels, 200 characters each. Blank lines are removed on Apply. This changes the local draft only.</p>
      {error && <p role="alert">{error}</p>}
      <div className="toolbar"><button type="submit">Apply 3D labels</button><button type="button" onClick={close}>Cancel 3D labels</button></div>
    </fieldset>
  </form>;
}
