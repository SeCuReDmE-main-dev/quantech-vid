import { z } from 'zod';

// Names are the only client allowlist. Input schemas come unchanged from the server.
export const TOOL_NAMES = ['quantech_inspect_project', 'quantech_stage_source_import',
  'quantech_analyze_visual_asset', 'quantech_stage_storyboard', 'quantech_stage_scene_changes',
  'quantech_stage_render', 'quantech_run_approved_render', 'quantech_inspect_production_result'] as const;
export type ToolName = typeof TOOL_NAMES[number];
export const toolDefinitionSchema = z.object({ name: z.enum(TOOL_NAMES), description: z.string().min(1).max(2000),
  input_schema: z.record(z.string(), z.unknown()), output_schema: z.record(z.string(), z.unknown()),
  annotations: z.object({ read_only: z.boolean(), idempotent: z.boolean(), open_world: z.literal(false) }),
});
export const catalogSchema = z.object({ tools: z.array(toolDefinitionSchema).length(8) })
  .refine(c => new Set(c.tools.map(t => t.name)).size === 8, 'Exactly eight distinct known tools are required.');
export type ToolDefinition = z.infer<typeof toolDefinitionSchema>;
export const toolResultSchema = z.object({ ok: z.boolean(), tool: z.enum(TOOL_NAMES),
  result: z.record(z.string(), z.unknown()).nullable(),
  error: z.object({ code: z.string().regex(/^[A-Z0-9_]{1,80}$/), message: z.string(), retryable: z.boolean() }).nullable(),
}).superRefine((r, ctx) => { if (r.ok ? r.result === null || r.error !== null : r.error === null || r.result !== null)
  ctx.addIssue({ code: 'custom', message: 'Result and error must agree with status.' }); });
export type ToolResult = z.infer<typeof toolResultSchema>;
