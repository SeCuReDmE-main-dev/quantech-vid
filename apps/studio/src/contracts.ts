import { z } from 'zod';

const id = z.string().min(1).max(200);
const hash = z.string().regex(/^[a-f0-9]{64}$/i);
const localized = (maximum: number) => z.object({ en: z.string().max(maximum), fr: z.string().max(maximum).optional() });

export const sourceOriginalSchema = z.object({
  name: z.string().min(1).max(180).refine(name => !/[\\/]/.test(name) && name !== '.' && name !== '..'), sha256: hash,
  size: z.number().int().positive().max(50_000_000),
  media_type: z.enum(['text/plain', 'text/markdown', 'image/png', 'image/jpeg', 'image/webp']),
  transformation: z.enum(['literal-text-preview-v1', 'rgb-png-v1']),
}).strict();
export const sourceSchema = z.object({
  id, sha256: hash, media_type: z.string(), size: z.number().int().nonnegative(),
  provenance: z.object({ origin: z.string(), collected_by: z.string(), note: z.string().nullable().optional(),
    original: sourceOriginalSchema.optional() }),
  rights: z.object({ basis: z.enum(['owned', 'licensed', 'public-domain', 'permission']), reference: z.string() }),
  allowed_operations: z.array(z.enum(['render', 'analyze'])), created_at: z.string(),
});
export type SourceAsset = z.infer<typeof sourceSchema>;

export const claimSchema = z.object({
  id: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/),
  text: z.string().min(1).max(1000),
  status: z.enum(['reported', 'observed', 'hypothesis', 'disputed', 'suspended']),
  rationale: z.string().min(1).max(500),
  evidence: z.array(z.object({ source_asset_id: z.string().regex(/^src_[a-f0-9]{32}$/),
    locator: z.string().min(1).max(160) }).strict()).max(8).default([]),
}).strict().superRefine((claim, ctx) => {
  if (claim.status === 'observed' && !claim.evidence.length)
    ctx.addIssue({ code: 'custom', message: 'A declared observation needs a source reference.' });
});
export type SceneClaim = z.infer<typeof claimSchema>;

export const visual3DSchema = z.object({
  kind: z.enum(['title', 'diagram', 'annotated-object', 'comparison', 'code', 'presentation', 'synthetic-avatar']),
  lines: z.array(z.string().min(1).max(200)).max(8), accent: z.string().regex(/^#[0-9A-Fa-f]{6}$/),
  animation: z.enum(['none', 'spin', 'pulse']),
}).strict();
export type Visual3D = z.infer<typeof visual3DSchema>;

// Decimal-string half-up, matching Python Decimal(str(seconds)). Binary float
// multiplication alone can round a decimal tie down on some accepted inputs.
export function captionMilliseconds(seconds: number): number {
  if (!Number.isFinite(seconds) || seconds < 0) return NaN;
  const [mantissa, exponent = '0'] = String(seconds).toLowerCase().split('e');
  const [whole, fraction = ''] = mantissa.split('.');
  const digits = BigInt(whole + fraction), shift = Number(exponent) - fraction.length + 3;
  if (shift >= 0) return Number(digits * 10n ** BigInt(shift));
  const divisor = 10n ** BigInt(-shift);
  return Number(digits / divisor + (digits % divisor * 2n >= divisor ? 1n : 0n));
}

export const transcriptSegmentSchema = z.object({
  id: z.string().regex(/^[A-Za-z0-9_-]{1,64}$/),
  start: z.number().finite().nonnegative(), end: z.number().finite().positive(),
  text: z.string().min(1).max(1000).refine(value => /[^\p{Z}\p{C}\s]/u.test(value), 'Caption text needs visible content.'),
  source_asset_id: z.string().regex(/^src_[a-f0-9]{32}$/),
  source_locator: z.string().min(1).max(160).refine(value => /[^\p{Z}\p{C}\s]/u.test(value), 'A source location needs visible content.'),
}).strict().superRefine((segment, ctx) => {
  if (segment.end <= segment.start || captionMilliseconds(segment.end) <= captionMilliseconds(segment.start))
    ctx.addIssue({ code: 'custom', message: 'A segment must retain positive duration at millisecond precision.' });
});
export type TranscriptSegment = z.infer<typeof transcriptSegmentSchema>;

export function validSegmentSequence(segments: TranscriptSegment[], sourceIds: string[], duration: number): boolean {
  return segments.length <= 128 && new Set(segments.map(s => s.id)).size === segments.length &&
    segments.every((segment, index) => sourceIds.includes(segment.source_asset_id) && segment.end <= duration &&
      (index === 0 || segment.start >= segments[index - 1].end));
}

export const sceneSchema = z.object({
  id: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/),
  duration: z.number().finite().positive().max(60), source_asset_id: id,
  title: localized(200), body: localized(1000).default({ en: '' }), fit: z.enum(['cover', 'contain']),
  // Omit absent/empty claims on old documents; do not silently rewrite historical hashes.
  claims: z.array(claimSchema).max(16).optional(),
  visual_3d: visual3DSchema.optional(),
});
export const documentSchema = z.object({
  schema_version: z.literal('2.0'), slug: z.string().regex(/^[a-z0-9][a-z0-9-]{0,79}$/),
  title: z.string().min(1).max(200), disclosure: z.string().max(500),
  sources: z.array(id).min(1).max(128), scenes: z.array(sceneSchema).min(1).max(128),
  tracks: z.array(z.object({ locale: z.enum(['fr', 'en']), title: z.string().max(200),
    narration: z.string().max(8000), voice: z.string().max(120).nullable().optional(),
    segments: z.array(transcriptSegmentSchema).max(128).optional() })).min(1).max(2),
  output_profiles: z.array(z.object({ name: id, width: z.number().int().min(320).max(4096),
    height: z.number().int().min(320).max(4096), fps: z.number().int().min(12).max(60) })).min(1).max(16),
}).superRefine((doc, ctx) => {
  if (new Set(doc.sources).size !== doc.sources.length ||
      new Set(doc.scenes.map(s => s.id)).size !== doc.scenes.length ||
      doc.scenes.some(s => !doc.sources.includes(s.source_asset_id))) {
    ctx.addIssue({ code: 'custom', message: 'Every scene needs a unique ID and a declared source.' });
  }
  if (new Set(doc.tracks.map(t => t.locale)).size !== doc.tracks.length ||
      new Set(doc.output_profiles.map(p => p.name)).size !== doc.output_profiles.length) {
    ctx.addIssue({ code: 'custom', message: 'Track locales and output profile names must be unique.' });
  }
  for (const scene of doc.scenes) {
    const claims = scene.claims ?? [];
    if (new Set(claims.map(c => c.id)).size !== claims.length ||
        claims.some(c => c.evidence.some(e => !doc.sources.includes(e.source_asset_id)))) {
      ctx.addIssue({ code: 'custom', message: 'Statements need unique IDs and admitted evidence sources.' });
    }
  }
  const duration = doc.scenes.reduce((sum, scene) => sum + scene.duration, 0);
  for (const track of doc.tracks) {
    if (!validSegmentSequence(track.segments ?? [], doc.sources, duration))
      ctx.addIssue({ code: 'custom', message: 'Manual captions require ordered, unique segments within the film and admitted sources.' });
  }
});
export type ProjectDocument = z.infer<typeof documentSchema>;
export const revisionSchema = z.object({ project_id: id, revision: z.number().int().positive(),
  document_hash: hash, document: documentSchema, created_at: z.string() });
export type ProjectRevision = z.infer<typeof revisionSchema>;
export const projectSummarySchema = z.object({ project_id: id, revision: z.number().int().positive(),
  document_hash: hash, slug: z.string(), title: z.string(), created_at: z.string() });
export type ProjectSummary = z.infer<typeof projectSummarySchema>;
export type NarrationMode = 'silent' | 'local_kokoro_cpu';
export const planSchema = z.object({
  id, project_id: id, revision: z.number().int().positive(), project_hash: hash,
  asset_hashes: z.record(z.string(), hash), locale: z.enum(['fr', 'en']), profile: z.string(),
  original_hashes: z.record(z.string().regex(/^src_[a-f0-9]{32}$/), hash).optional(),
  provider_resource_modes: z.record(z.string(), z.string()),
  narration_mode: z.enum(['silent', 'local_kokoro_cpu']).optional(),
  limits: z.record(z.string(), z.number().finite()), plan_hash: hash, created_at: z.string(),
});
export type RenderPlan = z.infer<typeof planSchema>;
export const jobSchema = z.object({
  id, project_id: id, revision: z.number().int().positive(), plan_id: id,
  status: z.enum(['queued', 'running', 'complete', 'failed', 'cancelled']),
  progress: z.number().min(0).max(100), error_code: z.string().nullable().optional(),
  receipts: z.array(z.object({ name: z.string(), role: z.string(), media_type: z.string(),
    size: z.number().int().nonnegative(), sha256: hash })),
  created_at: z.string(), updated_at: z.string(),
});
export type ProductionJob = z.infer<typeof jobSchema>;
export const sessionSchema = z.object({ actor_id: id, actor_type: z.literal('human'),
  session_token: z.string().min(16), csrf_token: z.string().min(16), expires_in_seconds: z.number().positive() });
export type HumanSession = z.infer<typeof sessionSchema>;
export const agentSchema = z.object({ agent_id: id, actor_type: z.literal('agent'), client_token: z.string().min(16) });
export type RunnerSession = z.infer<typeof agentSchema>;
export const healthSchema = z.object({ status: z.literal('ok'), version: z.string(), loopback: z.literal(true),
  capabilities: z.record(z.string(), z.boolean()) });
export type Health = z.infer<typeof healthSchema>;
