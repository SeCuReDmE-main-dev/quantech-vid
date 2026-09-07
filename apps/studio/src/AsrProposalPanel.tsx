import { useEffect, useRef, useState } from 'react';
import { StudioAPI, StudioError } from './api/client';
import type { AsrProposal } from './api/asr-contracts';
import type { ProjectRevision, SourceAsset, TranscriptSegment } from './contracts';
import { SourceAudioPreview } from './SourceAudioPreview';

export type AsrContext = { api: StudioAPI; revision: ProjectRevision; sources: SourceAsset[];
  configured: boolean; disabled: boolean; onPendingChange: (pending: boolean) => void };

export function AsrProposalPanel({ context, onReview }: { context: AsrContext;
  onReview: (segments: TranscriptSegment[]) => void }) {
  const candidates = context.sources.filter(s => context.revision.document.sources.includes(s.id)
    && s.provenance.original?.media_type === 'audio/wav' && s.allowed_operations.includes('analyze'));
  const [selected, setSelected] = useState(''), [acknowledged, setAcknowledged] = useState(false);
  const [proposal, setProposal] = useState<AsrProposal | null>(null), [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const live = useRef(true), request = useRef<AbortController | null>(null);
  const onPending = useRef(context.onPendingChange); onPending.current = context.onPendingChange;
  useEffect(() => { live.current = true; return () => {
    live.current = false; request.current?.abort(); onPending.current(false);
  }; }, []);
  const source = candidates.find(s => s.id === selected);
  const coherent = !!proposal && source?.id === proposal.source_asset_id
    && source.sha256 === proposal.asset_sha256 && source.provenance.original?.sha256 === proposal.original_audio_sha256
    && context.revision.project_id === proposal.project_id && context.revision.revision === proposal.revision
    && context.revision.document_hash === proposal.project_hash;
  async function propose() {
    if (request.current || context.disabled || !context.configured || !acknowledged || !source) return;
    const controller = new AbortController(); request.current = controller;
    setPending(true); onPending.current(true); setProposal(null); setError('');
    try {
      const value = await context.api.asrProposal(context.revision, source, controller.signal);
      if (live.current && !controller.signal.aborted) setProposal(value);
    } catch (cause) {
      if (live.current) setError(cause instanceof StudioError ? cause.message : 'No usable proposal was returned. Your saved captions are unchanged. No automatic retry was made.');
    } finally {
      request.current = null;
      if (live.current) { setPending(false); onPending.current(false); }
    }
  }
  if (!candidates.length) return null;
  return <section aria-label="Experimental local transcription">
    <h4>Experimental local transcription</h4>
    <p className="fine">English PCM16 mono 16 kHz only. Machine text may hallucinate, including on noise. Rejecting digital silence is not speech detection. Nothing here saves or approves a video.</p>
    {!context.configured && <p>Local ASR is not configured. You can still write captions manually.</p>}
    <label>Audio source for transcription<select value={selected} disabled={context.disabled || pending}
      onChange={e => { setSelected(e.target.value); setProposal(null); setAcknowledged(false); }}>
      <option value="">Choose an admitted WAV source</option>
      {candidates.map(s => <option key={s.id} value={s.id}>{s.provenance.original?.name}</option>)}
    </select></label>
    {source && <SourceAudioPreview
      key={`${source.id}:${source.sha256}:${source.provenance.original?.sha256}`}
      source={source} api={context.api} disabled={context.disabled || pending} />}
    <label><input type="checkbox" checked={acknowledged} disabled={context.disabled || pending}
      onChange={e => setAcknowledged(e.target.checked)}/>I understand this is an unverified machine proposal requiring review.</label>
    <button type="button" disabled={context.disabled || pending || !context.configured || !acknowledged || !source}
      onClick={() => void propose()}>Create local caption proposal</button>
    {pending && <p role="status">Local CPU transcription is running. No other provider will be substituted.</p>}
    {error && <p role="alert">{error}</p>}
    {proposal && <div>
      <p>Machine proposal only · {(proposal.audio_duration_ms / 1000).toFixed(3)} s · {proposal.segments.length} segments</p>
      <p className="fine">Original audio SHA-256: <code>{proposal.original_audio_sha256}</code></p>
      <p className="fine">Runtime binding: <code>{proposal.resource_binding_sha256}</code></p>
      <ol>{proposal.segments.map(s => <li key={s.id}>{s.start.toFixed(3)}–{s.end.toFixed(3)} s · {s.text}</li>)}</ol>
      {!proposal.segments.length && <p>No segments proposed. This does not prove silence or absence of speech.</p>}
      <button type="button" disabled={context.disabled || pending || !proposal.segments.length || !coherent}
        onClick={() => onReview(proposal.segments.map(s => ({ ...s })))}>Review proposal in caption editor</button>
      <button type="button" disabled={pending} onClick={() => setProposal(null)}>Discard machine proposal</button>
      <p className="fine">Review replaces only the editor's temporary draft. Apply, save, approval and render remain separate actions. Unapplied proposals are not stored.</p>
    </div>}
  </section>;
}
