import { useState } from 'react';
import { transcriptSegmentSchema, validSegmentSequence, type TranscriptSegment } from './contracts';

export function TranscriptEditor({ segments, duration, sourceIds, disabled, onChange, onEditingChange }: {
  segments: TranscriptSegment[]; duration: number; sourceIds: string[]; disabled: boolean;
  onChange: (segments: TranscriptSegment[]) => void; onEditingChange: (editing: boolean) => void;
}) {
  const [draft, setDraft] = useState<TranscriptSegment[] | null>(null), [error, setError] = useState('');
  function close() { setDraft(null); setError(''); onEditingChange(false); }
  function patch(index: number, changes: Partial<TranscriptSegment>) {
    setDraft(current => current?.map((segment, i) => i === index ? {...segment, ...changes} : segment) ?? null);
  }
  return <section className="transcript-editor" aria-label="Source-linked manual captions">
    <h3>Source-linked manual captions</h3>
    <p className="fine">Explicit timings and source locations, not automatic speech recognition or forced alignment. Narration mode is selected separately. Saving a new revision requires a new render plan and approval.</p>
    {draft === null ? <>
      <p>{segments.length ? `${segments.length} manually timed segments` : 'No manual segments. Captions currently use an estimated timing from the narration text.'}</p>
      <ol>{segments.map(segment => <li key={segment.id}><strong>{segment.start.toFixed(3)}–{segment.end.toFixed(3)} s</strong> {segment.text}
        <p className="fine">{segment.source_asset_id} · {segment.source_locator}</p></li>)}</ol>
      <button disabled={disabled} onClick={() => { setDraft(segments.map(s => ({...s}))); onEditingChange(true); }}>Edit manual captions</button>
    </> : <form aria-label="Edit manual captions" onSubmit={e => {
      e.preventDefault(); if (disabled) return;
      const parsed = transcriptSegmentSchema.array().max(128).safeParse(draft);
      if (!parsed.success || !validSegmentSequence(parsed.data, sourceIds, duration)) {
        setError('Use ordered, non-overlapping segments within the film duration, with text and a location in an admitted source.'); return;
      }
      onChange(parsed.data); close();
    }}>
      <fieldset disabled={disabled}><legend>Caption draft — apply or cancel before production</legend>
        {draft.map((segment, index) => <div className="caption-row" key={segment.id}>
          <h4>Segment {index + 1}</h4>
          <div className="caption-times"><label>Start {index + 1} (seconds)<input type="number" min="0" max={duration} step="0.001" value={segment.start}
            onChange={e => patch(index, {start: Number(e.target.value)})} /></label>
            <label>End {index + 1} (seconds)<input type="number" min="0.001" max={duration} step="0.001" value={segment.end}
              onChange={e => patch(index, {end: Number(e.target.value)})} /></label></div>
          <label>Caption text {index + 1}<textarea rows={2} maxLength={1000} value={segment.text} onChange={e => patch(index, {text: e.target.value})} /></label>
          <label>Caption source {index + 1}<select value={segment.source_asset_id} onChange={e => patch(index, {source_asset_id:e.target.value})}>
            {sourceIds.map(id => <option key={id} value={id}>{id}</option>)}</select></label>
          <label>Source location {index + 1}<input maxLength={160} value={segment.source_locator} placeholder="For example: paragraph 2, sentence 1"
            onChange={e => patch(index, {source_locator: e.target.value})} /></label>
          <button type="button" onClick={() => setDraft(draft.filter((_, i) => i !== index))}>Remove caption {index + 1}</button>
        </div>)}
        <button type="button" disabled={!sourceIds.length || draft.length >= 128 || (draft.at(-1)?.end ?? 0) >= duration} onClick={() => {
          const start = draft.at(-1)?.end ?? 0;
          setDraft([...draft, {id:`caption-${crypto.randomUUID().slice(0,8)}`, start, end:Math.min(duration,start+1), text:'',
            source_asset_id:sourceIds[0],source_locator:''}]);
        }}>Add timed caption</button>
        {error && <p role="alert">{error}</p>}
        <div className="toolbar"><button type="submit">Apply manual captions</button><button type="button" onClick={close}>Cancel caption edit</button></div>
      </fieldset>
    </form>}
  </section>;
}
