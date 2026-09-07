import type { Visual3D } from './contracts';

export const SYNTHETIC_AVATAR_STYLES = [
  { id: 'orbital-guide', name: 'Orbital guide', description: 'Teal geometry with a bounded rotation.',
    visual: { kind: 'synthetic-avatar', accent: '#14B8A6', animation: 'spin',
      lines: ['Fictional geometric guide', 'Human review required'] } },
  { id: 'signal-mosaic', name: 'Signal mosaic', description: 'Violet geometry with a gentle pulse.',
    visual: { kind: 'synthetic-avatar', accent: '#8B5CF6', animation: 'pulse',
      lines: ['Synthetic geometric host', 'Human review required'] } },
] as const satisfies readonly { id: string; name: string; description: string; visual: Visual3D }[];

export function SyntheticAvatarStyle({ visual, disabled, onChange }: {
  visual: Visual3D; disabled: boolean; onChange: (visual: Visual3D) => void;
}) {
  if (visual.kind !== 'synthetic-avatar') return null;
  return <section aria-label="Synthetic avatar style">
    <p><strong>Fictional geometric fixture only.</strong> No real-person likeness, identity, photo, or voice is created.</p>
    <fieldset disabled={disabled}><legend>Choose a synthetic fixture style</legend>
      {SYNTHETIC_AVATAR_STYLES.map(style => {
        const selected = visual.accent === style.visual.accent && visual.animation === style.visual.animation &&
          JSON.stringify(visual.lines) === JSON.stringify(style.visual.lines);
        return <button type="button" key={style.id} aria-pressed={selected}
          onClick={() => onChange({ ...style.visual, lines: [...style.visual.lines] })}>
          <strong>{style.name}</strong> — {style.description}
        </button>;
      })}
    </fieldset>
    <p className="fine">Applying a style changes only the undoable local draft. Review and save it before preparing a new plan; no render starts here.</p>
  </section>;
}
