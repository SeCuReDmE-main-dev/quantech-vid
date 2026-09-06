import { expect, it, vi } from 'vitest';
import { inspectThroughNativeContext, type ModelContext } from '../src/tools/webmcp';

it('uses only the same-origin read-only tool and checks its returned contract', async () => {
  const ownTool = { name: 'quantech_inspect_project', origin: location.origin };
  const response = { ok: true, tool: ownTool.name, result: { effect: 'read_only' }, error: null };
  const native: ModelContext = { registerTool: vi.fn(), getTools: vi.fn().mockResolvedValue([
    { name: ownTool.name, origin: 'https://unrelated.example' }, ownTool]), executeTool: vi.fn().mockResolvedValue(JSON.stringify(response)) };
  await expect(inspectThroughNativeContext(native, 'project', 1)).resolves.toEqual(response);
  expect(native.executeTool).toHaveBeenCalledWith(ownTool, { project_id: 'project', revision: 1, include_document: false }, expect.any(Object));
});

it('separates method absence, native discovery refusal and malformed output', async () => {
  const native: ModelContext = { registerTool: vi.fn() };
  await expect(inspectThroughNativeContext(native, 'project', 1)).rejects.toMatchObject({ code: 'NATIVE_INSPECTION_UNAVAILABLE' });
  native.getTools = vi.fn().mockRejectedValue(new Error('private browser diagnostic'));
  native.executeTool = vi.fn();
  await expect(inspectThroughNativeContext(native, 'project', 1)).rejects.toMatchObject({ code: 'NATIVE_TOOL_DISCOVERY_FAILED' });
  native.getTools = vi.fn().mockResolvedValue([{ name: 'quantech_inspect_project', origin: location.origin }]);
  native.executeTool = vi.fn().mockResolvedValue('<invalid result>');
  await expect(inspectThroughNativeContext(native, 'project', 1)).rejects.toMatchObject({ code: 'NATIVE_TOOL_RESULT_INVALID' });
});
