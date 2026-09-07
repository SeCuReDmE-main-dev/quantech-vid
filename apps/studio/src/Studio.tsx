import { lazy, Suspense, useEffect, useRef, useState, type FormEvent } from 'react';
import { StudioAPI, StudioError } from './api/client';
import { jobSchema, planSchema, type Health, type ProductionJob, type ProjectDocument, type ProjectRevision, type ProjectSummary, type RenderPlan, type SourceAsset } from './contracts';
import { edit, initialProject, moveScene, persistDraft, redo, restoreDraft, timeline, undo, verifiedCompletion, type History } from './editor';
import { inspectThroughNativeContext, nativeModelContext, WebMCPBridge, type ToolEvent } from './tools/webmcp';
import { applyProposal } from './tools/proposals';
import type { ToolDefinition, ToolName } from './tools/contracts';
import { SceneClaims } from './SceneClaims';
import { Scene3DLabels } from './Scene3DLabels';
import { TranscriptEditor } from './TranscriptEditor';
import { VerifiedVideoPreview } from './VerifiedVideoPreview';
import { SourceMetadata } from './SourceMetadata';
import { SourcePreview } from './SourcePreview';
import { EngineStatus } from './EngineStatus';

const Scene3DPreview = lazy(() => import('./Scene3DPreview'));

export function Studio({ api: providedAPI }: { api?: StudioAPI }) {
  const [api] = useState(() => providedAPI ?? new StudioAPI());
  const [health, setHealth] = useState<Health | null>(null);
  const [paired, setPaired] = useState(false);
  const [code, setCode] = useState('');
  const [advanced, setAdvanced] = useState(false);
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');
  const [history, setHistory] = useState<History | null>(null);
  const [revision, setRevision] = useState<ProjectRevision | null>(null);
  const [restore, setRestore] = useState(() => restoreDraft(localStorage));
  const [sources, setSources] = useState<SourceAsset[]>([]);
  const [sceneIndex, setSceneIndex] = useState(0);
  const [profile, setProfile] = useState('landscape');
  const [plan, setPlan] = useState<RenderPlan | null>(null);
  const [approved, setApproved] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [job, setJob] = useState<ProductionJob | null>(null);
  const [message, setMessage] = useState('Your sources stay local. Start with a synthetic example; no provider account is needed.');
  const [error, setError] = useState<StudioError | null>(null);
  const [busy, setBusy] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const [openId, setOpenId] = useState('');
  const [selection, setSelection] = useState('');
  const [chosenFile, setChosenFile] = useState<File | null>(null);
  const [rightsBasis, setRightsBasis] = useState<'owned' | 'licensed' | 'public-domain' | 'permission'>('owned');
  const [rightsReference, setRightsReference] = useState('');
  const [savedProjects, setSavedProjects] = useState<ProjectSummary[]>([]);
  const [media, setMedia] = useState<{ url: string; type: string; name: string } | null>(null);
  const [nativeTools] = useState(nativeModelContext);
  const [toolStatus, setToolStatus] = useState('Not connected');
  const [toolDefinitions, setToolDefinitions] = useState<ToolDefinition[]>([]);
  const [toolEvent, setToolEvent] = useState<ToolEvent | null>(null);
  const [toolExecuting, setToolExecuting] = useState(false);
  const [claimEditing, setClaimEditing] = useState(false);
  const [visualEditing, setVisualEditing] = useState(false);
  const [transcriptEditing, setTranscriptEditing] = useState(false);
  const [uncertainTool, setUncertainTool] = useState<{ tool: ToolName; argumentsValue: Record<string, unknown> } | null>(null);
  const bridge = useRef<WebMCPBridge | null>(null);
  const busyRef = useRef(false);
  const requestKeys = useRef(new Map<string, string>());
  const document = history?.present;
  const selectedScene = document?.scenes[Math.min(sceneIndex, document.scenes.length - 1)];
  const selectedSource = sources.find(source => source.id === selectedScene?.source_asset_id);
  const previewContext = JSON.stringify([document, revision?.project_id, revision?.revision, profile, sources, paired]);
  useEffect(() => { api.clearPreviews(); }, [api, previewContext]);
  const dirty = !!document && (!revision || JSON.stringify(document) !== JSON.stringify(revision.document));
  const activeJob = job?.status === 'queued' || job?.status === 'running';
  const captionReceipt = job?.receipts.find(receipt => receipt.role === 'captions-vtt' && receipt.media_type === 'text/vtt' && receipt.size <= 1_000_000);
  const workLocked = !!busy || !!activeJob || uncertain || toolExecuting;
  const frozen = workLocked || claimEditing || visualEditing || transcriptEditing;
  const toolSnapshot = useRef({ projectId: revision?.project_id ?? null, revision: revision?.revision ?? null, dirty, busy: false });
  toolSnapshot.current = { projectId: revision?.project_id ?? null, revision: revision?.revision ?? null, dirty: dirty || claimEditing || visualEditing || transcriptEditing,
    busy: busyRef.current || uncertain };

  useEffect(() => { let live = true; api.health().then(value => { if (live) setHealth(value); })
    .catch(() => { if (live) setMessage('The local server is not reachable. Your existing draft is still available.'); });
    return () => { live = false; }; }, [api]);
  useEffect(() => { return () => { if (media) URL.revokeObjectURL(media.url); }; }, [media]);
  useEffect(() => {
    const onPageHide = () => { void bridge.current?.close(); bridge.current = null;
      api.forgetSessions(); setPaired(false); setApproved(false); setToolDefinitions([]); setToolStatus('Disconnected after page navigation'); };
    window.addEventListener('pagehide', onPageHide);
    return () => { window.removeEventListener('pagehide', onPageHide); void bridge.current?.close(); };
  }, [api]);

  useEffect(() => {
    if (!document) return;
    const timer = window.setTimeout(() => {
      const saved = persistDraft(localStorage, { document, projectId: revision?.project_id ?? null,
        baseRevision: revision?.revision ?? null, savedAt: new Date().toISOString() });
      if (!saved) setMessage('Browser draft storage is unavailable. Save to the paired server before closing.');
    }, 500);
    return () => window.clearTimeout(timer);
  }, [document, revision]);

  useEffect(() => {
    if (!job || !activeJob || !paired) return;
    let live = true;
    const timer = window.setTimeout(() => { api.job(job.id).then(value => {
      if (!live) return;
      setJob(value);
      if (value.status === 'complete') setMessage('The server finished this render. Inspect its files and receipts below.');
      if (value.status === 'failed') setError(new StudioError(value.error_code ?? 'RENDER_FAILED'));
    }).catch(() => { if (live) setMessage('Automatic status refresh paused. Use Refresh status; do not start another render.'); }); }, 1500);
    return () => { live = false; window.clearTimeout(timer); };
  }, [api, job, activeJob, paired]);

  async function perform(label: string, action: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(label); setError(null);
    try { await action(); } catch (cause) { setError(cause instanceof StudioError ? cause : new StudioError('INVALID_INPUT')); }
    finally { busyRef.current = false; setBusy(''); }
  }
  function invalidatePlan() { setPlan(null); setApproved(false); setReviewed(false); setToolEvent(null); }
  function change(next: ProjectDocument) {
    if (!history || frozen) return;
    try { setHistory(edit(history, next)); invalidatePlan(); }
    catch { setError(new StudioError('INVALID_INPUT')); }
  }
  function loadRevision(value: ProjectRevision) {
    if (revision?.project_id !== value.project_id || revision?.revision !== value.revision) void disableTools();
    setRevision(value); setHistory({ past: [], present: value.document, future: [] });
    setSceneIndex(0); setProfile(value.document.output_profiles[0].name); invalidatePlan(); setJob(null); setMedia(null);
  }
  function adoptSource(source: SourceAsset) {
    setSources(current => [...current.filter(s => s.id !== source.id), source]);
    if (!history) setHistory({ past: [], present: initialProject(source), future: [] });
    else { setHistory(edit(history, { ...history.present, sources: [...new Set([...history.present.sources, source.id])] })); invalidatePlan(); }
    setMessage('Source admitted. Ownership and provenance are declarations, not an independent license verification.');
  }
  function restoreLocal() {
    if (!restore) return;
    void disableTools();
    setHistory({ past: [], present: restore.document, future: [] });
    setOpenId(restore.projectId ?? ''); setRevision(null); setProfile(restore.document.output_profiles[0].name);
    setRestore(null); invalidatePlan();
    setMessage('Local draft restored without restoring any approval or credential. Save as a new project, or reopen the server version separately.');
  }
  function addScene() {
    if (!document || !selectedScene) return;
    change({ ...document, scenes: [...document.scenes, { ...selectedScene,
      id: `scene-${crypto.randomUUID().slice(0, 8)}`, title: { en: 'A new scene to review' }, body: { en: '' } }] });
    setSceneIndex(document.scenes.length);
  }
  async function save() {
    if (!document) return;
    const value = revision ? await api.save(revision.project_id, revision.revision, document) : await api.create(document);
    await disableTools();
    setRevision(value); setHistory(current => current ? { ...current, present: value.document } : null);
    invalidatePlan(); setMessage(`Saved revision ${value.revision}. Saving does not approve or start a render.`);
  }
  async function run() {
    if (!plan) return;
    let key = requestKeys.current.get(plan.id);
    if (!key) { key = crypto.randomUUID(); requestKeys.current.set(plan.id, key); }
    try { setJob(await api.run(plan.id, key)); setUncertain(false); setApproved(false); setMessage('Render queued. The same request key is retained for safe retries.'); }
    catch (cause) {
      const rejectedBeforeRun = cause instanceof StudioError && cause.status >= 400 && cause.status < 500 &&
        ['APPROVAL_REQUIRED', 'APPROVAL_INTEGRITY_FAILED', 'STALE_PROJECT_REVISION', 'AGENT_SCOPE_REJECTED', 'AUTHENTICATION_REQUIRED'].includes(cause.code);
      setUncertain(!rejectedBeforeRun);
      if (rejectedBeforeRun) { setApproved(false); setReviewed(false); }
      throw cause;
    }
  }
  function receiveTool(event: ToolEvent) {
    setToolEvent(event);
    if (!event.response.ok) { setError(new StudioError(event.response.error?.code ?? 'TOOL_REQUEST_FAILED')); return; }
    const result = event.response.result;
    if (event.tool === 'quantech_stage_render') {
      const value = planSchema.parse(result?.plan);
      setPlan(value); setProfile(value.profile); setApproved(false); setReviewed(false); setJob(null); setMedia(null);
      setMessage('An agent prepared this plan. Review it below; no approval or render has started.');
    } else if (event.tool === 'quantech_run_approved_render' || event.tool === 'quantech_inspect_production_result') {
      const value = jobSchema.parse(result?.job); setJob(value); setApproved(false); setUncertain(false); setUncertainTool(null);
      setMessage('Agent job result received. Production and byte verification remain separate steps.');
    } else setMessage(result?.effect === 'proposal_only' ? 'An agent proposal is ready for review. Your saved project is unchanged.' : 'Agent inspection completed; no source content is treated as an instruction.');
  }
  async function enableTools() {
    if (!nativeTools || bridge.current || !revision || dirty) return;
    await api.ensureRunner(revision.project_id, revision.revision);
    const catalogue = await api.catalog();
    const instance = new WebMCPBridge(nativeTools, () => ({ ...toolSnapshot.current, busy: busyRef.current || toolSnapshot.current.busy }),
      (name, args, signal) => api.invokeTool(name, args, signal), receiveTool,
      event => { setUncertainTool(event); setUncertain(true); }, setToolExecuting);
    bridge.current = instance;
    try { await instance.register(catalogue.tools); setToolDefinitions(catalogue.tools); setToolStatus('Eight native WebMCP tools registered'); }
    catch (cause) { bridge.current = null; setToolStatus('Native registration failed; manual studio remains available'); throw cause; }
  }
  async function disableTools() {
    await bridge.current?.close(); bridge.current = null; setToolDefinitions([]); setToolStatus('Disconnected');
  }
  const steps = ['Pair locally', 'Admit a source', 'Shape the scenes', 'Review a plan', 'Approve', 'Render & inspect'];
  const stage = !paired ? 0 : !document ? 1 : !plan ? 2 : !approved && !job ? 3 : !job ? 4 : 5;

  return <div className={`studio theme-${theme}`}>
    <a className="skip-link" href="#workspace">Skip to workspace</a>
    <header className="topbar">
      <div className="brand"><span className="brand-mark" aria-hidden="true">QV</span><div><strong>QuaNTecH-ViD</strong><small>SecuredMe · Local production studio</small></div></div>
      <div className="toolbar"><span className="connection">{health ? `Local server ${health.version}` : 'Server not verified'}</span>
        <button onClick={() => setAdvanced(!advanced)} aria-pressed={advanced}>{advanced ? 'Advanced view' : 'Guided view'}</button>
        <button onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? 'Light theme' : 'Dark theme'}</button>
      </div>
    </header>
    <div className="workspace-heading"><div><p className="eyebrow">A SOURCE. A STORY. A RESULT YOU CAN INSPECT.</p>
      <h1>Your ideas, under your direction.</h1><p>Build locally. Review what changes. Keep the proof beside the film.</p></div>
      <span className="pill">No paid engine enabled</span></div>
    {!advanced && <ol className="steps" aria-label="Production journey">{steps.map((step, i) => <li key={step} aria-current={stage === i ? 'step' : undefined}><span>{i + 1}</span>{step}</li>)}</ol>}
    <section className="notification" aria-live="polite" aria-atomic="true">{busy ? `${busy}…` : message}</section>
    {error && <div role="alert" className="error"><strong>{error.code.replaceAll('_', ' ')}</strong><p>{error.message}</p><button onClick={() => setError(null)}>Dismiss message</button></div>}
    {restore && !document && <div className="restore"><p>A local draft from {new Date(restore.savedAt).toLocaleString()} is available. No saved credential or approval will be reused.</p><button onClick={restoreLocal}>Restore local draft</button></div>}
    {!paired && <section className="pairing panel" aria-labelledby="pair-title"><div><h2 id="pair-title">Connect to your local workshop</h2>
      <p>Enter the one-time operator code from the local server. This pairs this browser with the studio; it does not sign in to ChatGPT, GitHub, or Google.</p></div>
      <form onSubmit={(event: FormEvent) => { event.preventDefault(); const value = code; setCode(''); void perform('Pairing', async () => { await api.pair(value); setPaired(true); setMessage('Paired locally. Start with a synthetic sample, then edit your first scene.'); }); }}>
        <label>One-time operator code<input type="password" autoComplete="off" value={code} onChange={e => setCode(e.target.value)} required minLength={16}/></label>
        <button className="primary" disabled={!!busy || !code}>Pair this studio</button>
      </form>
      <p className="fine">After reopening this browser, run <code>quantech-vid pairing-code</code> in your local terminal to issue a fresh ten-minute code. Reconnect, then list your saved projects. Old approvals are never restored from the browser.</p>
    </section>}
    <main id="workspace" className="workspace" aria-busy={!!busy}>
      <aside className="panel sources" aria-labelledby="sources-title"><p className="eyebrow">01 · SOURCES</p><h2 id="sources-title">The material</h2>
        <p>Only content you explicitly admit belongs to this project.</p>
        <button className="primary" disabled={!paired || frozen} onClick={() => void perform('Creating a local sample', async () => adoptSource(await api.sample()))}>Create a synthetic sample</button>
        <label className="unavailable">Select a local file <input type="file" accept=".png,.jpg,.jpeg,.webp,.txt,.md,.markdown" disabled={!paired || frozen} aria-describedby="import-status" onChange={e => setChosenFile(e.target.files?.[0] ?? null)} /></label>
        <p id="import-status" className="fine">PNG, JPEG, WebP or UTF-8 text/Markdown, up to 50 MB (text: 200,000 characters). Selection alone sends nothing. The original is preserved; its visual excerpt is bounded. Markdown stays literal: no HTML execution or linked images are fetched. Audio, video and PDF imports are not qualified yet.</p>
        {chosenFile && <form onSubmit={e => { e.preventDefault(); void perform('Importing the selected file', async () => { const result = await api.upload(chosenFile, rightsBasis, rightsReference); adoptSource(result.asset); setChosenFile(null); setRightsReference(''); }); }}>
          <p className="fine">{chosenFile.name} · {(chosenFile.size / 1024).toFixed(1)} KB</p>
          <label>Basis for using this source<select value={rightsBasis} onChange={e => setRightsBasis(e.target.value as typeof rightsBasis)}><option value="owned">I own the source</option><option value="licensed">A license permits this use</option><option value="public-domain">Public domain</option><option value="permission">I have permission</option></select></label>
          <label>Rights reference or explanation<input required maxLength={500} value={rightsReference} onChange={e => setRightsReference(e.target.value)} /></label>
          <button disabled={frozen || !rightsReference.trim()}>Admit this selected file</button>
        </form>}
        <button disabled={!paired || frozen} onClick={() => void perform('Reading admitted source metadata', async () => {
          setSources((await api.sources()).sources);
          setMessage('Source metadata refreshed. No source was imported and no project, plan or approval was changed.');
        })}>Reload admitted source metadata</button>
        <ul className="asset-list">{sources.map(source => <li key={source.id}><SourceMetadata source={source}/></li>)}</ul>
        {!sources.length && <div className="empty">No newly admitted source in this session. A restored project can still reference sources already held by the server.</div>}
        {advanced && <form onSubmit={e => { e.preventDefault(); void perform('Admitting the selected source', async () => { adoptSource(await api.admit(selection)); setSelection(''); }); }}>
          <label>Operator-provided selection handle<input value={selection} onChange={e => setSelection(e.target.value)} autoComplete="off" minLength={16} /></label>
          <button disabled={!paired || frozen || selection.length < 16}>Admit owned selection</button><small>Only use a handle created by the local file-selection broker. Never paste a filesystem path or credentials.</small>
        </form>}
      </aside>
      <section className="panel preview-panel" aria-labelledby="preview-title"><div className="section-head"><div><p className="eyebrow">02 · STORYBOARD</p><h2 id="preview-title">{document?.title ?? 'A first scene starts here'}</h2></div><span className="pill">{dirty ? 'Unsaved changes' : revision ? `Revision ${revision.revision}` : 'Local draft'}</span></div>
        <div className={`preview ${profile === 'portrait' ? 'portrait' : ''}`} aria-label="Text storyboard preview, not a rendered video">
          <span className="preview-mark" aria-hidden="true">QV</span><p className="eyebrow">STORYBOARD · LOCAL DRAFT</p>
          <h3>{selectedScene?.title.en ?? 'Make something you can show.'}</h3><p>{selectedScene?.body.en ?? 'Add a sample source and shape its story. You decide when anything runs.'}</p>
          {selectedScene?.claims?.some(c => ['hypothesis', 'disputed', 'suspended'].includes(c.status)) && <p className="warning">This scene contains unresolved statements. Their status and sources must remain visible in the export.</p>}
          <small>{selectedScene ? `Scene ${sceneIndex + 1} · ${selectedScene.duration}s` : 'No generation or rendering has started'}</small>
        </div><p className="fine">This text storyboard previews structure, not the final media. The generated video and QA report are checked separately.</p>
        {paired && selectedSource && <SourcePreview key={`${previewContext}:${selectedSource.id}`} source={selectedSource} api={api} disabled={frozen} />}
        {selectedScene?.visual_3d && <Suspense fallback={<p>Loading the local 3D module…</p>}><Scene3DPreview
          visual={selectedScene.visual_3d} title={selectedScene.title.en} duration={selectedScene.duration} portrait={profile === 'portrait'} /></Suspense>}
        <div className="toolbar"><button disabled={!history?.past.length || frozen} onClick={() => { if (history) { setHistory(undo(history)); invalidatePlan(); } }}>Undo</button>
          <button disabled={!history?.future.length || frozen} onClick={() => { if (history) { setHistory(redo(history)); invalidatePlan(); } }}>Redo</button>
          <button disabled={!document || frozen} onClick={addScene}>Add scene</button>
          <button className="primary" disabled={!paired || !document || frozen || !dirty} onClick={() => void perform('Saving project', save)}>Save project</button></div>
        <p className="fine">Draft content is saved in this browser. Pairing codes, sessions and production approvals are never stored with it.</p>
        {revision && <p className="fine">Project ID: <code>{revision.project_id}</code></p>}
        <form className="open-project" onSubmit={e => { e.preventDefault(); void perform('Opening saved project', async () => loadRevision(await api.read(openId.trim()))); }}>
          <label>Open a saved project ID<input value={openId} onChange={e => setOpenId(e.target.value)} placeholder="Project identifier, not a path" /></label>
        <button disabled={!paired || frozen || !openId || dirty}>Open server revision</button>
        </form>
        <button className="project-refresh" disabled={!paired || frozen} onClick={() => void perform('Listing your saved projects', async () => { setSavedProjects((await api.projects()).projects); setSources((await api.sources()).sources); })}>List saved projects</button>
        {savedProjects.length > 0 && <ul className="asset-list">{savedProjects.map(item => <li key={item.project_id}><button disabled={frozen || dirty} onClick={() => void perform('Opening the latest saved revision', async () => loadRevision(await api.read(item.project_id)))}>{item.title} · revision {item.revision}</button></li>)}</ul>}
      </section>
      <aside className="panel properties" aria-labelledby="properties-title"><p className="eyebrow">03 · DETAILS</p><h2 id="properties-title">Shape the scene</h2>
        {document && selectedScene ? <fieldset disabled={frozen}><legend>Scene properties</legend>
          <label>Project title<input value={document.title} maxLength={200} onChange={e => { if (e.target.value) change({ ...document, title: e.target.value }); }} /></label>
          <label>Scene heading<input value={selectedScene.title.en} maxLength={200} onChange={e => change({ ...document, scenes: document.scenes.map(s => s.id === selectedScene.id ? { ...s, title: { ...s.title, en: e.target.value } } : s) })} /></label>
          <label>On-screen text<textarea value={selectedScene.body.en} rows={4} maxLength={1000} onChange={e => change({ ...document, scenes: document.scenes.map(s => s.id === selectedScene.id ? { ...s, body: { ...s.body, en: e.target.value } } : s) })} /></label>
          <label>Duration in seconds<input type="number" min="0.1" max="60" step="0.1" value={selectedScene.duration} onChange={e => { const value = Number(e.target.value); if (value > 0 && value <= 60) change({ ...document, scenes: document.scenes.map(s => s.id === selectedScene.id ? { ...s, duration: value } : s) }); }} /></label>
          <label>Image framing<select value={selectedScene.fit} onChange={e => change({ ...document, scenes: document.scenes.map(s => s.id === selectedScene.id ? { ...s, fit: e.target.value as 'cover' | 'contain' } : s) })}><option value="contain">Contain — show the whole source</option><option value="cover">Cover — crop to the frame</option></select></label>
          <label>Scene source<select value={selectedScene.source_asset_id} onChange={e => change({ ...document, scenes: document.scenes.map(s => s.id === selectedScene.id ? { ...s, source_asset_id: e.target.value } : s) })}>{document.sources.map(id => {
            const found = sources.find(s => s.id === id);
            return <option key={id} value={id}>{found?.provenance.original?.name ?? found?.media_type ?? 'Admitted source'} · {id.slice(0, 16)}</option>;
          })}</select></label>
          <label>Output format<select value={profile} onChange={e => { setProfile(e.target.value); invalidatePlan(); }}>{document.output_profiles.map(p => <option key={p.name} value={p.name}>{p.name} · {p.width} × {p.height}</option>)}</select></label>
          <label>Production disclosure<textarea rows={3} value={document.disclosure} maxLength={500} onChange={e => change({ ...document, disclosure: e.target.value })} /></label>
          <label>Scene representation<select value={selectedScene.visual_3d?.kind ?? 'source-image'} onChange={e => {
            const kind = e.target.value;
            change({ ...document, scenes: document.scenes.map(s => {
              if (s.id !== selectedScene.id) return s;
              const { visual_3d: previous, ...rest } = s;
              return kind === 'source-image' ? rest : { ...rest, visual_3d: { kind: kind as NonNullable<typeof s.visual_3d>['kind'],
                accent: previous?.accent ?? '#14B8A6', animation: previous?.animation ?? 'none', lines: previous?.lines ?? [] } };
            }) });
          }}><option value="source-image">Admitted source image</option>{['title','diagram','annotated-object','comparison','code','presentation'].map(kind => <option key={kind} value={kind}>3D · {kind}</option>)}</select></label>
          {selectedScene.visual_3d && <><label>3D animation<select value={selectedScene.visual_3d.animation} onChange={e => change({ ...document,
            scenes: document.scenes.map(s => s.id === selectedScene.id && s.visual_3d ? { ...s, visual_3d: { ...s.visual_3d, animation: e.target.value as 'none' | 'spin' | 'pulse' } } : s) })}>
            <option value="none">Still</option><option value="spin">Bounded rotation</option><option value="pulse">Gentle pulse</option></select></label>
            <p className="fine">Procedural, synthetic geometry. Source admission and uncertainty labels still apply. Importing external models is not enabled.</p></>}
        </fieldset> : <div className="empty">The selected scene’s text, duration and framing will appear here.</div>}
        {document && selectedScene?.visual_3d && <Scene3DLabels key={selectedScene.id} visual={selectedScene.visual_3d}
          disabled={workLocked || claimEditing || transcriptEditing} onEditingChange={setVisualEditing} onChange={visual => {
            if (history && !workLocked) {
              setHistory(edit(history, { ...document, scenes: document.scenes.map(s => s.id === selectedScene.id ? { ...s, visual_3d: visual } : s) }));
              invalidatePlan();
            }
          }} />}
        {document && selectedScene && <SceneClaims key={selectedScene.id} claims={selectedScene.claims ?? []} sourceIds={document.sources}
          disabled={workLocked || visualEditing || transcriptEditing} onEditingChange={setClaimEditing} onChange={claims => {
            if (history && !workLocked) {
              const next = { ...document, scenes: document.scenes.map(s => s.id === selectedScene.id ? { ...s, claims } : s) };
              setHistory(edit(history, next)); invalidatePlan();
            }
          }} />}
      </aside>
      <section className="panel timeline" aria-labelledby="timeline-title"><div className="section-head"><h2 id="timeline-title">Timeline</h2><span>{document ? timeline(document).at(-1)?.end.toFixed(1) : '0.0'} seconds · English · silent render</span></div>
        <ol>{document && timeline(document).map((scene, i) => <li key={scene.id} className={selectedScene?.id === scene.id ? 'selected' : ''}>
          <button disabled={frozen} onClick={() => setSceneIndex(i)} aria-label={`Select scene ${i + 1}: ${scene.title.en || 'Untitled'}`} aria-pressed={selectedScene?.id === scene.id}><small>{scene.start.toFixed(1)}–{scene.end.toFixed(1)}s</small><strong>{scene.title.en || `Scene ${i + 1}`}</strong></button>
          <div className="toolbar"><button aria-label={`Move scene ${i + 1} earlier`} disabled={i === 0 || frozen} onClick={() => { change(moveScene(document, i, -1)); setSceneIndex(i - 1); }}>←</button><button aria-label={`Move scene ${i + 1} later`} disabled={i === document.scenes.length - 1 || frozen} onClick={() => { change(moveScene(document, i, 1)); setSceneIndex(i + 1); }}>→</button></div>
        </li>)}</ol>
        {!document && <div className="empty">Scene order and timing become visible after you admit a source.</div>}
        {document && <label>Narration / caption text<textarea rows={3} maxLength={8000} value={document.tracks.find(t => t.locale === 'en')?.narration ?? ''} disabled={frozen} onChange={e => change({ ...document, tracks: document.tracks.map(t => t.locale === 'en' ? { ...t, narration: e.target.value } : t) })} /><span className="fine">Stored for editing. This qualified render is silent: no cloud voice request is made.</span></label>}
        {document && <TranscriptEditor key={document.slug} segments={document.tracks.find(t => t.locale === 'en')?.segments ?? []}
          duration={document.scenes.reduce((sum, scene) => sum + scene.duration, 0)} sourceIds={document.sources}
          disabled={workLocked || claimEditing || visualEditing} onEditingChange={setTranscriptEditing} onChange={segments => {
            if (history && !workLocked) {
              setHistory(edit(history, { ...document, tracks: document.tracks.map(track => {
                if (track.locale !== 'en') return track;
                const { segments: _old, ...base } = track;
                return segments.length ? { ...base, segments } : base;
              }) }));
              invalidatePlan();
            }
          }} />}
      </section>
    </main>
    <section className="production panel" aria-labelledby="production-title"><div><p className="eyebrow">04 · PRODUCTION CONTROL</p><h2 id="production-title">Prepare. Review. Then render.</h2><p>Saving a project never starts a job. Changing its revision invalidates the previous plan.</p></div>
      <button disabled={!paired || !revision || dirty || frozen || !health?.capabilities.approved_silent_render} onClick={() => void perform('Preparing a bounded plan', async () => { if (revision) { setPlan(await api.plan(revision.project_id, revision.revision, profile)); setApproved(false); setReviewed(false); setJob(null); setMedia(null); } })}>Prepare render plan</button>
      {dirty && <p className="fine">Save your changes before preparing a plan.</p>}
      {plan && <div className="plan-review"><h3>Review this exact plan</h3><dl><dt>Revision</dt><dd>{plan.revision}</dd><dt>Output</dt><dd>{plan.profile}</dd><dt>Engines</dt><dd>{Object.values(plan.provider_resource_modes).join(' · ')}</dd><dt>Source fingerprints</dt><dd>{Object.keys(plan.asset_hashes).length}</dd><dt>Plan fingerprint</dt><dd><code>{plan.plan_hash}</code></dd></dl>
        <p className="fine">{Object.keys(plan.original_hashes ?? {}).length
          ? `${Object.keys(plan.original_hashes!).length} original-file fingerprints are bound to this plan in addition to its render derivatives.`
          : 'No structured original-file fingerprints are bound to this plan. Its admitted-asset fingerprints still apply.'}</p>
        <dl>{Object.entries(plan.limits).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{value.toLocaleString()}</dd></div>)}</dl>
        <label className="check"><input type="checkbox" checked={reviewed} disabled={frozen || approved || !!job} onChange={e => setReviewed(e.target.checked)} />I have reviewed the sources, scene order, output and local compute request.</label>
        <div className="toolbar"><button disabled={!reviewed || approved || frozen || !!job} onClick={() => void perform('Recording your approval', async () => { await api.approve(plan.id, plan.project_id, plan.revision); setApproved(true); setMessage('This exact plan is approved for 15 minutes. No render has started.'); })}>Approve this plan</button>
          <button className="primary" disabled={(!approved && !uncertain) || !!busy || !!job || !!uncertainTool || toolExecuting || claimEditing || visualEditing || transcriptEditing} onClick={() => void perform(uncertain ? 'Checking the same render request' : 'Starting approved render', run)}>{uncertain ? 'Retry the same request safely' : 'Run approved render'}</button></div>
        {uncertain && <p className="warning">The outcome of the request is unknown. The retry keeps the same plan and idempotency key; do not create a second plan.</p>}
      </div>}
      {job && <div className="job"><div className="section-head"><h3>{verifiedCompletion(job) ? 'Render completed — receipts available' : `Render ${job.status}`}</h3><span>{job.progress}%</span></div>
        <progress value={job.progress} max={100} aria-label="Render progress" />
        <p className="fine">A receipt identifies output bytes. It does not certify factual accuracy, a person’s identity, rights, or learning outcomes.</p>
        <div className="toolbar"><button disabled={!!busy} onClick={() => void perform('Refreshing status', async () => setJob(await api.job(job.id)))}>Refresh status</button><button disabled={!activeJob || !!busy} onClick={() => void perform('Requesting cancellation', async () => setJob(await api.cancel(job.id)))}>Cancel render</button></div>
        <ul className="receipt-list">{job.receipts.map(receipt => <li key={receipt.name}><strong>{receipt.role}</strong><span>{receipt.name} · {(receipt.size / 1024).toFixed(1)} KB</span><code>{receipt.sha256}</code>
          <button disabled={!!busy || receipt.size > 100_000_000} onClick={() => void perform('Checking artifact bytes', async () => {
            const blob = await api.artifact(job.id, receipt); const url = URL.createObjectURL(blob);
            setMedia({ url, type: receipt.media_type, name: receipt.name }); setMessage('Artifact bytes match the recorded SHA-256. Inspect the content; this is not a factual certification.');
          })}>Inspect {receipt.role}</button></li>)}</ul>
        {media && <div className="media-result"><h3>Verified file: {media.name}</h3>
          {media.type.startsWith('video/') && <VerifiedVideoPreview key={media.url} url={media.url}
            loadCaptions={captionReceipt ? () => api.artifact(job.id, captionReceipt) : undefined} />}
          {media.type.startsWith('image/') && <img src={media.url} alt="Generated poster for the reviewed project" />}
          <a href={media.url} download={media.name}>Save this verified file</a><button onClick={() => setMedia(null)}>Close artifact</button>
        </div>}
        {job.status === 'complete' && !verifiedCompletion(job) && <p role="alert" className="warning">The server reports completion, but expected proof artifacts are missing. This result is not qualified.</p>}
      </div>}
    </section>
    <section className="agent-tools panel" aria-labelledby="agent-tools-title"><h2 id="agent-tools-title">Agent tools · proposals before production</h2>
      <p>{nativeTools ? toolStatus : 'Native WebMCP is unavailable in this browser. The manual studio works without it; no polyfill is installed.'}</p>
      <p className="fine">Enable only for this selected, saved project. Eight tools share the server catalogue. None can approve a plan, request a password, publish, or pay. Source text and tool results are untrusted data.</p>
      {nativeTools && <div className="toolbar"><button disabled={!paired || !revision || dirty || frozen || toolDefinitions.length > 0} onClick={() => void perform('Connecting the native tool catalogue', enableTools)}>Enable agent tools for this project</button>
        <button disabled={!toolDefinitions.length || frozen} onClick={() => void perform('Disconnecting native tools', disableTools)}>Disconnect agent tools</button></div>}
      {toolDefinitions.length > 0 && <ul>{toolDefinitions.map(t => <li key={t.name}><code>{t.name}</code> — {t.description}</li>)}</ul>}
      {toolDefinitions.length > 0 && (!nativeTools?.getTools || !nativeTools?.executeTool) && <p className="fine">This browser exposes tool registration, but not the native inspection API. Registration is observed; end-to-end native execution remains unverified in this browser build.</p>}
      {toolDefinitions.length > 0 && nativeTools?.getTools && nativeTools.executeTool && <button disabled={frozen || !revision || dirty} onClick={() => {
        if (nativeTools && revision) {
          // Do not acquire the human mutation lock: this invokes our read-only tool callback.
          setError(null); void inspectThroughNativeContext(nativeTools, revision.project_id, revision.revision)
            .catch(cause => setError(cause instanceof StudioError ? cause : new StudioError('NATIVE_INSPECTION_UNAVAILABLE')));
        }
      }}>Inspect project through native WebMCP</button>}
      {toolEvent && <div className="proposal"><h3>{toolEvent.response.ok ? 'Inspect the agent result' : 'Agent request rejected'}</h3><p><code>{toolEvent.tool}</code></p>
        <pre aria-label="Agent proposal or inspection result">{JSON.stringify(toolEvent.response.result ?? toolEvent.response.error, null, 2)}</pre>
        {toolEvent.response.result?.effect === 'proposal_only' && <button disabled={frozen || dirty || !revision} onClick={() => { if (document && revision) {
          try { const proposed = applyProposal(document, toolEvent, revision.document_hash); change(proposed); setMessage('Proposal applied to an undoable local draft only. Inspect it, then save to create a new revision.'); }
          catch { setError(new StudioError('TOOL_CONTEXT_CHANGED')); }
        } }}>Apply reviewed proposal to local draft</button>}
      </div>}
      {uncertainTool && <div className="warning"><p>An agent render request has an unknown outcome. Do not approve another plan. This action checks the same project, plan and request key.</p>
        <button disabled={!!busy || toolExecuting} onClick={() => void perform('Checking the existing agent render request', async () => receiveTool({ ...uncertainTool,
          response: await api.invokeTool(uncertainTool.tool, uncertainTool.argumentsValue) }))}>Check the same agent render request</button></div>}
    </section>
    <section className="capabilities panel" aria-labelledby="capabilities-title"><h2 id="capabilities-title">Capability status, not promises</h2><div className="capability-grid">
      <div><strong>Manual studio</strong><p>{health?.capabilities.approved_silent_render ? 'Local silent FFmpeg path available; this session’s result is checked separately.' : 'Renderer availability not yet verified.'}</p></div>
      <EngineStatus key={String(paired)} api={api} disabled={!paired || frozen} />
    </div></section>
    <footer><p>SecuredMe · Human direction, local production, inspectable evidence.</p>{paired && <button disabled={!!busy || activeJob || toolExecuting} onClick={() => void perform('Revoking this studio session', async () => { try { await disableTools(); await api.disconnect(); } finally { setPaired(false); setApproved(false); setReviewed(false); } })}>Disconnect this studio</button>}</footer>
  </div>;
}
