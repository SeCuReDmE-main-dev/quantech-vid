import { describe, expect, it, vi } from 'vitest';
import { StudioError } from '../src/api/client';
import { TOOL_NAMES, type ToolDefinition, type ToolResult } from '../src/tools/contracts';
import { cloneBoundedJSON, WebMCPBridge, type ModelContext, type RegisteredStudioTool, type ToolContext, type ToolEvent } from '../src/tools/webmcp';
import { applyProposal } from '../src/tools/proposals';
import { revision } from './fixtures';

const binding = { project_id: 'prj_fixture', revision: 1 };
const initial: ToolContext = { projectId: binding.project_id, revision: 1, dirty: false, busy: false };
const definitions: ToolDefinition[] = TOOL_NAMES.map(name => ({ name, description: `${name} fixture contract`,
  input_schema: { type: 'object', additionalProperties: false, properties: { project_id: { type: 'string' }, revision: { type: 'integer' } }, required: ['project_id', 'revision'] },
  output_schema: { type: 'object' }, annotations: { read_only: !['quantech_stage_render', 'quantech_run_approved_render'].includes(name), idempotent: true, open_world: false } }));
function harness() {
  let state = { ...initial };
  const tools = new Map<string, RegisteredStudioTool>();
  const context: ModelContext = { registerTool: vi.fn((tool, options) => { tools.set(tool.name, tool); options?.signal?.addEventListener('abort', () => tools.delete(tool.name)); }),
    unregisterTool: vi.fn(name => { tools.delete(name); }) };
  const invoke = vi.fn(async (name: typeof TOOL_NAMES[number]): Promise<ToolResult> => ({ ok: true, tool: name, result: { effect: 'read_only' }, error: null }));
  const receive = vi.fn(); const uncertain = vi.fn(); const busy = vi.fn();
  const bridge = new WebMCPBridge(context, () => state, invoke, receive, uncertain, busy);
  return { bridge, context, tools, invoke, receive, uncertain, busy, state: (next: Partial<ToolContext>) => { state = { ...state, ...next }; } };
}
describe('eight tools, one server catalogue, no human approval surface', () => {
  it('registers exact server schemas, closed hints and only the eight allowed functions', async () => {
    const h = harness(); await h.bridge.register(definitions);
    expect([...h.tools.keys()]).toEqual(TOOL_NAMES);
    for (const definition of definitions) {
      expect(h.tools.get(definition.name)?.inputSchema).toEqual(definition.input_schema);
      expect(h.tools.get(definition.name)?.inputSchema).not.toBe(definition.input_schema);
      expect(h.tools.get(definition.name)?.annotations.untrustedContentHint).toBe(true);
    }
    expect([...h.tools.keys()].filter(name => /approv/.test(name))).toEqual(['quantech_run_approved_render']);
    await h.bridge.close(); expect(h.tools.size).toBe(0);
  });
  it('rejects stale project, unsaved draft, closed session and cancelled calls before any request', async () => {
    const h = harness(); await h.bridge.register(definitions);
    const execute = h.tools.get(TOOL_NAMES[0])!.execute;
    expect((await execute({ ...binding, project_id: 'another-project' })).ok).toBe(false);
    h.state({ dirty: true }); expect((await execute(binding)).error?.code).toBe('TOOL_CONTEXT_CHANGED');
    h.state({ dirty: false }); const abort = new AbortController(); abort.abort();
    expect((await execute(binding, { signal: abort.signal })).ok).toBe(false);
    await h.bridge.close(); expect((await execute(binding)).error?.code).toBe('TOOL_SESSION_CLOSED');
    expect(h.invoke).not.toHaveBeenCalled();
  });
  it('does not silently coerce functions, nonfinite values, accessors, cycles or oversized text', () => {
    const cyclic: Record<string, unknown> = {}; cyclic.self = cyclic;
    const getter = vi.fn(() => 'secret'); const accessor = Object.defineProperty({}, 'credential', { get: getter, enumerable: true });
    for (const value of [{ n: NaN }, { n: Infinity }, { fn: () => {} }, { n: undefined }, cyclic, accessor, { text: 'a'.repeat(65537) }]) {
      expect(() => cloneBoundedJSON(value)).toThrow();
    }
    expect(getter).not.toHaveBeenCalled();
    expect(cloneBoundedJSON({ ...binding, list: [1, null, 'safe'] })).toEqual({ ...binding, list: [1, null, 'safe'] });
  });
  it('allows one request at a time and discards a proposal after a project switch', async () => {
    const h = harness(); let finish!: (r: ToolResult) => void;
    h.invoke.mockImplementation(() => new Promise(resolve => { finish = resolve; })); await h.bridge.register(definitions);
    const execute = h.tools.get(TOOL_NAMES[0])!.execute;
    const pending = execute(binding);
    expect((await execute(binding)).error?.code).toBe('TOOL_BUSY');
    h.state({ revision: 2 }); finish({ ok: true, tool: TOOL_NAMES[0], result: { effect: 'read_only' }, error: null });
    expect((await pending).error?.code).toBe('TOOL_CONTEXT_CHANGED'); expect(h.receive).not.toHaveBeenCalled();
    expect(h.busy.mock.calls).toEqual([[true], [false]]);
  });
  it('preserves an ambiguous approved render request for same-key recovery, without approving or retrying', async () => {
    const h = harness(); await h.bridge.register(definitions);
    h.invoke.mockRejectedValue(new StudioError('NETWORK_UNAVAILABLE'));
    const args = { ...binding, plan_id: 'plan_fixture', idempotency_key: 'same-key-for-recovery' };
    await h.tools.get('quantech_run_approved_render')!.execute(args);
    expect(h.uncertain).toHaveBeenCalledWith({ tool: 'quantech_run_approved_render', argumentsValue: args });
    expect(h.invoke).toHaveBeenCalledOnce(); expect(h.receive).not.toHaveBeenCalled();
  });
  it('returns copies, not shared data that can mutate UI state after delivery', async () => {
    const h = harness(); await h.bridge.register(definitions);
    const result = await h.tools.get(TOOL_NAMES[0])!.execute(binding);
    result.result!.effect = 'changed-by-caller';
    expect(h.receive.mock.calls[0][0].response.result.effect).toBe('read_only');
  });
  it('cleans up partial registration failure instead of leaving half a catalogue active', async () => {
    const h = harness(); const register = vi.mocked(h.context.registerTool);
    register.mockImplementationOnce(tool => { h.tools.set(tool.name, tool); }).mockRejectedValueOnce(new Error('unsupported'));
    await expect(h.bridge.register(definitions)).rejects.toThrow('unsupported'); expect(h.tools.size).toBe(0);
  });
});
it('turns a reviewed agent proposal into a local draft only, tied to its source hash', () => {
  const rev = revision(); const original = structuredClone(rev.document);
  const event: ToolEvent = { tool: 'quantech_stage_scene_changes', argumentsValue: { ...binding,
    patches: [{ scene_id: original.scenes[0].id, title: { en: 'Reviewed proposal' } }] }, response: {
      ok: true, tool: 'quantech_stage_scene_changes', result: { effect: 'proposal_only', project_hash: rev.document_hash }, error: null } };
  const next = applyProposal(original, event, rev.document_hash);
  expect(next.scenes[0].title.en).toBe('Reviewed proposal'); expect(original).toEqual(rev.document);
  expect(() => applyProposal(original, event, 'b'.repeat(64))).toThrow();
  expect(next).not.toHaveProperty('approval'); expect(next).not.toHaveProperty('revision');
});
