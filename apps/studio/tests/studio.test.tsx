import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Studio } from '../src/Studio';
import { StudioAPI, StudioError } from '../src/api/client';
import { health, job, plan, revision, source } from './fixtures';

beforeEach(() => localStorage.clear());
afterEach(cleanup);
function fixtureAPI() {
  const api = new StudioAPI(vi.fn() as typeof fetch);
  vi.spyOn(api, 'health').mockResolvedValue(health);
  vi.spyOn(api, 'pair').mockResolvedValue({ actor_id: 'operator', expires_in_seconds: 900 });
  vi.spyOn(api, 'sample').mockResolvedValue(source);
  vi.spyOn(api, 'create').mockImplementation(async document => ({ ...revision(), document }));
  vi.spyOn(api, 'plan').mockResolvedValue(plan);
  vi.spyOn(api, 'approve').mockResolvedValue({ grant_id: 'grant', plan_id: plan.id, scope: 'render', status: 'approved' });
  vi.spyOn(api, 'run').mockResolvedValue(job);
  vi.spyOn(api, 'job').mockResolvedValue(job);
  return api;
}
async function pair() {
  fireEvent.change(screen.getByLabelText('One-time operator code'), { target: { value: 'synthetic-operator-code' } });
  fireEvent.click(screen.getByRole('button', { name: 'Pair this studio' }));
  await screen.findByText(/Paired locally/);
}
it('requires separate visible actions for source admission, saving, planning, approval and execution', async () => {
  const api = fixtureAPI(); render(<Studio api={api} />);
  await pair(); expect(api.sample).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Create a synthetic sample' }));
  await screen.findByDisplayValue('My first verified film');
  expect(api.create).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Save project' }));
  await screen.findByText(/Saved revision 1/);
  expect(api.plan).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Prepare render plan' }));
  await screen.findByText('Review this exact plan');
  const approve = screen.getByRole('button', { name: 'Approve this plan' }) as HTMLButtonElement;
  expect(approve.disabled).toBe(true); expect(api.approve).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(approve);
  await screen.findByText(/No render has started/);
  expect(api.approve).toHaveBeenCalledWith(plan.id, plan.project_id, plan.revision);
  expect(api.run).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Run approved render' }));
  await waitFor(() => expect(api.run).toHaveBeenCalledOnce());
  expect(api.run).toHaveBeenCalledWith(plan.id, expect.any(String));
});
it('invalidates a reviewed plan when a scene changes, preserving undo', async () => {
  const api = fixtureAPI(); render(<Studio api={api} />); await pair();
  fireEvent.click(screen.getByRole('button', { name: 'Create a synthetic sample' }));
  await screen.findByDisplayValue('My first verified film');
  fireEvent.click(screen.getByRole('button', { name: 'Save project' })); await screen.findByText(/Saved revision 1/);
  fireEvent.click(screen.getByRole('button', { name: 'Prepare render plan' })); await screen.findByText('Review this exact plan');
  fireEvent.change(screen.getByLabelText('Scene heading'), { target: { value: 'A changed idea' } });
  expect(screen.queryByText('Review this exact plan')).toBeNull();
  expect(api.approve).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
  expect((screen.getByLabelText('Scene heading') as HTMLInputElement).value).toBe('From an idea to a visible result');
});
it('shows unavailable provider capabilities instead of presenting fake login buttons', async () => {
  render(<Studio api={fixtureAPI()} />);
  await screen.findByText('Local server test');
  expect(screen.getAllByText(/Connector not qualified/)).toHaveLength(3);
  expect(screen.queryByRole('button', { name: /Connect ChatGPT/ })).toBeNull();
});
it('does not upload a file merely because it was selected', async () => {
  const api = fixtureAPI(); vi.spyOn(api, 'upload'); render(<Studio api={api} />); await pair();
  const file = new File(['synthetic source'], 'fixture.txt', { type: 'text/plain' });
  fireEvent.change(screen.getByLabelText('Select a local file'), { target: { files: [file] } });
  expect(screen.getByText(/fixture.txt/)).toBeTruthy();
  expect(api.upload).not.toHaveBeenCalled();
  expect((screen.getByRole('button', { name: 'Admit this selected file' }) as HTMLButtonElement).disabled).toBe(true);
});

it('prevents scene navigation from discarding an unapplied detail form and stranding the production lock', async () => {
  render(<Studio api={fixtureAPI()} />); await pair();
  fireEvent.click(screen.getByRole('button', { name: 'Create a synthetic sample' }));
  await screen.findByDisplayValue('My first verified film');
  fireEvent.click(screen.getByRole('button', { name: 'Add scene' }));
  fireEvent.click(screen.getByRole('button', { name: 'Add a statement' }));
  const firstScene = screen.getByRole('button', { name: 'Select scene 1: From an idea to a visible result' }) as HTMLButtonElement;
  expect(firstScene.disabled).toBe(true);
  expect((screen.getByRole('button', { name: 'Save project' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(firstScene);
  expect(screen.getByLabelText('Statement')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel statement edit' }));
  expect(firstScene.disabled).toBe(false);
  fireEvent.click(firstScene);
  expect((screen.getByLabelText('Scene heading') as HTMLInputElement).value).toBe('From an idea to a visible result');
});

it.each([['APPROVAL_REQUIRED', 403, false], ['NETWORK_UNAVAILABLE', 0, true]] as const)(
  'distinguishes an authoritative %s refusal from an uncertain request outcome', async (code, status, uncertain) => {
    const api = fixtureAPI(); vi.mocked(api.run).mockRejectedValueOnce(new StudioError(code, status));
    render(<Studio api={api} />); await pair();
    fireEvent.click(screen.getByRole('button', { name: 'Create a synthetic sample' }));
    await screen.findByDisplayValue('My first verified film');
    fireEvent.click(screen.getByRole('button', { name: 'Save project' })); await screen.findByText(/Saved revision 1/);
    fireEvent.click(screen.getByRole('button', { name: 'Prepare render plan' })); await screen.findByText('Review this exact plan');
    fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: 'Approve this plan' }));
    await screen.findByText(/No render has started/);
    fireEvent.click(screen.getByRole('button', { name: 'Run approved render' })); await screen.findByRole('alert');
    if (uncertain) {
      fireEvent.click(screen.getByRole('button', { name: 'Retry the same request safely' }));
      await waitFor(() => expect(api.run).toHaveBeenCalledTimes(2));
      expect(vi.mocked(api.run).mock.calls[1]).toEqual(vi.mocked(api.run).mock.calls[0]);
    } else {
      const review = screen.getByRole('checkbox') as HTMLInputElement;
      expect(review.disabled).toBe(false); expect(review.checked).toBe(false);
      expect((screen.getByRole('button', { name: 'Run approved render' }) as HTMLButtonElement).disabled).toBe(true);
      expect(screen.queryByText(/outcome of the request is unknown/)).toBeNull();
    }
  });
