import { cleanup, render, screen } from '@testing-library/react';
import { afterEach,expect,it } from 'vitest';
import { revisionChanges,RevisionDiff } from '../src/RevisionDiff';
import { revision } from './fixtures';
afterEach(cleanup);
it('compares changed and removed fields without mutating the baseline',()=>{
  const saved={a:'old',b:null,c:4},draft={a:'new',b:null};
  expect(revisionChanges(saved,draft).changes).toEqual([
    {path:'project.a',before:'"old"',after:'"new"'},
    {path:'project.c',before:'4',after:'(not present)'}]);
  expect(saved.c).toBe(4);
});
it('bounds change count and value length',()=>{
  const before=Object.fromEntries(Array.from({length:80},(_,i)=>[String(i),'old']));
  const after=Object.fromEntries(Array.from({length:80},(_,i)=>[String(i),'x'.repeat(2000)]));
  const result=revisionChanges(before,after);
  expect(result.changes).toHaveLength(40);expect(result.truncated).toBe(true);
  expect(result.changes.every(change=>change.after.length<=500)).toBe(true);
});
it('shows literal hostile content and no approval control',()=>{
  const saved=revision().document;
  render(<RevisionDiff saved={saved} draft={{...saved,title:'<img src=x onerror=alert(1)>'}}/>);
  expect(screen.getByText('"<img src=x onerror=alert(1)>"')).toBeTruthy();
  expect(document.querySelector('img')).toBeNull();expect(screen.queryByRole('button')).toBeNull();
});
