import { StudioError } from '../api/client';
import { catalogSchema, TOOL_NAMES, toolResultSchema, type ToolDefinition, type ToolName, type ToolResult } from './contracts';

// Experimental document.modelContext surface verified against the current W3C draft.
// Never install a polyfill or claim that an ordinary function is native WebMCP.
export interface RegisteredStudioTool {
  name: ToolName; description: string; inputSchema: Record<string, unknown>;
  annotations: { readOnlyHint: boolean; untrustedContentHint: boolean; consequentialHint: boolean };
  execute(input: unknown, options?: { signal?: AbortSignal }): Promise<ToolResult>;
}
export interface ModelContext {
  registerTool(tool: RegisteredStudioTool, options?: { signal?: AbortSignal }): void | Promise<void>;
  unregisterTool?(name: string): void | Promise<void>; // Older experimental implementations.
  getTools?(): Promise<Array<{ name: string; origin: string; [key: string]: unknown }>>;
  executeTool?(tool: { name: string; origin: string }, input: object, options?: { signal?: AbortSignal }): Promise<string>;
}
export function nativeModelContext(): ModelContext | undefined {
  const context = (document as Document & { modelContext?: ModelContext }).modelContext;
  return context && typeof context.registerTool === 'function' ? context : undefined;
}
export type ToolContext = { projectId: string | null; revision: number | null; dirty: boolean; busy: boolean };
export type ToolEvent = { tool: ToolName; argumentsValue: Record<string, unknown>; response: ToolResult };

export async function inspectThroughNativeContext(context: ModelContext, projectId: string, revision: number): Promise<ToolResult> {
  if (!context.getTools || !context.executeTool) throw new StudioError('NATIVE_INSPECTION_UNAVAILABLE');
  let tools: Awaited<ReturnType<NonNullable<ModelContext['getTools']>>>;
  try { tools = await context.getTools(); }
  catch { throw new StudioError('NATIVE_TOOL_DISCOVERY_FAILED'); }
  if (!Array.isArray(tools)) throw new StudioError('NATIVE_TOOL_DISCOVERY_INVALID');
  const ownTool = tools.find(tool => tool.name === 'quantech_inspect_project' && tool.origin === location.origin);
  if (!ownTool) throw new StudioError('TOOL_NOT_FOUND');
  let result: string;
  try { result = await context.executeTool(ownTool, { project_id: projectId, revision, include_document: false },
    { signal: AbortSignal.timeout(10000) }); }
  catch { throw new StudioError('NATIVE_TOOL_EXECUTION_FAILED'); }
  try { return toolResultSchema.parse(JSON.parse(result)); }
  catch { throw new StudioError('NATIVE_TOOL_RESULT_INVALID'); }
}

function fail(tool: ToolName, code: string): ToolResult {
  return { ok: false, tool, result: null, error: { code, message: new StudioError(code).message, retryable: false } };
}
export function cloneBoundedJSON(value: unknown): Record<string, unknown> {
  let count = 0;
  const seen = new Set<object>();
  function visit(item: unknown, depth: number): void {
    if (++count > 10000 || depth > 20) throw new StudioError('INVALID_TOOL_INPUT');
    if (item === null || typeof item === 'boolean') return;
    if (typeof item === 'string') { if (item.length > 65536) throw new StudioError('INVALID_TOOL_INPUT'); return; }
    if (typeof item === 'number' && Number.isFinite(item)) return;
    if (typeof item !== 'object' || seen.has(item)) throw new StudioError('INVALID_TOOL_INPUT');
    if (!Array.isArray(item) && Object.getPrototypeOf(item) !== Object.prototype && Object.getPrototypeOf(item) !== null)
      throw new StudioError('INVALID_TOOL_INPUT');
    seen.add(item);
    for (const [key, descriptor] of Object.entries(Object.getOwnPropertyDescriptors(item))) {
      if (key === '__proto__' || key === 'constructor' || key === 'prototype' || descriptor.get || descriptor.set)
        throw new StudioError('INVALID_TOOL_INPUT');
      visit(descriptor.value, depth + 1);
    }
    seen.delete(item);
  }
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new StudioError('INVALID_TOOL_INPUT');
  visit(value, 0);
  const text = JSON.stringify(value);
  if (new TextEncoder().encode(text).byteLength > 262144) throw new StudioError('TOOL_INPUT_TOO_LARGE');
  return JSON.parse(text) as Record<string, unknown>;
}

export class WebMCPBridge {
  private lifetime = new AbortController();
  private registered: ToolName[] = [];
  private executing = false;
  constructor(private context: ModelContext, private snapshot: () => ToolContext,
    private invoke: (name: ToolName, args: Record<string, unknown>, signal: AbortSignal) => Promise<ToolResult>,
    private onResult: (event: ToolEvent) => void,
    private onUncertain: (event: { tool: ToolName; argumentsValue: Record<string, unknown> }) => void = () => {},
    private onBusy: (busy: boolean) => void = () => {}) {}

  async register(definitions: ToolDefinition[]) {
    catalogSchema.parse({ tools: definitions });
    try {
      for (const definition of definitions) {
        if (this.lifetime.signal.aborted) throw new StudioError('TOOL_SESSION_CLOSED');
        const name = definition.name;
        await this.context.registerTool({ name, description: definition.description,
          inputSchema: structuredClone(definition.input_schema),
          annotations: { readOnlyHint: definition.annotations.read_only,
            untrustedContentHint: true, consequentialHint: name === 'quantech_run_approved_render' },
          execute: (input, options) => this.execute(name, input, options?.signal),
        }, { signal: this.lifetime.signal });
        this.registered.push(name);
      }
    } catch (error) { await this.close(); throw error; }
  }

  async execute(name: ToolName, input: unknown, signal?: AbortSignal): Promise<ToolResult> {
    if (this.lifetime.signal.aborted || signal?.aborted) return fail(name, 'TOOL_SESSION_CLOSED');
    if (!TOOL_NAMES.includes(name)) return fail(name, 'TOOL_NOT_FOUND');
    let args: Record<string, unknown>;
    try { args = cloneBoundedJSON(input); } catch { return fail(name, 'INVALID_TOOL_INPUT'); }
    const initial = this.snapshot();
    if (!initial.projectId || initial.dirty || args.project_id !== initial.projectId || args.revision !== initial.revision)
      return fail(name, 'TOOL_CONTEXT_CHANGED');
    if (initial.busy || this.executing) return fail(name, 'TOOL_BUSY');
    this.executing = true;
    this.onBusy(true);
    const cancellation = AbortSignal.any([this.lifetime.signal, ...(signal ? [signal] : []), AbortSignal.timeout(30000)]);
    try {
      const response = await this.invoke(name, args, cancellation);
      const now = this.snapshot();
      if (response.tool !== name) throw new StudioError('INVALID_RESPONSE');
      // Never apply a delayed proposal or approval to a new tab/project revision.
      if (cancellation.aborted || initial.projectId !== now.projectId || initial.revision !== now.revision || now.dirty) {
        if (name === 'quantech_run_approved_render') this.onUncertain({ tool: name, argumentsValue: args });
        return fail(name, 'TOOL_CONTEXT_CHANGED');
      }
      const copy = structuredClone(response);
      this.onResult({ tool: name, argumentsValue: structuredClone(args), response: structuredClone(copy) });
      return copy;
    } catch (error) {
      if (name === 'quantech_run_approved_render' && (!(error instanceof StudioError) || !error.status || error.status >= 500))
        this.onUncertain({ tool: name, argumentsValue: args });
      return fail(name, error instanceof StudioError ? error.code : 'TOOL_REQUEST_FAILED');
    } finally { this.executing = false; this.onBusy(false); }
  }

  async close() {
    this.lifetime.abort();
    for (const name of this.registered) {
      try { await this.context.unregisterTool?.(name); } catch { /* Session check still closes captured callbacks. */ }
    }
    this.registered = [];
  }
}
