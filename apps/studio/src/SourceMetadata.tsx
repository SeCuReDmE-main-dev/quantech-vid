import type { SourceAsset } from './contracts';

export function SourceMetadata({ source }: { source: SourceAsset }) {
  const original = source.provenance.original;
  return <>
    <strong>{original?.name ?? source.media_type}</strong>
    {original && <small>Original: {original.media_type} · {original.size.toLocaleString()} bytes</small>}
    <small>{original ? 'Render derivative: ' : 'Admitted asset: '}{source.media_type} · {(source.size / 1024).toFixed(1)} KB</small>
    <code>{source.id.slice(0, 16)}…</code>
    <span>{source.rights.basis} · declaration, not a verified license</span>
    <details className="source-evidence"><summary>Inspect source evidence</summary>
      <dl><dt>Admitted asset SHA-256</dt><dd><code>{source.sha256}</code></dd>
        {original ? <><dt>Original SHA-256</dt><dd><code>{original.sha256}</code></dd>
          <dt>Transformation</dt><dd>{original.transformation === 'literal-text-preview-v1'
            ? 'Bounded literal-text excerpt to PNG. Not a conversion of the full manuscript.'
            : original.transformation === 'glb-four-view-png-v1'
              ? 'Four fixed views of a plain GLB model. Static PNG, not an animated or editable imported 3D scene. Original retained separately.'
              : 'RGB PNG derivative. Original image retained separately.'}</dd></>
          : <><dt>Original-file linkage</dt><dd>No structured original descriptor for this source. Do not infer one from its type or a provenance note.</dd></>}
        <dt>Source declaration</dt><dd>{source.provenance.origin}</dd>
        <dt>Rights reference</dt><dd>{source.rights.reference}</dd>
      </dl>
      <p className="fine">Fingerprints identify bytes, not truth or permission. Original retention and deletion controls are not yet complete.</p>
    </details>
  </>;
}
