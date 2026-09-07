import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { EngineStatus } from '../src/EngineStatus';
import { StudioAPI } from '../src/api/client';
afterEach(cleanup);
it('does not probe on mount and separates authentication from production',async()=>{
  const api=new StudioAPI(vi.fn() as typeof fetch);
  vi.spyOn(api,'inspectEngine').mockResolvedValue({checked_at:'2026-09-07T00:00:00Z',connection:{
    provider:'openai_codex',production:'unavailable',client:{name:'Codex',installation:'installed',runtime:'present',version:{state:'known',value:'0.149.1'}},
    auth:{state:'confirmed',method:'chatgpt',reason_code:'ACCOUNT_READ_CONFIRMED'},
    rights:{state:'unknown',reason_code:'MODEL_RIGHTS_NOT_PROBED'},quota:{state:'unknown',reason_code:'UNKNOWN'},
    reason_codes:['NATIVE_TOOL_ISOLATION_NOT_PROVEN']}});
  render(<EngineStatus api={api} disabled={false}/>);
  expect(api.inspectEngine).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'Inspect Codex · ChatGPT'}));
  await screen.findByText('confirmed · chatgpt');
  expect(screen.getByText(/inspection is not a production capability test/)).toBeTruthy();
  expect(screen.getByText(/Provider sign-in is not studio permission/)).toBeTruthy();
  expect(api.inspectEngine).toHaveBeenCalledOnce();
});
it('blocks inspection while unpaired or production is busy',()=>{
  const api=new StudioAPI(vi.fn() as typeof fetch);vi.spyOn(api,'inspectEngine');
  render(<EngineStatus api={api} disabled/>);
  fireEvent.click(screen.getByRole('button',{name:'Inspect Codex · ChatGPT'}));
  expect(api.inspectEngine).not.toHaveBeenCalled();
});
