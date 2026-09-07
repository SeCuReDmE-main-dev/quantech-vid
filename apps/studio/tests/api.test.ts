import { describe, expect, it, vi } from 'vitest';
import { webcrypto } from 'node:crypto';
import { Blob as NodeBlob } from 'node:buffer';
import { StudioAPI } from '../src/api/client';
import { job, plan, source } from './fixtures';

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status,
  headers: { 'content-type': 'application/json' } });
const human = { actor_id: 'operator', actor_type: 'human', session_token: 'test-session-credential-only',
  csrf_token: 'test-csrf-credential-only', expires_in_seconds: 900 };
const runner = { agent_id: 'agent', actor_type: 'agent', client_token: 'test-agent-credential-only' };
const projectId = `prj_${'a'.repeat(32)}`;

describe('same-origin studio client', () => {
  it('holds credentials in memory, applies CSRF only to human mutations and reuses request IDs', async () => {
    const mock = vi.fn().mockResolvedValueOnce(json(human)).mockResolvedValueOnce(json(source))
      .mockResolvedValueOnce(json(runner)).mockResolvedValueOnce(json({ grant_id: 'grant', plan_id: plan.id, scope: 'render', status: 'approved' }))
      .mockResolvedValueOnce(json(job)).mockResolvedValueOnce(json(job));
    const api = new StudioAPI(mock as typeof fetch);
    expect(await api.pair('operator-code')).not.toHaveProperty('session_token');
    await api.sample(); await api.approve(plan.id, projectId, plan.revision);
    await api.run(plan.id, 'idempotent-render-key'); await api.run(plan.id, 'idempotent-render-key');
    const humanHeaders = mock.mock.calls[1][1].headers as Headers;
    expect(humanHeaders.get('X-CSRF-Token')).toBe(human.csrf_token);
    expect(JSON.parse(mock.mock.calls[2][1].body)).toMatchObject({ project_id: projectId, revision: plan.revision });
    expect(JSON.parse(mock.mock.calls[3][1].body)).toMatchObject({ agent_id: runner.agent_id,
      project_id: projectId, revision: plan.revision });
    for (const call of mock.mock.calls.slice(-2)) {
      const headers = call[1].headers as Headers;
      expect(headers.get('Authorization')).toBe(`Bearer ${runner.client_token}`);
      expect(headers.has('X-CSRF-Token')).toBe(false);
      expect(headers.get('Idempotency-Key')).toBe('idempotent-render-key');
      expect(call[0]).toBe('/api/v2/renders/run-approved');
      expect(call[1].credentials).toBe('omit');
      expect(call[1].redirect).toBe('error');
    }
    api.forgetSessions(); expect(() => api.sample()).toThrow('Pair this studio');
  });
  it('does not expose provider HTML or raw error details', async () => {
    const html = '<html>SECRET_PROVIDER_RESPONSE</html>';
    const api = new StudioAPI(vi.fn().mockResolvedValue(new Response(html, { status: 502, headers: { 'content-type': 'text/html' } })) as typeof fetch);
    await expect(api.health()).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
    await expect(api.health()).rejects.not.toThrow('SECRET_PROVIDER_RESPONSE');
    const api2 = new StudioAPI(vi.fn().mockResolvedValue(json({ error: { code: 'ORIGIN_REJECTED', message: 'PRIVATE_PATH' } }, 403)) as typeof fetch);
    await expect(api2.health()).rejects.toMatchObject({ code: 'ORIGIN_REJECTED' });
    await expect(api2.health()).rejects.not.toThrow('PRIVATE_PATH');
  });
  it('rejects malformed successes and never retries a mutation by itself', async () => {
    const mock = vi.fn().mockResolvedValue(json({ status: 'ok' }));
    await expect(new StudioAPI(mock as typeof fetch).health()).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
    expect(mock).toHaveBeenCalledTimes(1);
  });
  it('does not start a render without an independently registered runner', () => {
    const mock = vi.fn();
    expect(() => new StudioAPI(mock as typeof fetch).run('plan', 'key')).toThrow();
    expect(mock).not.toHaveBeenCalled();
  });
  it.each(['école 100%.txt', 'école 100%.md', 'école 100%.markdown'])('uploads the explicitly selected %s without rewriting its bytes or leaking credentials in the URL', async filename => {
    const mediaType = filename.endsWith('.txt') ? 'text/plain' : 'text/markdown';
    const mock = vi.fn().mockResolvedValueOnce(json(human)).mockResolvedValueOnce(json({ asset: source,
      original: { name: filename, size: 6, sha256: 'a'.repeat(64), media_type: mediaType }, derived: true }));
    const api = new StudioAPI(mock as typeof fetch); await api.pair('synthetic-code');
    const file = new File(['école'], filename, { type: mediaType });
    const result = await api.upload(file, 'owned', 'Texte fictif — propriété de QA');
    expect(result.original.name).toBe(filename);
    expect(result.original.media_type).toBe(mediaType);
    expect(mock.mock.calls[1][0]).toBe('/api/v2/sources/upload');
    const options = mock.mock.calls[1][1]; expect(options.body).toBe(file);
    expect(options.headers['X-File-Name']).toBe(encodeURIComponent(file.name));
    expect(options.headers['X-Rights-Reference']).toBe(encodeURIComponent('Texte fictif — propriété de QA'));
    expect(options.headers['X-CSRF-Token']).toBe(human.csrf_token);
    expect(options.headers['Content-Type']).toBe(mediaType);
    expect(options.credentials).toBe('omit');
    expect(options.redirect).toBe('error');
  });
  it('does not send an HTML file disguised with a Markdown filename suffix or an empty Markdown', async () => {
    const mock = vi.fn().mockResolvedValueOnce(json(human));
    const api = new StudioAPI(mock as typeof fetch); await api.pair('synthetic-code');
    for (const file of [new File(['<script>inert</script>'], 'source.md.html'), new File([], 'empty.md')])
      await expect(api.upload(file, 'owned', 'Synthetic QA')).rejects.toMatchObject({ code: 'UNSUPPORTED_SOURCE_MEDIA' });
    expect(mock).toHaveBeenCalledTimes(1);
  });
  it('deduplicates concurrent runner registration so approval and tools bind the same agent', async () => {
    const mock = vi.fn().mockResolvedValueOnce(json(human)).mockResolvedValueOnce(json(runner));
    const api = new StudioAPI(mock as typeof fetch); await api.pair('synthetic-code');
    expect(await Promise.all([api.ensureRunner(projectId, plan.revision), api.ensureRunner(projectId, plan.revision)])).toEqual(['agent', 'agent']);
    expect(mock).toHaveBeenCalledTimes(2);
  });
  it('revokes an old runner before registering a different project revision scope', async () => {
    const second = { ...runner, agent_id: 'agent-2', client_token: 'test-agent-credential-two' };
    const mock = vi.fn().mockResolvedValueOnce(json(human)).mockResolvedValueOnce(json(runner))
      .mockResolvedValueOnce(new Response(null, { status: 204 })).mockResolvedValueOnce(json(second));
    const api = new StudioAPI(mock as typeof fetch); await api.pair('synthetic-code');
    await api.ensureRunner(projectId, 1);
    await api.ensureRunner(projectId, 2);
    expect(mock.mock.calls[2][0]).toBe('/api/v2/agents/agent');
    expect(mock.mock.calls[2][1].method).toBe('DELETE');
    expect(mock.mock.calls[3][0]).toBe('/api/v2/agents');
    expect(JSON.parse(mock.mock.calls[3][1].body)).toMatchObject({ project_id: projectId, revision: 2 });
  });
  it('lists metadata without expecting an embedded document or stale project contents', async () => {
    const summary = { project_id: 'prj_fixture', revision: 2, document_hash: 'a'.repeat(64),
      slug: 'fixture', title: 'Current title', created_at: '2026-09-06T00:00:00Z' };
    const mock = vi.fn().mockResolvedValueOnce(json(human)).mockResolvedValueOnce(json({ projects: [summary] }));
    const api = new StudioAPI(mock as typeof fetch); await api.pair('synthetic-code');
    expect(await api.projects()).toEqual({ projects: [summary] });
  });
  it('opens artifact bytes only after exact length and SHA-256 verification', async () => {
    vi.stubGlobal('crypto', webcrypto); vi.stubGlobal('Blob', NodeBlob);
    try {
      const bytes = new TextEncoder().encode('synthetic verified artifact');
      const sha256 = Buffer.from(await webcrypto.subtle.digest('SHA-256', bytes)).toString('hex');
      const mock = vi.fn().mockResolvedValueOnce(json(human)).mockImplementation(async () => new Response(bytes,
        { headers: { 'content-length': String(bytes.length), 'content-type': 'text/plain' } }));
      const api = new StudioAPI(mock as typeof fetch); await api.pair('synthetic-code');
      const receipt = { name: 'fixture.txt', size: bytes.length, sha256, media_type: 'text/plain' };
      expect(await (await api.artifact('job_fixture', receipt)).text()).toBe('synthetic verified artifact');
      await expect(api.artifact('job_fixture', { ...receipt, sha256: '0'.repeat(64) })).rejects.toMatchObject({ code: 'ARTIFACT_INTEGRITY_FAILED' });
      await expect(api.artifact('job_fixture', { ...receipt, size: bytes.length + 1 })).rejects.toMatchObject({ code: 'ARTIFACT_INTEGRITY_FAILED' });
      expect(mock.mock.calls[1][0]).toBe('/api/v2/renders/job_fixture/artifacts/fixture.txt');
      expect(mock.mock.calls[1][1].headers.Authorization).toBe(`Bearer ${human.session_token}`);
    } finally { vi.unstubAllGlobals(); }
  });
});
