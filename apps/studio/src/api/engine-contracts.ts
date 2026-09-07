import { z } from 'zod';
export const engineProviders = ['openai_codex','github_copilot','google_antigravity'] as const;
export type EngineProvider = typeof engineProviders[number];
export const engineStatusSchema = z.object({checked_at:z.string(),connection:z.object({
  provider:z.enum(engineProviders), production:z.literal('unavailable'),
  client:z.object({name:z.string(),installation:z.enum(['installed','missing']),runtime:z.enum(['present','missing','unknown']),
    version:z.object({state:z.enum(['known','unknown']),value:z.string().nullable().optional()})}),
  auth:z.object({state:z.enum(['confirmed','unauthenticated','expired','unknown']),method:z.string(),reason_code:z.string()}),
  rights:z.object({state:z.enum(['confirmed','denied','unknown']),reason_code:z.string()}),
  quota:z.object({state:z.enum(['available','exhausted','unknown']),remaining_percent:z.number().min(0).max(100).nullable().optional(),reason_code:z.string()}),
  reason_codes:z.array(z.string()).max(16),
})});
export type EngineInspection=z.infer<typeof engineStatusSchema>;
