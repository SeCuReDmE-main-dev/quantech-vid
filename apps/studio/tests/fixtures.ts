import type { Health, ProductionJob, ProjectRevision, RenderPlan, SourceAsset } from '../src/contracts';
import { initialProject } from '../src/editor';
export const digest = 'a'.repeat(64);
export const source: SourceAsset = { id: 'src_synthetic', sha256: digest, media_type: 'image/png', size: 4096,
  provenance: { origin: 'synthetic-fixture', collected_by: 'test' },
  rights: { basis: 'owned', reference: 'Synthetic fixture' }, allowed_operations: ['render', 'analyze'], created_at: '2026-09-06T00:00:00Z' };
export const revision = (): ProjectRevision => ({ project_id: 'prj_fixture', revision: 1,
  document_hash: digest, document: initialProject(source), created_at: '2026-09-06T00:00:00Z' });
export const plan: RenderPlan = { id: 'plan_fixture', project_id: 'prj_fixture', revision: 1,
  project_hash: digest, asset_hashes: { src_synthetic: digest }, locale: 'en', profile: 'landscape',
  provider_resource_modes: { render: 'local-ffmpeg', narration: 'silent' },
  limits: { max_duration_seconds: 600, max_output_bytes: 500000000 }, plan_hash: digest, created_at: '2026-09-06T00:00:00Z' };
export const health: Health = { status: 'ok', version: 'test', loopback: true,
  capabilities: { approved_silent_render: true, network_import: false, paid_narration: false } };
export const job: ProductionJob = { id: 'job_fixture', project_id: 'prj_fixture', revision: 1,
  plan_id: plan.id, status: 'queued', progress: 0, receipts: [], created_at: 'now', updated_at: 'now' };
