import type { NarrationMode } from './contracts';

export function NarrationSelector({ value, disabled, onChange }: {
  value: NarrationMode; disabled: boolean; onChange: (value: NarrationMode) => void;
}) {
  return <label>Narration engine<select value={value} disabled={disabled}
    onChange={event => onChange(event.target.value === 'local_kokoro_cpu' ? 'local_kokoro_cpu' : 'silent')}>
    <option value="silent">Silent · no voice synthesis</option>
    <option value="local_kokoro_cpu">Experimental local CPU · English stock voice</option>
  </select><span className="fine">{value === 'silent'
    ? 'No voice provider is invoked. Narration text remains editable.'
    : 'Requires a separately qualified server runtime. Stock af_heart only, 1–1,000 characters. Planning checks availability; choosing this option does not synthesize audio or approve a render. No cloud fallback.'}</span></label>;
}
