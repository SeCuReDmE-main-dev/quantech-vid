import { documentSchema, type ProjectDocument } from '../contracts';
import { StudioError } from '../api/client';
import type { ToolEvent } from './webmcp';

// This produces only an undoable local draft. The normal Save/CAS path remains necessary.
export function applyProposal(document: ProjectDocument, event: ToolEvent, currentHash: string): ProjectDocument {
  const result = event.response.result;
  if (!event.response.ok || result?.effect !== 'proposal_only' || result.project_hash !== currentHash)
    throw new StudioError('TOOL_CONTEXT_CHANGED');
  const args = event.argumentsValue;
  let proposed: unknown;
  if (event.tool === 'quantech_stage_source_import') {
    if (typeof args.source_asset_id !== 'string') throw new StudioError('INVALID_TOOL_INPUT');
    proposed = { ...document, sources: [...new Set([...document.sources, args.source_asset_id])] };
  } else if (event.tool === 'quantech_stage_storyboard') {
    if (!Array.isArray(args.scenes)) throw new StudioError('INVALID_TOOL_INPUT');
    proposed = { ...document, scenes: args.mode === 'append' ? [...document.scenes, ...args.scenes] : args.scenes };
  } else if (event.tool === 'quantech_stage_scene_changes') {
    if (!Array.isArray(args.patches)) throw new StudioError('INVALID_TOOL_INPUT');
    const patches = args.patches as Record<string, unknown>[];
    proposed = { ...document, scenes: document.scenes.map(scene => {
      let next: Record<string, unknown> = scene;
      for (const patch of patches.filter(p => p.scene_id === scene.id)) {
        const { scene_id: _id, ...changes } = patch;
        next = { ...next, ...Object.fromEntries(Object.entries(changes).filter(([, value]) => value !== null)) };
      }
      return next;
    }) };
  } else throw new StudioError('INVALID_TOOL_INPUT');
  const parsed = documentSchema.safeParse(proposed);
  if (!parsed.success) throw new StudioError('INVALID_TOOL_INPUT');
  return parsed.data;
}
