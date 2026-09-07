import { useEffect, useRef, useState } from 'react';

export function VerifiedVideoPreview({ url, loadCaptions }: { url: string; loadCaptions?: () => Promise<Blob> }) {
  const [captionUrl, setCaptionUrl] = useState<string | null>(null);
  const [status, setStatus] = useState(''), [loading, setLoading] = useState(false);
  const live = useRef(true), inFlight = useRef(false), allocated = useRef<string | null>(null);
  const trackElement = useRef<HTMLTrackElement | null>(null);
  useEffect(() => { live.current = true; return () => {
    live.current = false;
    if (allocated.current) URL.revokeObjectURL(allocated.current);
  }; }, []);
  useEffect(() => {
    const element = trackElement.current;
    if (!captionUrl || !element) return;
    // Track load/error events do not bubble; subscribe directly and also handle
    // a small Blob finishing before this effect attaches its listener.
    const loaded = () => {
      element.track.mode = 'showing';
      setStatus(`Browser loaded ${element.track.cues?.length ?? 0} caption cues. Timing remains manually reviewable.`);
    };
    const failed = () => setStatus('The browser could not parse this caption track. Download the verified file for inspection.');
    element.addEventListener('load', loaded); element.addEventListener('error', failed);
    if (element.readyState === 2) loaded();
    else if (element.readyState === 3) failed();
    return () => { element.removeEventListener('load', loaded); element.removeEventListener('error', failed); };
  }, [captionUrl]);
  async function attach() {
    if (!loadCaptions || inFlight.current || captionUrl) return;
    inFlight.current = true; setLoading(true); setStatus('Checking caption bytes…');
    try {
      const blob = await loadCaptions();
      if (!live.current) return;
      if (blob.size > 1_000_000 || blob.type !== 'text/vtt') throw new Error('UNSUPPORTED_CAPTIONS');
      const next = URL.createObjectURL(blob); allocated.current = next; setCaptionUrl(next);
      setStatus('Caption bytes verified. Waiting for the browser to load the track.');
    } catch { if (live.current) setStatus('Captions could not be verified or loaded. The video remains available.'); }
    finally { inFlight.current = false; if (live.current) setLoading(false); }
  }
  return <div className="verified-video">
    <video src={url} controls preload="metadata" aria-label="Generated video preview">
      {captionUrl && <track ref={trackElement} src={captionUrl} kind="captions" srcLang="en" label="English · verified caption file" default />}
    </video>
    {loadCaptions && <button disabled={loading || !!captionUrl} onClick={() => void attach()}>Load verified captions</button>}
    {status && <p role="status" className="fine">{status}</p>}
  </div>;
}
