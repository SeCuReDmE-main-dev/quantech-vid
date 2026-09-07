// @vitest-environment node
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, it } from 'vitest';

// CSS imports are stubbed by Vitest's default CSS handling. Read the actual
// stylesheet in the Node environment so this check cannot validate a stub.
const css = readFileSync(resolve('src/studio.css'), 'utf8');

// WCAG relative luminance, taken from the actual stylesheet, not a duplicated
// palette that could stay green while the interface changes. Disabled controls,
// rendered images and browser-native menus still need separate inspection.
function luminance(value: string): number {
  const hex = value.length === 4 ? '#' + value.slice(1).split('').map(c => c+c).join('') : value;
  const [r,g,b] = hex.slice(1).match(/../g)!.map(c => parseInt(c,16)/255)
    .map(c => c <= .04045 ? c/12.92 : ((c+.055)/1.055)**2.4);
  return .2126*r + .7152*g + .0722*b;
}
function contrast(a:string,b:string):number {
  const x=luminance(a),y=luminance(b); return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);
}
it.each(['studio','theme-light'])('keeps %s text, control boundaries and focus colors above the measured thresholds', selector => {
  const match=css.match(new RegExp(`\\.${selector}\\s*\\{([^}]+)\\}`));
  expect(match, `Missing palette for .${selector}`).not.toBeNull();
  const rule=match![1];
  const palette=Object.fromEntries([...rule.matchAll(/--([a-z-]+):\s*(#[a-f0-9]{3,6})/gi)].map(m=>[m[1],m[2]]));
  for (const bg of ['bg','panel','raised']) {
    for (const fg of ['text','muted','accent','gold','danger'])
      expect(contrast(palette[fg],palette[bg]), `${selector}: ${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
    expect(contrast(palette.line,palette[bg]), `${selector}: control edge on ${bg}`).toBeGreaterThanOrEqual(3);
  }
  expect(contrast(palette['on-accent'],palette.accent)).toBeGreaterThanOrEqual(4.5);
  expect(contrast(palette.accent,palette.hover)).toBeGreaterThanOrEqual(3);
});
