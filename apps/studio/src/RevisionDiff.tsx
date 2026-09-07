import type { ProjectDocument } from './contracts';

type Change={path:string;before:string;after:string};
const describe=(value:unknown)=>value===undefined?'(not present)':JSON.stringify(value).slice(0,500);
export function revisionChanges(before:unknown,after:unknown) {
  const changes:Change[]=[];let truncated=false;
  function walk(left:unknown,right:unknown,path:string) {
    if(changes.length>=40){truncated=true;return;}
    if(JSON.stringify(left)===JSON.stringify(right))return;
    if(left!==null&&right!==null&&typeof left==='object'&&typeof right==='object') {
      const a=left as Record<string,unknown>,b=right as Record<string,unknown>;
      for(const key of new Set([...Object.keys(a),...Object.keys(b)])) {
        walk(a[key],b[key],path?`${path}.${key}`:key);
        if(truncated)break;
      }
    } else changes.push({path,before:describe(left),after:describe(right)});
  }
  walk(before,after,'project');return {changes,truncated};
}
export function RevisionDiff({saved,draft}:{saved:ProjectDocument|null;draft:ProjectDocument}) {
  if(!saved)return <p className="fine">First save creates a baseline. Later edits can be compared with that saved revision before approval.</p>;
  const {changes,truncated}=revisionChanges(saved,draft);
  return <details className="revision-diff"><summary>Review changes since the saved revision ({changes.length}{truncated?'+':''})</summary>
    <p>Predict the effect, inspect the change, then save and test the result. This comparison does not approve a render or grade your work. Undo remains available. Displayed values are limited to 500 characters.</p>
    {!changes.length?<p>No changes to the saved document.</p>:<ol>{changes.map(change=><li key={change.path}>
      <code>{change.path}</code><dl><dt>Saved</dt><dd><code>{change.before}</code></dd>
      <dt>Draft</dt><dd><code>{change.after}</code></dd></dl></li>)}</ol>}
    {truncated&&<p>Showing the first 40 changed fields only. Each displayed value is limited to 500 characters; inspect the full project before production.</p>}
  </details>;
}
