import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { sourceSchema, sourceOriginalSchema, planSchema } from '../src/contracts';
import { SourceMetadata } from '../src/SourceMetadata';
import { source, plan } from './fixtures';

afterEach(cleanup);
const original = { name: '<script>.md', sha256: 'b'.repeat(64), size: 460,
  media_type: 'text/markdown' as const, transformation: 'literal-text-preview-v1' as const };

it('preserves structured original metadata, but renders untrusted text only', () => {
  const parsed = sourceSchema.parse({ ...source, provenance: { ...source.provenance, original } });
  const { container } = render(<SourceMetadata source={parsed}/>);
  expect(screen.getByText(original.name)).toBeTruthy();
  expect(screen.getByText(/Original: text\/markdown/)).toBeTruthy();
  expect(screen.getByText(/Render derivative: image\/png/)).toBeTruthy();
  expect(screen.getByText(original.sha256)).toBeTruthy();
  expect(screen.getByText(/Not a conversion of the full manuscript/)).toBeTruthy();
  expect(container.querySelector('script')).toBeNull();
});
it('does not invent an original for older or generated sources and preserves old plan omission', () => {
  render(<SourceMetadata source={sourceSchema.parse(source)}/>);
  expect(screen.getByText(/No structured original descriptor/)).toBeTruthy();
  expect(planSchema.parse(plan)).not.toHaveProperty('original_hashes');
  expect(planSchema.parse({ ...plan, original_hashes: { [`src_${'a'.repeat(32)}`]: original.sha256 } }).original_hashes)
    .toEqual({ [`src_${'a'.repeat(32)}`]: original.sha256 });
});
it('rejects malformed original metadata and never accepts an internal path or invented transformation', () => {
  for (const mutation of [{ sha256: 'invalid' }, { size: -1 }, { size: '460' }, { media_type: 'text/html' },
    { transformation: 'network-conversion' }, { internal_path: 'private-path' }, { name: '../source.md' }])
    expect(sourceOriginalSchema.safeParse({ ...original, ...mutation }).success).toBe(false);
});
