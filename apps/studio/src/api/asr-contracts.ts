import { z } from 'zod';
import { transcriptSegmentSchema } from '../contracts';

const hash = z.string().regex(/^[a-f0-9]{64}$/);
export const asrProposalSchema = z.object({
  schema_version: z.literal('quantech.asr-proposal.v1'),
  proposal_id: z.string().regex(/^asrp_[a-f0-9]{32}$/), effect: z.literal('proposal_only'),
  project_id: z.string().regex(/^prj_[a-f0-9]{32}$/), revision: z.number().int().positive(),
  project_hash: hash, locale: z.literal('en'), source_asset_id: z.string().regex(/^src_[a-f0-9]{32}$/),
  asset_sha256: hash, original_audio_sha256: hash,
  audio_duration_ms: z.number().int().positive().max(300000), resource_binding_sha256: hash,
  segments: z.array(transcriptSegmentSchema).max(128),
  limitations: z.object({ machine_proposal_only: z.literal(true), human_review_required: z.literal(true),
    speaker_identity_inferred: z.literal(false), vad_performed: z.literal(false),
    exact_zero_energy_rejected: z.literal(true), independent_verification: z.literal(false) }).strict(),
}).strict();
export type AsrProposal = z.infer<typeof asrProposalSchema>;
