import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { WorkspaceLayout } from '../src/WorkspaceLayout';

afterEach(cleanup);

it('changes only layout weights and resets both accessible sliders', () => {
  render(<WorkspaceLayout busy={false}><p>Preserved project content</p></WorkspaceLayout>);
  const main = screen.getByRole('main');
  fireEvent.click(screen.getByText('Adjust workspace panels'));
  const sources = screen.getByRole('slider', { name: 'Sources panel width' });
  const details = screen.getByRole('slider', { name: 'Details panel width' });
  fireEvent.change(sources, { target: { value: '180' } });
  fireEvent.change(details, { target: { value: '80' } });
  expect(main.style.getPropertyValue('--sources-weight')).toBe('1.8fr');
  expect(main.style.getPropertyValue('--details-weight')).toBe('0.8fr');
  expect(screen.getByText('Preserved project content')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Reset panel widths' }));
  expect(main.style.getPropertyValue('--sources-weight')).toBe('1fr');
  expect(main.style.getPropertyValue('--details-weight')).toBe('1.1fr');
  expect(screen.queryByRole('button', { name: /approve|render/i })).toBeNull();
});

it('keeps navigation and busy semantics without persisting a project or credential', () => {
  render(<WorkspaceLayout busy={true}><p>Working</p></WorkspaceLayout>);
  expect(screen.getByRole('main').id).toBe('workspace');
  expect(screen.getByRole('main').getAttribute('aria-busy')).toBe('true');
  expect(screen.getByLabelText('Sources panel width').getAttribute('min')).toBe('80');
  expect(screen.getByLabelText('Sources panel width').getAttribute('max')).toBe('180');
});
