import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { NarrationSelector } from '../src/NarrationSelector';
import { StudioError } from '../src/api/client';

afterEach(cleanup);

it('requires an explicit mode choice and has no approval or render control', () => {
  const change = vi.fn();
  render(<NarrationSelector value="silent" disabled={false} onChange={change} />);
  expect(change).not.toHaveBeenCalled();
  expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('silent');
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'local_kokoro_cpu' } });
  expect(change).toHaveBeenCalledExactlyOnceWith('local_kokoro_cpu');
  expect(screen.queryByRole('button')).toBeNull();
});

it('labels experimental availability honestly and locks changes while busy', () => {
  render(<NarrationSelector value="local_kokoro_cpu" disabled={true} onChange={vi.fn()} />);
  expect((screen.getByRole('combobox') as HTMLSelectElement).disabled).toBe(true);
  expect(screen.getByText(/Requires a separately qualified server runtime/)).toBeTruthy();
  expect(new StudioError('LOCAL_VOICE_AUDIO_EXCEEDS_TIMELINE').message).toMatch(/not silently cut/);
  expect(new StudioError('LOCAL_VOICE_UNAVAILABLE').message).toMatch(/No cloud fallback/);
});
