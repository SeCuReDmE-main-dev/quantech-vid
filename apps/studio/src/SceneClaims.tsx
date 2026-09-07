import { useState } from 'react';
import { claimSchema, type SceneClaim } from './contracts';

const labels: Record<SceneClaim['status'], string> = {
  reported: 'Reported — not independently verified', observed: 'Observed — source cited by author',
  hypothesis: 'Hypothesis — not established', disputed: 'Disputed — conflict remains',
  suspended: 'Suspended — awaiting evidence',
};

export function SceneClaims({ claims, sourceIds, disabled, onChange, onEditingChange }: {
  claims: SceneClaim[]; sourceIds: string[]; disabled: boolean;
  onChange: (claims: SceneClaim[]) => void; onEditingChange: (editing: boolean) => void;
}) {
  const [draft, setDraft] = useState<SceneClaim | null>(null);
  const [error, setError] = useState('');
  function open(value: SceneClaim) { setDraft(structuredClone(value)); setError(''); onEditingChange(true); }
  function close() { setDraft(null); setError(''); onEditingChange(false); }
  function apply() {
    const parsed = claimSchema.safeParse(draft);
    if (!parsed.success || parsed.data.evidence.some(e => !sourceIds.includes(e.source_asset_id))) {
      setError('Provide a statement and rationale. Observed requires at least one admitted source with a location.'); return;
    }
    const found = claims.some(c => c.id === parsed.data.id);
    onChange(found ? claims.map(c => c.id === parsed.data.id ? parsed.data : c) : [...claims, parsed.data]); close();
  }
  return <section className="scene-claims" aria-label="Statements and evidence">
    <h3>Statements and evidence</h3>
    <p className="fine">Keep an uncertainty visible rather than turning it into a fact. These are author declarations, not independent verification. Saving preserves the previous revision.</p>
    <ul>{claims.map((claim, i) => <li key={claim.id}>
      <strong>{labels[claim.status]}</strong><p>{claim.text}</p><p className="fine">{claim.rationale}</p>
      <div className="toolbar"><button disabled={disabled || !!draft} onClick={() => open(claim)}>Edit statement {i + 1}</button>
        <button disabled={disabled || !!draft} onClick={() => onChange(claims.filter(c => c.id !== claim.id))}>Remove statement {i + 1} from this draft</button></div>
    </li>)}</ul>
    <button disabled={disabled || !!draft || claims.length >= 16} onClick={() => open({ id: `claim-${crypto.randomUUID().slice(0, 8)}`,
      text: '', status: 'reported', rationale: '', evidence: [] })}>Add a statement</button>
    {draft && <form className="claim-form" onSubmit={e => { e.preventDefault(); apply(); }}>
      <fieldset disabled={disabled}><legend>Review this statement before adding it to the scene</legend>
        <label>Statement<textarea value={draft.text} maxLength={1000} onChange={e => setDraft({ ...draft, text: e.target.value })} /></label>
        <label>Evidence status<select value={draft.status} onChange={e => setDraft({ ...draft, status: e.target.value as SceneClaim['status'] })}>
          {Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Reason for this status<textarea value={draft.rationale} maxLength={500} onChange={e => setDraft({ ...draft, rationale: e.target.value })} /></label>
        {draft.evidence.map((entry, i) => <div key={i} className="claim-evidence">
          <label>Evidence source {i + 1}<select value={entry.source_asset_id} onChange={e => setDraft({ ...draft,
            evidence: draft.evidence.map((item, n) => n === i ? { ...item, source_asset_id: e.target.value } : item) })}>
            {sourceIds.map(id => <option value={id} key={id}>{id}</option>)}</select></label>
          <label>Source location {i + 1}<input value={entry.locator} maxLength={160} onChange={e => setDraft({ ...draft,
            evidence: draft.evidence.map((item, n) => n === i ? { ...item, locator: e.target.value } : item) })} /></label>
          <button type="button" onClick={() => setDraft({ ...draft, evidence: draft.evidence.filter((_, n) => n !== i) })}>Remove evidence {i + 1}</button>
        </div>)}
        <button type="button" disabled={!sourceIds.length || draft.evidence.length >= 8} onClick={() => setDraft({ ...draft,
          evidence: [...draft.evidence, { source_asset_id: sourceIds[0], locator: '' }] })}>Add an admitted evidence source</button>
        {error && <p role="alert">{error}</p>}
        <p className="fine">This edit is not in the project until you apply it. Other production controls are paused while it is open. Cancel preserves the previous statement.</p>
        <div className="toolbar"><button type="submit">Apply statement to local draft</button><button type="button" onClick={close}>Cancel statement edit</button></div>
      </fieldset>
    </form>}
  </section>;
}
