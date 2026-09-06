import { useEffect, useRef, useState } from 'react';
import { createSceneRenderer, type SceneRenderer } from '@quantech/scene3d';
import type { Visual3D } from './contracts';

export default function Scene3DPreview({ visual, title, duration, portrait }: {
  visual: Visual3D; title: string; duration: number; portrait: boolean;
}) {
  const holder = useRef<HTMLDivElement>(null), renderer = useRef<SceneRenderer | null>(null);
  const animation = useRef(0);
  const [time, setTime] = useState(0), [playing, setPlaying] = useState(false), [status, setStatus] = useState('Preparing local WebGL preview');
  const configKey = JSON.stringify({ ...visual, title, duration, portrait });
  function stop() { cancelAnimationFrame(animation.current); animation.current = 0; setPlaying(false); }
  useEffect(() => {
    const host = holder.current; if (!host) return;
    const canvas = document.createElement('canvas');
    canvas.setAttribute('aria-label', 'Procedural 3D representation, not measured evidence');
    canvas.setAttribute('role', 'img'); host.appendChild(canvas);
    let instance: SceneRenderer | null = null;
    try {
      instance = createSceneRenderer(canvas, { ...visual, title, duration },
        portrait ? { width: 540, height: 960 } : { width: 960, height: 540 });
      renderer.current = instance; instance.renderAt(0); setTime(0); setStatus('Local WebGL frame rendered. Synthetic representation, not evidence.');
    } catch { setStatus('WebGL preview unavailable. Keep editing manually; no rendered result is claimed.'); }
    return () => { stop(); renderer.current = null; instance?.dispose(); instance?.renderer.forceContextLoss(); canvas.remove(); };
  }, [configKey]);
  function seek(value: number) {
    stop(); if (!renderer.current) return;
    try { renderer.current.renderAt(value); setTime(value); } catch { setStatus('The preview stopped. No production was started.'); }
  }
  function play() {
    if (playing) { stop(); return; }
    if (!renderer.current) return;
    const started = performance.now(); const offset = time >= duration ? 0 : time;
    setPlaying(true);
    const tick = (now: number) => {
      if (!renderer.current) return;
      const next = Math.min(duration, offset + (now - started) / 1000);
      try { renderer.current.renderAt(next); setTime(next); }
      catch { stop(); setStatus('The preview stopped. No production was started.'); return; }
      if (next >= duration) stop(); else animation.current = requestAnimationFrame(tick);
    };
    animation.current = requestAnimationFrame(tick);
  }
  return <section className="three-preview" aria-label="Local 3D preview">
    <div ref={holder} className={portrait ? 'three-canvas portrait' : 'three-canvas'} />
    <p role="status" className="fine">{status}</p>
    <label>3D preview time<input type="range" min={0} max={duration} step={0.05} value={time} disabled={!renderer.current}
      onChange={e => seek(Number(e.target.value))} /></label>
    <div className="toolbar"><button onClick={play} disabled={!renderer.current}>{playing ? 'Pause 3D preview' : 'Play 3D preview'}</button>
      <span>{time.toFixed(2)} / {duration.toFixed(2)} s</span></div>
    <p className="fine">This is a local preview, not an approved export. Its explicit clock and scene module are shared with the production renderer.</p>
  </section>;
}
