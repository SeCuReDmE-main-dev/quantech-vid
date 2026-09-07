import { z } from 'zod';
import { sourceSchema, revisionSchema, projectSummarySchema, planSchema, jobSchema, sessionSchema, agentSchema,
  healthSchema, type HumanSession, type ProjectDocument, type RunnerSession } from '../contracts';
import { catalogSchema, toolResultSchema, type ToolName } from '../tools/contracts';
import type { SourceAsset, NarrationMode } from '../contracts';
import { engineStatusSchema, type EngineProvider } from './engine-contracts';

const messages: Record<string, string> = {
  LOCAL_VOICE_UNAVAILABLE: 'The server has no qualified local voice runtime. Choose silent rendering or ask the operator to qualify the isolated CPU runtime. No cloud fallback was requested.',
  LOCAL_VOICE_LOCALE_UNSUPPORTED: 'This experimental stock voice supports English only.',
  LOCAL_VOICE_VOICE_NOT_ALLOWED: 'Only the stock af_heart voice is allowed. Personal and cloned voices are not supported.',
  LOCAL_VOICE_TEXT_INVALID: 'Use 1–1,000 characters of English narration for this local voice test.',
  LOCAL_VOICE_AUDIO_EXCEEDS_TIMELINE: 'The spoken audio is longer than this film. Extend the scene durations, save and approve a new plan; speech was not silently cut.',
  LOCAL_VOICE_RESOURCE_INTEGRITY_FAILED: 'The voice runtime changed or failed its integrity check. No replacement provider was used; prepare a new plan after operator verification.',
  LOCAL_VOICE_INTEGRITY_FAILED: 'The server could not verify its voice runtime. No cloud fallback was used; ask the operator to check the resources.',
  LOCAL_VOICE_BINDING_MISMATCH: 'The voice resources no longer match this approved plan. Review a new plan after the runtime is verified.',
  LOCAL_VOICE_SYNTHESIS_TIMEOUT: 'Local speech synthesis exceeded its time limit and was stopped. No cloud provider was substituted.',
  LOCAL_VOICE_SYNTHESIS_FAILED: 'Local speech synthesis failed. Inspect the existing job; no automatic retry or cloud fallback was made.',
  AUTHENTICATION_REQUIRED: 'Pair this studio with the local server first.',
  SESSION_EXPIRED: 'This local session expired. Pair again; your draft is preserved.',
  PAIRING_REJECTED: 'This one-time code is invalid, expired, or already used. Create a fresh code in the local server.',
  REVISION_CONFLICT: 'The project changed elsewhere. Reopen its latest revision before saving.',
  SOURCE_INTEGRITY_FAILED: 'A source changed after selection. Select it again and review a new plan.',
  SOURCE_ORIGINAL_INTEGRITY_FAILED: 'The linked original file is missing or changed. Re-admit a trusted copy and review a new plan. The existing derivative does not replace that proof.',
  HUMAN_APPROVAL_REQUIRED: 'Review and approve this exact plan before running it.',
  APPROVAL_REQUIRED: 'This plan has no active approval. Review it again and approve before retrying; no new render was queued.',
  APPROVAL_INTEGRITY_FAILED: 'The authorization could not be verified. Review a new approval; do not bypass this check.',
  STALE_PROJECT_REVISION: 'This plan is for an older revision. Open the current project and prepare a new plan.',
  AGENT_SCOPE_REJECTED: 'This agent is not authorized for the selected project and revision. Reconnect its tools from this studio.',
  GRANT_EXPIRED: 'The approval expired. Review a fresh approval before trying again.',
  GRANT_REJECTED: 'This approval cannot authorize the requested render.',
  NETWORK_IMPORT_DISABLED: 'URL importing is disabled. Use an explicitly selected local source.',
  BODY_TOO_LARGE: 'This input exceeds the local server limit. Use a smaller source.',
  ORIGIN_REJECTED: 'The studio address does not match the server’s configured origin.',
  HOST_REJECTED: 'The server rejected this host. Open the configured loopback studio address.',
  UNSUPPORTED_SOURCE_MEDIA: 'Select a nonempty PNG, JPEG, WebP or UTF-8 text/Markdown file under 50 MB.',
  INVALID_UTF8_SOURCE: 'This text or Markdown file is not valid UTF-8. Save a UTF-8 copy and select that copy.',
  SOURCE_TOO_LARGE: 'This text or Markdown exceeds the 200,000-character limit. Select a smaller, explicitly chosen excerpt.',
  ARTIFACT_INTEGRITY_FAILED: 'The file does not match its receipt. It was not opened. Inspect the existing job before retrying.',
  TOOL_CONTEXT_CHANGED: 'The selected project or revision changed. Save your work and inspect the current project first.',
  TOOL_SESSION_CLOSED: 'Agent tools are disconnected. Enable them again from the visible studio controls.',
  TOOL_BUSY: 'Another studio action is in progress. Wait for its result before starting another one.',
  NATIVE_TOOL_DISCOVERY_FAILED: 'The browser rejected native tool discovery. Registration is separate from execution; the manual path remains available.',
  NATIVE_TOOL_DISCOVERY_INVALID: 'The browser returned a tool catalogue that does not match this experimental API version. No tool was invoked.',
  NATIVE_TOOL_EXECUTION_FAILED: 'Native discovery succeeded, but the browser rejected the read-only tool invocation. No approval or render was requested.',
  NATIVE_TOOL_RESULT_INVALID: 'The native tool returned an unrecognized result. Its execution is not counted as verified.',
};
export class StudioError extends Error {
  constructor(public readonly code: string, public readonly status = 0) {
    super(messages[code] ?? (status === 401 ? 'The session is no longer valid. Pair again; keep your draft.' :
      'The request could not be verified. Your draft is preserved; inspect the current status before retrying.'));
    this.name = 'StudioError';
  }
}

type Credentials = HumanSession | RunnerSession;
export class StudioAPI {
  // This instance lives in memory only; never put credentials in URLs or browser storage.
  private human?: HumanSession;
  private runner?: RunnerSession;
  private runnerScope?: { project_id: string; revision: number };
  private runnerRegistration?: Promise<string>;
  private previews = new Map<string, Blob>();
  private previewEpoch = 0;
  clearPreviews() { this.previews.clear(); this.previewEpoch++; }
  constructor(private readonly fetcher: typeof fetch = globalThis.fetch.bind(globalThis)) {}
  forgetSessions() { this.clearPreviews(); this.human = undefined; this.runner = undefined; this.runnerScope = undefined; }
  async disconnect() {
    try { await this.request('/session', z.undefined(), 'DELETE', undefined, this.operator()); }
    finally { this.forgetSessions(); }
  }

  private async request<T>(path: string, schema: z.ZodType<T>, method = 'GET',
    body?: unknown, credentials?: Credentials, idempotencyKey?: string, signal?: AbortSignal, timeoutMs = 20000): Promise<T> {
    const headers = new Headers({ Accept: 'application/json' });
    if (body !== undefined) headers.set('Content-Type', 'application/json');
    if (credentials) {
      headers.set('Authorization', `Bearer ${credentials.actor_type === 'human' ? credentials.session_token : credentials.client_token}`);
      if (credentials.actor_type === 'human' && method !== 'GET') headers.set('X-CSRF-Token', credentials.csrf_token);
    }
    if (idempotencyKey) headers.set('Idempotency-Key', idempotencyKey);
    let response: Response;
    try {
      response = await this.fetcher(`/api/v2${path}`, { method, headers, credentials: 'omit',
        cache: 'no-store', redirect: 'error', signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(timeoutMs)]) : AbortSignal.timeout(timeoutMs),
        body: body === undefined ? undefined : JSON.stringify(body) });
    } catch { throw new StudioError('NETWORK_UNAVAILABLE'); }
    if (response.status === 204) return undefined as T;
    if (!response.headers.get('content-type')?.includes('application/json')) throw new StudioError('INVALID_RESPONSE', response.status);
    let payload: unknown;
    try { payload = await response.json(); } catch { throw new StudioError('INVALID_RESPONSE', response.status); }
    if (!response.ok) {
      const failure = z.object({ error: z.object({ code: z.string().regex(/^[A-Z0-9_]{1,80}$/) }) }).safeParse(payload);
      throw new StudioError(failure.success ? failure.data.error.code : 'REQUEST_REJECTED', response.status);
    }
    const parsed = schema.safeParse(payload);
    if (!parsed.success) throw new StudioError('INVALID_RESPONSE', response.status);
    return parsed.data;
  }
  private operator() { if (!this.human) throw new StudioError('AUTHENTICATION_REQUIRED'); return this.human; }
  health() { return this.request('/health', healthSchema); }
  async inspectEngine(provider: EngineProvider) {
    const result = await this.request(`/engines/${encodeURIComponent(provider)}/inspect`,engineStatusSchema,'POST',{},this.operator());
    if(result.connection.provider!==provider)throw new StudioError('INVALID_RESPONSE');
    return result;
  }
  async pair(operatorCode: string) {
    const session = await this.request('/pair', sessionSchema, 'POST', { operator_code: operatorCode, actor_id: 'local-studio-creator' });
    this.clearPreviews();
    this.human = session;
    return { actor_id: session.actor_id, expires_in_seconds: session.expires_in_seconds };
  }
  async ensureRunner(project_id: string, revision: number): Promise<string> {
    if (!/^prj_[a-f0-9]{32}$/.test(project_id) || !Number.isSafeInteger(revision) || revision < 1)
      throw new StudioError('TOOL_CONTEXT_CHANGED');
    if (this.runner && this.runnerScope?.project_id === project_id && this.runnerScope.revision === revision)
      return this.runner.agent_id;
    if (this.runnerRegistration) {
      await this.runnerRegistration;
      return this.ensureRunner(project_id, revision);
    }
    if (!this.runnerRegistration) this.runnerRegistration = (async () => {
      const operator = this.operator();
      if (this.runner) {
        await this.request(`/agents/${encodeURIComponent(this.runner.agent_id)}`, z.undefined(), 'DELETE', undefined, operator);
        this.runner = undefined; this.runnerScope = undefined;
      }
      const runner = await this.request('/agents', agentSchema, 'POST', {
        agent_id: `studio-${crypto.randomUUID()}`, label: 'Human-directed local studio renderer', project_id, revision,
      }, operator);
      if (this.human !== operator) throw new StudioError('TOOL_SESSION_CLOSED');
      this.runner = runner; this.runnerScope = { project_id, revision };
      return runner.agent_id;
    })().finally(() => { this.runnerRegistration = undefined; });
    return this.runnerRegistration;
  }
  catalog() {
    if (!this.runner) throw new StudioError('TOOL_SESSION_CLOSED');
    return this.request('/tools/catalog', catalogSchema, 'GET', undefined, this.runner);
  }
  invokeTool(name: ToolName, argumentsValue: Record<string, unknown>, signal?: AbortSignal) {
    if (!this.runner) throw new StudioError('TOOL_SESSION_CLOSED');
    return this.request(`/tools/${encodeURIComponent(name)}`, toolResultSchema, 'POST', argumentsValue, this.runner, undefined, signal);
  }
  sample() { return this.request('/sources/sample', sourceSchema, 'POST', {}, this.operator()); }
  sources() { return this.request('/sources', z.object({ sources: z.array(sourceSchema) }), 'GET', undefined, this.operator()); }
  async sourcePreview(source: SourceAsset): Promise<Blob> {
    const human = this.operator(), epoch = this.previewEpoch;
    if (!/^src_[a-f0-9]{32}$/.test(source.id) || !/^[a-f0-9]{64}$/.test(source.sha256) ||
        !['image/png', 'image/jpeg', 'image/webp'].includes(source.media_type) ||
        !Number.isSafeInteger(source.size) || source.size < 1 || source.size > 10 * 1024 * 1024)
      throw new StudioError('SOURCE_PREVIEW_UNAVAILABLE');
    const key = `${source.id}:${source.sha256}:${source.size}:${source.media_type}`;
    const cached = this.previews.get(key);
    if (cached) { this.previews.delete(key); this.previews.set(key, cached); return cached; }
    let response: Response;
    try { response = await this.fetcher(`/api/v2/sources/${source.id}/preview`, {
      headers: { Authorization: `Bearer ${human.session_token}` }, credentials: 'omit',
      cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(20000),
    }); } catch { throw new StudioError('NETWORK_UNAVAILABLE'); }
    if (!response.ok) throw new StudioError('SOURCE_PREVIEW_UNAVAILABLE', response.status);
    if (response.headers.get('content-type') !== source.media_type ||
        Number(response.headers.get('content-length')) !== source.size || !response.body)
      throw new StudioError('SOURCE_INTEGRITY_FAILED');
    const reader = response.body.getReader(), parts: Uint8Array<ArrayBuffer>[] = []; let size = 0;
    try {
      for (;;) { const part = await reader.read(); if (part.done) break;
        size += part.value.byteLength;
        if (size > source.size) throw new StudioError('SOURCE_INTEGRITY_FAILED');
        parts.push(new Uint8Array(part.value)); }
    } finally { await reader.cancel(); reader.releaseLock(); }
    const blob = new Blob(parts, { type: source.media_type });
    const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())))
      .map(n => n.toString(16).padStart(2, '0')).join('');
    if (size !== source.size || hash !== source.sha256) throw new StudioError('SOURCE_INTEGRITY_FAILED');
    if (epoch !== this.previewEpoch || human !== this.human) throw new StudioError('TOOL_CONTEXT_CHANGED');
    this.previews.set(key, blob);
    while (this.previews.size > 4 || [...this.previews.values()].reduce((sum, item) => sum + item.size, 0) > 20 * 1024 * 1024)
      this.previews.delete(this.previews.keys().next().value!);
    return blob;
  }
  projects() { return this.request('/projects', z.object({ projects: z.array(projectSummarySchema) }), 'GET', undefined, this.operator()); }
  async upload(file: File, basis: 'owned' | 'licensed' | 'public-domain' | 'permission', reference: string) {
    if (!file.size || file.size > 50_000_000 || !/\.(png|jpe?g|webp|txt|md|markdown)$/i.test(file.name)) throw new StudioError('UNSUPPORTED_SOURCE_MEDIA');
    const human = this.operator();
    let response: Response;
    try { response = await this.fetcher('/api/v2/sources/upload', { method: 'POST', credentials: 'omit',
      cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(60000), body: file,
      headers: { Authorization: `Bearer ${human.session_token}`, 'X-CSRF-Token': human.csrf_token,
        'Content-Type': file.type || 'application/octet-stream', 'X-File-Name': encodeURIComponent(file.name),
        'X-Rights-Basis': basis, 'X-Rights-Reference': encodeURIComponent(reference),
        'X-Source-Origin': encodeURIComponent('Explicit browser file selection') },
    }); } catch { throw new StudioError('NETWORK_UNAVAILABLE'); }
    let payload: unknown;
    try { payload = await response.json(); } catch { throw new StudioError('INVALID_RESPONSE', response.status); }
    if (!response.ok) {
      const failure = z.object({ error: z.object({ code: z.string().regex(/^[A-Z0-9_]{1,80}$/) }) }).safeParse(payload);
      throw new StudioError(failure.success ? failure.data.error.code : 'UPLOAD_REJECTED', response.status);
    }
    const parsed = z.object({ asset: sourceSchema, original: z.object({ name: z.string(), size: z.number(),
      sha256: z.string().regex(/^[a-f0-9]{64}$/i), media_type: z.string() }), derived: z.boolean() }).safeParse(payload);
    if (!parsed.success) throw new StudioError('INVALID_RESPONSE');
    return parsed.data;
  }
  async artifact(jobId: string, receipt: { name: string; size: number; sha256: string; media_type: string }): Promise<Blob> {
    // The server repeats ownership/integrity checks. No credential is ever put in the media URL.
    if (receipt.size > 100_000_000) throw new StudioError('PREVIEW_SIZE_LIMIT');
    const human = this.operator();
    let response: Response;
    try { response = await this.fetcher(`/api/v2/renders/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(receipt.name)}`, {
      headers: { Authorization: `Bearer ${human.session_token}` }, credentials: 'omit', cache: 'no-store',
      redirect: 'error', signal: AbortSignal.timeout(60000),
    }); } catch { throw new StudioError('NETWORK_UNAVAILABLE'); }
    if (!response.ok) throw new StudioError('ARTIFACT_UNAVAILABLE', response.status);
    const length = Number(response.headers.get('content-length'));
    if (!Number.isSafeInteger(length) || length !== receipt.size || !response.body) throw new StudioError('ARTIFACT_INTEGRITY_FAILED');
    const reader = response.body.getReader(); const parts: Uint8Array<ArrayBuffer>[] = []; let total = 0;
    try {
      for (;;) {
        const part = await reader.read(); if (part.done) break;
        total += part.value.byteLength;
        if (total > receipt.size) throw new StudioError('ARTIFACT_INTEGRITY_FAILED');
        parts.push(new Uint8Array(part.value));
      }
    } finally { await reader.cancel(); reader.releaseLock(); }
    if (total !== receipt.size) throw new StudioError('ARTIFACT_INTEGRITY_FAILED');
    const blob = new Blob(parts, { type: receipt.media_type });
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())))
      .map(n => n.toString(16).padStart(2, '0')).join('');
    if (digest !== receipt.sha256.toLowerCase()) throw new StudioError('ARTIFACT_INTEGRITY_FAILED');
    return blob;
  }
  admit(selectionToken: string) { return this.request('/sources/admit', sourceSchema, 'POST', {
    selection_token: selectionToken, provenance: { origin: 'explicit-local-selection', collected_by: 'local-studio-creator' },
    rights: { basis: 'owned', reference: 'Operator declares ownership of this selected local source.' },
    allowed_operations: ['render', 'analyze'],
  }, this.operator()); }
  create(document: ProjectDocument) { return this.request('/projects', revisionSchema, 'POST', { document }, this.operator()); }
  read(projectId: string) { return this.request(`/projects/${encodeURIComponent(projectId)}`, revisionSchema, 'GET', undefined, this.operator()); }
  save(projectId: string, base_revision: number, document: ProjectDocument) {
    return this.request(`/projects/${encodeURIComponent(projectId)}`, revisionSchema, 'PUT', { base_revision, document }, this.operator());
  }
  plan(project_id: string, revision: number, profile: string, narration_mode: NarrationMode = 'silent') { return this.request('/render-plans', planSchema, 'POST', {
    project_id, revision, locale: 'en', profile, narration_mode, max_duration_seconds: 600, max_output_bytes: 500_000_000,
  // Measured full local-resource verification took 19.061s. Bound this explicit
  // optional planning call at 60s; no automatic retry or approval is introduced.
  }, this.operator(), undefined, undefined, narration_mode === 'local_kokoro_cpu' ? 60000 : 20000); }
  async approve(planId: string, project_id: string, revision: number) {
    const agent_id = await this.ensureRunner(project_id, revision);
    return this.request(`/render-plans/${encodeURIComponent(planId)}/authorize`, z.object({ grant_id: z.string(),
      plan_id: z.string(), scope: z.literal('render'), status: z.literal('approved') }), 'POST',
      { agent_id, project_id, revision, expires_in_seconds: 900 }, this.operator());
  }
  run(planId: string, idempotencyKey: string) {
    if (!this.runner) throw new StudioError('HUMAN_APPROVAL_REQUIRED');
    return this.request('/renders/run-approved', jobSchema, 'POST', { plan_id: planId }, this.runner, idempotencyKey);
  }
  job(jobId: string) { return this.request(`/renders/${encodeURIComponent(jobId)}`, jobSchema, 'GET', undefined, this.operator()); }
  cancel(jobId: string) { return this.request(`/renders/${encodeURIComponent(jobId)}/cancel`, jobSchema, 'POST', {}, this.operator()); }
}
