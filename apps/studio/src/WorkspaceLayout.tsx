import { useState, type CSSProperties, type ReactNode } from 'react';

export function WorkspaceLayout({ children, busy }: { children: ReactNode; busy: boolean }) {
  const [sources, setSources] = useState(100);
  const [details, setDetails] = useState(110);
  const bounded = (value: string) => Math.min(180, Math.max(80, Number(value) || 100));
  const style = { '--sources-weight': `${sources / 100}fr`, '--details-weight': `${details / 100}fr` } as CSSProperties;
  return <>
    <details className="layout-controls panel">
      <summary>Adjust workspace panels</summary>
      <p id="layout-help">Use the sliders or arrow keys to resize the desktop panels. Narrow screens stack automatically. These view settings never change a project or its approval.</p>
      <div className="layout-sliders">
        <label>Sources panel width<input type="range" min="80" max="180" step="10" value={sources}
          aria-describedby="layout-help" aria-valuetext={`${sources} percent of default weight`}
          onChange={event => setSources(bounded(event.target.value))} /></label>
        <label>Details panel width<input type="range" min="80" max="180" step="10" value={details}
          aria-describedby="layout-help" aria-valuetext={`${details} relative width units`}
          onChange={event => setDetails(bounded(event.target.value))} /></label>
        <button type="button" onClick={() => { setSources(100); setDetails(110); }}>Reset panel widths</button>
      </div>
    </details>
    <main id="workspace" className="workspace" style={style} aria-busy={busy}>{children}</main>
  </>;
}
