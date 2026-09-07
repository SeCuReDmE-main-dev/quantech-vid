import { useEffect, useRef, useState } from 'react';
import type { StudioAPI } from './api/client';
import type { SourceAsset } from './contracts';

export function SourceAudioPreview({ source, api, disabled }: {
  source: SourceAsset; api: StudioAPI; disabled: boolean;
}) {
  const [url, setUrl] = useState<string | null>(null), [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);
  const mounted = useRef(true), allocated = useRef<string | null>(null), pending = useRef(false);
  const generation = useRef(0);
  function release() {
    generation.current++;
    if (allocated.current) URL.revokeObjectURL(allocated.current);
    allocated.current = null; setUrl(null);
  }
  useEffect(() => { mounted.current = true; return () => {
    mounted.current = false; generation.current++;
    if (allocated.current) URL.revokeObjectURL(allocated.current);
  }; }, []);
  async function load() {
    if (pending.current || disabled || url) return;
    const requestGeneration = generation.current;
    pending.current = true; setLoading(true); setStatus('Checking the original WAV bytes…');
    try {
      const blob = await api.sourceAudioPreview(source);
      if (!mounted.current || requestGeneration !== generation.current) return;
      const next = URL.createObjectURL(blob); allocated.current = next; setUrl(next);
      setStatus('Original WAV bytes verified. Playback is manual and does not start transcription or rendering.');
    } catch {
      if (mounted.current && requestGeneration === generation.current)
        setStatus('The original audio could not be verified. Nothing was played or transcribed.');
    } finally {
      pending.current = false; if (mounted.current && requestGeneration === generation.current) setLoading(false);
    }
  }
  return <section className="source-preview" aria-label="Original audio review">
    <button type="button" disabled={disabled || loading || !!url} onClick={() => void load()}>
      Load original audio for review
    </button>
    {url && <div>
      <audio controls preload="none" src={url} aria-label="Verified original WAV"
        onError={() => { release(); setStatus('The browser could not decode this WAV. Nothing was transcribed or rendered.'); }} />
      <button type="button" onClick={() => { release(); setStatus('Original audio closed and removed from this view.'); }}>
        Close original audio
      </button>
    </div>}
    <p className="fine">Explicit playback only. No autoplay, narration, mixing, transcription, rendering or upload is started here.</p>
    {status && <p role="status">{status}</p>}
  </section>;
}
