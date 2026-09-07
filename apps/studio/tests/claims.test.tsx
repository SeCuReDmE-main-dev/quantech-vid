import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SceneClaims } from '../src/SceneClaims';
import { claimSchema, documentSchema, type SceneClaim } from '../src/contracts';
import { initialProject } from '../src/editor';
import { source } from './fixtures';

const sourceId = `src_${'a'.repeat(32)}`;
const suspended: SceneClaim = { id: 'claim-one', text: 'A candidate result awaits inspection.', status: 'suspended',
  rationale: 'No confirming evidence has been admitted yet.', evidence: [] };
afterEach(cleanup);

it('keeps suspension and rejects invented verification flags, unknown evidence or an uncited observation', () => {
  expect(claimSchema.parse(suspended).status).toBe('suspended');
  expect(claimSchema.safeParse({ ...suspended, verified: true }).success).toBe(false);
  expect(claimSchema.safeParse({ ...suspended, status: 'observed' }).success).toBe(false);
  const document = initialProject({ ...source, id: sourceId });
  document.scenes[0].claims = [{ ...suspended, evidence: [{ source_asset_id: `src_${'b'.repeat(32)}`, locator: 'page 1' }] }];
  expect(documentSchema.safeParse(document).success).toBe(false);
  document.scenes[0].claims = [suspended, suspended];
  expect(documentSchema.safeParse(document).success).toBe(false);
});

it('leaves a historical document without claims unchanged when parsed', () => {
  const document = initialProject(source);
  expect(documentSchema.parse(document)).toEqual(document);
  expect(documentSchema.parse(document).scenes[0]).not.toHaveProperty('claims');
});

it('stages a statement locally and does not apply or save it on selection', () => {
  const changed = vi.fn(), editing = vi.fn();
  render(<SceneClaims claims={[]} sourceIds={[sourceId]} disabled={false} onChange={changed} onEditingChange={editing} />);
  fireEvent.click(screen.getByRole('button', { name: 'Add a statement' })); expect(editing).toHaveBeenCalledWith(true);
  fireEvent.change(screen.getByLabelText('Statement', { exact: true }), { target: { value: suspended.text } });
  fireEvent.change(screen.getByLabelText('Evidence status'), { target: { value: 'suspended' } });
  fireEvent.change(screen.getByLabelText('Reason for this status'), { target: { value: suspended.rationale } });
  expect(changed).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Apply statement to local draft' }));
  expect(changed).toHaveBeenCalledWith([expect.objectContaining({ status: 'suspended', evidence: [] })]);
  expect(editing).toHaveBeenLastCalledWith(false);
});

it('refuses an uncited observation and preserves the previous statement on cancel', () => {
  const changed = vi.fn(), editing = vi.fn();
  render(<SceneClaims claims={[suspended]} sourceIds={[sourceId]} disabled={false} onChange={changed} onEditingChange={editing} />);
  fireEvent.click(screen.getByRole('button', { name: 'Edit statement 1' }));
  fireEvent.change(screen.getByLabelText('Evidence status'), { target: { value: 'observed' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply statement to local draft' }));
  expect(screen.getByRole('alert')).toBeTruthy(); expect(changed).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel statement edit' }));
  expect(changed).not.toHaveBeenCalled(); expect(editing).toHaveBeenLastCalledWith(false);
});
