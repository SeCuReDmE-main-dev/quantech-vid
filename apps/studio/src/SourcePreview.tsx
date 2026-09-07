import { useEffect, useRef, useState } from 'react';
import type { StudioAPI } from './api/client';
import type { SourceAsset } from './contracts';

export function SourcePreview({ source, api, disabled }: { source: SourceAsset; api: StudioAPI; disabled: boolean }) {
  const [url, setUrl] = useState<string | null>(null), [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);
  const mounted = useRef(true), allocated = useRef<string | null>(null), pending = useRef(false);
  useEffect(() => { mounted.current = true; return () => {
    mounted.current = false; if (allocated.current) URL.revokeObjectURL(allocated.current);
  }; }, []);
  async function load() {
    if (pending.current || disabled) return;
    pending.current = true; setLoading(true); setStatus('Checking the admitted image bytes…');
    try {
      const blob = await api.sourcePreview(source);
      if (!mounted.current) return;
      const next = URL.createObjectURL(blob); allocated.current = next; setUrl(next);
      setStatus('Image bytes verified. This is the admitted derivative, not a final rendered frame.');
    } catch { if (mounted.current) setStatus('The source preview could not be verified. Reload source metadata or re-admit a trusted copy.'); }
    finally { pending.current = false; if (mounted.current) setLoading(false); }
  }
  return <section className="source-preview" aria-label="Admitted source preview">
    <button disabled={disabled || loading || !!url} onClick={() => void load()}>Inspect selected source image</button>
    {url && <img src={url} alt={`Admitted source derivative: ${source.provenance.original?.name ?? source.id}`}
      onError={() => { if (allocated.current) URL.revokeObjectURL(allocated.current); allocated.current = null;
        setUrl(null); setStatus('The browser could not decode this image. No render was requested.'); }} />}
    <p className="fine">Explicit inspection only. Memory cache is cleared when the project, revision, format, source metadata or local session changes.</p>
    {status && <p role="status">{status}</p>}
  </section>;
}
