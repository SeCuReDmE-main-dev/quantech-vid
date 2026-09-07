import { Blob as NodeBlob } from 'node:buffer';
import { webcrypto } from 'node:crypto';
import { afterEach, expect, it, vi } from 'vitest';
import { StudioAPI } from '../src/api/client';
import { source } from './fixtures';
const session={actor_type:'human',actor_id:'operator',session_token:'synthetic-session-token',csrf_token:'synthetic-csrf-token',expires_in_seconds:900};
afterEach(()=>vi.unstubAllGlobals());
async function fixture() {
  vi.stubGlobal('crypto',webcrypto); vi.stubGlobal('Blob',NodeBlob);
  const bytes=new TextEncoder().encode('synthetic bytes; server validates actual raster');
  const asset={...source,id:'src_'+'a'.repeat(32),size:bytes.length,
    sha256:Buffer.from(await webcrypto.subtle.digest('SHA-256',bytes)).toString('hex')};
  const response=()=>new Response(bytes,{headers:{'content-type':'image/png','content-length':String(bytes.length)}});
  const fetcher=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(session),{headers:{'content-type':'application/json'}}))
    .mockImplementation(async()=>response());
  const api=new StudioAPI(fetcher as typeof fetch); await api.pair('synthetic-code');
  return {api,fetcher,asset,response};
}
it('caches verified bytes only in memory, clears on context and logout, and authenticates each uncached request', async()=>{
  const {api,fetcher,asset}=await fixture();
  const first=await api.sourcePreview(asset);
  expect(await api.sourcePreview(asset)).toBe(first); expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[1][1]).toMatchObject({credentials:'omit',cache:'no-store',redirect:'error'});
  api.clearPreviews(); await api.sourcePreview(asset); expect(fetcher).toHaveBeenCalledTimes(3);
  api.forgetSessions(); await expect(api.sourcePreview(asset)).rejects.toMatchObject({code:'AUTHENTICATION_REQUIRED'});
});
it('rejects wrong hashes, invalid media, excessive sizes and stale pending responses', async()=>{
  const {api,fetcher,asset,response}=await fixture();
  await expect(api.sourcePreview({...asset,sha256:'0'.repeat(64)})).rejects.toMatchObject({code:'SOURCE_INTEGRITY_FAILED'});
  await expect(api.sourcePreview({...asset,size:11*1024*1024})).rejects.toMatchObject({code:'SOURCE_PREVIEW_UNAVAILABLE'});
  await expect(api.sourcePreview({...asset,media_type:'text/html'})).rejects.toMatchObject({code:'SOURCE_PREVIEW_UNAVAILABLE'});
  let finish!:(r:Response)=>void; fetcher.mockReturnValueOnce(new Promise(resolve=>{finish=resolve;}));
  const pending=api.sourcePreview(asset); api.clearPreviews(); finish(response());
  await expect(pending).rejects.toMatchObject({code:'TOOL_CONTEXT_CHANGED'});
});
it('evicts the least recently used entry after four cached images', async()=>{
  const {api,fetcher,asset}=await fixture();
  for(let i=0;i<5;i++) await api.sourcePreview({...asset,id:'src_'+String(i).repeat(32)});
  expect(fetcher).toHaveBeenCalledTimes(6);
  await api.sourcePreview({...asset,id:'src_'+'0'.repeat(32)});
  expect(fetcher).toHaveBeenCalledTimes(7);
});
