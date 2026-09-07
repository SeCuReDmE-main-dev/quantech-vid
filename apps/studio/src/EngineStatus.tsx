import { useRef, useEffect, useState } from 'react';
import type { StudioAPI } from './api/client';
import { engineProviders, type EngineProvider, type EngineInspection as Result } from './api/engine-contracts';
const names:Record<EngineProvider,string>={openai_codex:'Codex · ChatGPT',github_copilot:'Copilot · GitHub',google_antigravity:'Antigravity · Google'};
export function EngineStatus({api,disabled}:{api:StudioAPI;disabled:boolean}) {
  const [results,setResults]=useState<Partial<Record<EngineProvider,Result>>>({});
  const [pending,setPending]=useState<EngineProvider|null>(null),[error,setError]=useState('');
  const live=useRef(true),inFlight=useRef(false);
  useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
  async function inspect(provider:EngineProvider) {
    if(disabled||inFlight.current)return;
    inFlight.current=true;setPending(provider);setError('');
    try {const result=await api.inspectEngine(provider);
      if(live.current)setResults(previous=>({...previous,[provider]:result}));
    }catch{if(live.current)setError('Inspection unavailable. No login or production was started.');}
    finally{inFlight.current=false;if(live.current)setPending(null);}
  }
  return <><p>Provider sign-in is not studio permission. Local pairing, admitted sources and approval of an exact render plan remain separate.</p>{engineProviders.map(provider=>{const result=results[provider],connection=result?.connection;
    return <div key={provider}><strong>{names[provider]}</strong>
      <p>Connector not qualified in this build. No credentials requested; no paid fallback.</p>
      <button disabled={disabled||!!pending} onClick={()=>void inspect(provider)}>Inspect {names[provider]}</button>
      {pending===provider&&<p role="status">Checking the installed local client…</p>}
      {connection&&<dl><dt>Client</dt><dd>{connection.client.installation} · {connection.client.version.value??'version unknown'}</dd>
        <dt>Authentication</dt><dd>{connection.auth.state} · {connection.auth.method}</dd>
        <dt>Model access</dt><dd>{connection.rights.state}</dd>
        <dt>Quota</dt><dd>{connection.quota.state}{connection.quota.remaining_percent!=null?` · ${connection.quota.remaining_percent}% remaining`:''}</dd>
        <dt>Production</dt><dd>Unavailable — inspection is not a production capability test.</dd>
        <dt>Reasons</dt><dd>{connection.reason_codes.join(', ')}</dd><dt>Checked at</dt><dd>{result.checked_at}</dd></dl>}
    </div>;})}{error&&<p role="alert">{error}</p>}</>;
}
