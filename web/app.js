const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const teacher = location.pathname === '/laerer';
const statusText={workflow_wait:'Afventer lærerens workflow',queued:'I kø',preparing:'Klargør modellen',dispatching:'Sender til printer',starting:'Starter snart',printing:'Printer',recovering:'Kontrollerer printerens job',attention:'Spørg en voksen',done:'Færdig!',failed:'Kunne ikke printes',cancelled:'Afbrudt'};
const printerText={offline:'Ikke forbundet',disabled:'Uden for børnekøen',ready:'Klar til print',printing:'Printer lige nu',preparing:'Klargør et print',bed:'Venter på ryddet plade',attention:'Skal ses af en voksen'};
const qualityText={fast:'Hurtig',standard:'Standard',beautiful:'Smuk'};
let state=null, connected=false, file=null, fileData=null, colour='any', quality='standard', submitting=false, requestId=null, recentJob=null;
let adminReady=false, sessionChecked=false;
let fileSelection=0;
// Remove the credential retained by older browser versions.
try{sessionStorage.removeItem('pv_admin')}catch{}
let mine=new Set(JSON.parse(localStorage.getItem('pv_jobs')||'[]'));
let recentReceipt=null;
const libraryRequests=new Map();
const libraryInflight=new Set();
let libraryEntries=[];
let libraryPage=1, libraryPageData=null, libraryRead=0, libraryEpoch=0;
let libraryPolicyDirty=false, libraryPolicyVersion=0, libraryPolicyRequest=null, librarySearchTimer=null;
const libraryDeletes=new Map();
try{recentReceipt=JSON.parse(localStorage.getItem('pv_receipt')||'null');recentJob=recentReceipt?.id||null}catch{}
const uuid=()=>crypto.randomUUID?crypto.randomUUID():Array.from(crypto.getRandomValues(new Uint8Array(20)),v=>v.toString(16).padStart(2,'0')).join('');
const swatch=(hex,extra='')=>`<span class="swatch ${extra}" style="--swatch:${esc(hex)}" aria-hidden="true"></span>`;
const printerName=id=>state?.printers.find(p=>p.id===id)?.name||'Afventer printer';
const colourName=hex=>state?.colours.find(c=>c.hex===hex)?.name||'Valgt farve';
// Keep the student's palette tied to live, enabled printers, with at most four colours.
function availableColours(snapshot=state){
  const printers=(snapshot?.printers||[]).filter(p=>p.enabled&&p.status!=='offline'&&p.status!=='disabled');
  return (snapshot?.colours||[]).filter(c=>printers.some(p=>c.printers.includes(p.id)&&p.colours.includes(c.hex))).slice(0,4);
}

async function api(path, body, authenticated=false){
  const response=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json();if(!response.ok){if(authenticated&&response.status===401)setTeacherSession(false);const error=new Error(data.error||'Noget gik galt. Prøv igen.');error.status=response.status;throw error}return data;
}
// Small authenticated bridge for teacher-only modules loaded after this file.
window.teacherPrinterApi=(path,body)=>api(path,body,true);
function showError(id,message){$(id).textContent=message;$(id).hidden=!message}
function renderColours(){
  const focusValue=document.activeElement?.dataset.colour;
  const available=availableColours();
  $('colours').innerHTML=available.length?`<button class="colour-option any ${colour==='any'?'selected':''}" data-colour="any" aria-pressed="${colour==='any'}"><span class="any-icon" aria-hidden="true"></span><span><strong>Vælg for mig</strong><small>Én farve fra en ledig printer</small></span></button>`+available.map(c=>`<button class="colour-option ${colour===c.hex?'selected':''}" data-colour="${c.hex}" aria-pressed="${colour===c.hex}" aria-label="${esc(c.name)}${c.ready?', ledig printer':', kan sættes i kø'}" title="${esc(c.name)} · ${c.ready?'Ledig printer':'Venter på en ledig printer'}">${swatch(c.hex)}<span>${esc(c.name)}</span></button>`).join(''):'';
  $('colours').hidden=!available.length;
  if(focusValue)$('colours').querySelector(`[data-colour="${CSS.escape(focusValue)}"]`)?.focus({preventScroll:true});
  $('colour-help').hidden=!available.length;
  $('colour-help').textContent=!available.length?'':colour==='any'?'Farverne kommer fra de tilkoblede printere. Vælg selv, eller lad printeren vælge.':available.some(c=>c.hex===colour)?'Din valgte farve kan stå i kø, selv om printeren er optaget.':'Den valgte farve er ikke længere tilgængelig. Vælg en ny.';
}
function renderPrinters(){
  $('ready-count').textContent=state.printers.length?`${state.ready} ledige`:'Ikke sat op';
  $('printers').innerHTML=state.printers.length?state.printers.map(p=>`<div class="printer"><div class="printer-top"><span class="printer-mark" aria-hidden="true">${esc(p.model==='A1 mini'?'mini':p.model)}</span><div><strong class="printer-title">${esc(p.name)}</strong><span class="printer-state ${p.status}">${printerText[p.status]}${p.status==='printing'?` · ${p.progress}%`:''}</span></div></div><div class="printer-swatches" aria-label="PLA-farver">${p.colours.map(c=>swatch(c)).join('')}</div>${p.status==='printing'?`<div class="progress" role="progressbar" aria-label="Printstatus" aria-valuenow="${p.progress}" aria-valuemin="0" aria-valuemax="100"><span style="--progress:${p.progress}%"></span></div>`:''}</div>`).join(''):'<p class="small-note">Læreren skal forbinde skolens printere, før vi kan printe.</p>';
}
function renderQueue(){
  const jobs=state.jobs.filter(j=>j.status==='queued').sort((a,b)=>a.created-b.created);
  $('queue-count').textContent=`${jobs.length} i kø`;
  $('queue-list').innerHTML=jobs.length?jobs.map(j=>{const label=j.filename||'Printopgave';return `<article class="queue-row"><span class="queue-file" aria-hidden="true">STL</span><div class="queue-name"><strong title="${esc(label)}">${esc(label)}</strong><small>${esc(qualityText[j.quality])} · ${(j.meta?.size||[]).map(v=>Number(v).toLocaleString('da-DK')).join(' × ')} mm${mine.has(j.id)?' · Dit print':''}</small></div><div class="queue-colour">${j.colour||j.actual_colour?swatch(j.actual_colour||j.colour):'<span class="any-icon" style="width:20px;height:20px" aria-hidden="true"></span>'}<span>${j.colour?esc(colourName(j.colour)):'Vælg for mig'}</span></div><span class="status-pill queued">I kø</span><span class="queue-printer">${esc(printerName(j.printer_id))}</span></article>`}).join(''):'<div class="queue-empty">Der er ingen print i kø lige nu.</div>';
  const active=state.jobs.filter(j=>['workflow_wait','preparing','dispatching','starting','printing','recovering','attention'].includes(j.status));
  const liveRecent=recentJob&&state.jobs.find(j=>j.id===recentJob);
  const recent=liveRecent ? (liveRecent.filename || recentReceipt?.id!==liveRecent.id || !recentReceipt?.filename ? liveRecent : {...liveRecent,filename:recentReceipt.filename}) : (recentReceipt?.id===recentJob ? recentReceipt : null);
  const shown=[...(recent?[recent]:[]),...active,...jobs].filter((j,i,all)=>all.findIndex(v=>v.id===j.id)===i).slice(0,5);
  $('mini-queue-summary').textContent=`${state.paused?'Køen er på pause · ':''}${jobs.length} i kø · ${active.length} i gang`;
  $('mini-queue-list').innerHTML=shown.length?shown.map(j=>`<div class="mini-queue-row ${j.id===recentJob?'is-mine':''}"><strong>${esc(j.filename||'Printopgave')}</strong><span>${j.id===recentJob?'Dit seneste print · ':''}${esc(j.isolated&&!j.physical_resolved?'Afventer fysisk kontrol':statusText[j.status]||'Modtaget')}${j.printer_id?' · '+esc(printerName(j.printer_id)):''}</span></div>`).join(''):'<p class="small-note">Her vises dit print, når du har sendt det.</p>';
}
function renderSend(){
  const available=availableColours();
  const validColour=available.length>0&&(colour==='any'||available.some(c=>c.hex===colour));
  $('send-button').disabled=!file||submitting||!connected||!validColour||!state?.printers.length;
  $('send-button').innerHTML=submitting?'Sender din model…':'Send til print <span aria-hidden="true">↗</span>';
  if(!file){$('send-title').textContent='Vælg en model';$('send-help').textContent='Vælg en STL-fil for at komme i gang.';return}
  $('send-title').textContent=`${qualityText[quality]} · ${colour==='any'?'Printeren vælger farven':colourName(colour)}`;
  const ready=colour==='any'?state?.ready:available.find(c=>c.hex===colour)?.ready;
  $('send-help').textContent=!connected?'Venter på forbindelsen til værkstedet.':!available.length?'Afventer en tilkoblet printer med PLA.':!validColour?'Vælg en farve, der er tilgængelig.':state?.paused?'Køen er på pause. Du kan stadig stille dit print i kø.':ready?'Vi finder en ledig printer, som din model passer til.':'Printet starter, når en passende printer er ledig.';
  // A retry may still retrieve an accepted receipt after a lost response.
  if(connected&&state?.print_access?.message)$('send-help').textContent=state.print_access.message;
}
function renderReceipt(){
  if(!recentJob)return;
  const live=state.jobs.find(j=>j.id===recentJob);
  const job=live ? (live.filename || recentReceipt?.id!==live.id || !recentReceipt?.filename ? live : {...live,filename:recentReceipt.filename}) : (recentReceipt?.id===recentJob ? recentReceipt : null);
  if(!job)return;
  const unresolved=job.isolated&&!job.physical_resolved;
  const needsTeacher=unresolved||['failed','attention','recovering'].includes(job.status);
  const text=unresolved?'Printets fysiske status er uafklaret. Spørg en voksen.':job.status==='queued'?'Din model er i kø. Du behøver ikke holde siden åben.':job.status==='done'?`Dit print er færdigt på ${printerName(job.printer_id)}. Spørg en voksen, før du henter det.`:job.status==='cancelled'?'Printet er afbrudt. Spørg en voksen.':job.status==='printing'?`Din model bliver printet på ${printerName(job.printer_id)}.`:needsTeacher?'Læreren kan se og håndtere din model på lærersiden.':`Vi gør din model klar. ${job.printer_id?printerName(job.printer_id):'Printeren vælges automatisk.'}`;
  $('print-confirmation-filename').textContent=job.filename||'Printopgave';
  $('print-confirmation-status').textContent=text;
  $('print-confirmation-demo').hidden=!state.demo;
  $('receipt').hidden=false;$('receipt').innerHTML=`<strong>${esc(needsTeacher?'Afventer læreren':statusText[job.status])}</strong>${esc(text)}${state.demo?'<br><small>Dette er en demonstration. Der bliver ikke printet fysisk.</small>':''}<br><button class="secondary" id="new-print">Lav et print mere</button>`;
}
function stoppedJobAction(p){
  const job=state.jobs.find(j=>j.printer_id===p.id&&['attention','recovering'].includes(j.status));
  if(!job)return '';
  if(job.resolution_options?.includes('isolated'))return isolateJobAction(job);
  const allowed=job.resolution_options?.includes('cancelled');
  const help=allowed?'Bekræft, at printet er stoppet. Kontrollér derefter printeren og vælg “Pladen er ryddet”. Eventuelle printerfejl skal først afklares på printerens skærm.':p.machine_state==='OFFLINE'?'Afventer forbindelse til printeren.':['RUNNING','PREPARE','PAUSE'].includes(p.machine_state)?'Printeren melder stadig et igangværende eller pauset print. Stop det på printeren først.':'Ret eventuelle fejl på printerens skærm. Afventer en frisk status om, at printeren er stoppet uden fejl.';
  return `<div><button class="secondary" data-resolve="${job.id}" data-result="cancelled" ${allowed?'':'disabled'}>Printet er stoppet</button><p class="small-note">${esc(help)}</p></div>`;
}
function isolateJobAction(job){
  return `<div><button class="secondary" data-resolve="${job.id}" data-result="isolated">Isolér uafklaret job</button><p class="small-note">Printeren kan stadig køre. Isolation frigiver ikke pladen. Ret forbindelsen og kontrollér printeren fysisk før næste print.</p></div>`;
}
function resolveJob(button){
  if(button.dataset.result==='isolated'&&!confirm('Isolér det uafklarede job i køen? Printeren kan stadig køre. Isolation stopper ikke printeren og frigiver ikke pladen. Kontrollér printeren fysisk, og ret forbindelsen.'))return;
  adminAction('/api/jobs/resolve',{id:button.dataset.resolve,result:button.dataset.result});
}
function renderTeacher(){
  if(!adminReady)return;
  $('queue-mode').textContent=state.paused?'Køen er sat på pause':'Køen kører';$('pause-queue').textContent=state.paused?'Start køen igen':'Sæt køen på pause';
  $('teacher-printers').innerHTML=state.printers.length?state.printers.map(p=>`<article class="teacher-printer"><div class="panel-heading"><h2>${esc(p.name)}</h2><span class="muted">${esc(p.model)}</span></div><span class="printer-state ${p.status}">${printerText[p.status]}${p.status==='printing'?` · ${p.progress}%`:''}</span><div class="printer-swatches">${p.colours.map(c=>swatch(c)).join('')||'<span class="small-note">Ingen genkendte PLA-farver</span>'}</div><button class="primary" data-release="${p.id}" ${!p.can_release?'disabled':''}>${p.bed_clear?'Pladen er frigivet':'Pladen er ryddet'}</button>${stoppedJobAction(p)}${state.demo&&p.status==='printing'?`<button class="secondary" data-finish="${p.id}">Afslut demoprint</button>`:''}${!state.demo?`<p class="small-note">${['RUNNING','PREPARE','PAUSE'].includes(p.machine_state)?'Printer nu · '+p.progress+' %':p.machine_state==='OFFLINE'?'Ingen statusforbindelse':'Forbundet · afsluttet eller stoppet'}</p>${p.model==='H2D'?'<p class="small-note">Kamera og lærerprint i Bambu Studio / Handy.</p>':`<button class="secondary" data-camera="${p.id}" aria-label="Vis kamera: ${esc(p.name)}">Vis kamera</button>`}`:''}${p.message?`<p class="small-note">${esc(p.message)}</p>`:''}</article>`).join(''):'<p class="muted">Ingen printere er tilsluttet endnu.</p>';
  const jobs=state.jobs.filter(j=>['attention','recovering','failed'].includes(j.status));
  $('attention-jobs').innerHTML=jobs.length?jobs.map(j=>{const archived=libraryEntries.find(e=>e.jobs?.some(job=>job.id===j.id));return `<div class="attention-row"><div><strong>${esc(j.filename)} · ${esc(statusText[j.status])}</strong><p>${esc(j.message)} ${esc(j.printer_id?printerName(j.printer_id):'')}</p></div><div class="attention-actions">${j.status==='failed'?`<button class="secondary" data-diagnostic="${esc(j.id)}">Se fejl</button>${archived?`<button class="secondary" data-library-reprint="${esc(archived.digest)}" data-library-name="${esc(archived.filename)}" data-library-colour="${esc(archived.colour||'')}" data-library-quality="${esc(archived.quality||'standard')}">Send igen</button>`:''}`:''}${j.status!=='failed'?`<button class="secondary" data-diagnostic="${esc(j.id)}">Se fejl</button>`:''}${j.resolution_options?.includes('isolated')?isolateJobAction(j):''}${j.status==='attention'?`<button class="secondary" data-resolve="${j.id}" data-result="done" ${j.resolution_options?.includes('done')?'':'disabled'}>Printet er færdigt</button><button class="secondary" data-resolve="${j.id}" data-result="cancelled" ${j.resolution_options?.includes('cancelled')?'':'disabled'}>Printet er stoppet</button>`:''}</div></div>`}).join(''):'<p class="muted">Ingen print i fejlkøen.</p>';
  const queued=state.jobs.filter(j=>j.status==='queued').sort((a,b)=>a.created-b.created);
  $('teacher-queue').innerHTML=queued.length?queued.map(j=>`<div class="attention-row"><div><strong>${esc(j.filename)}</strong><p>${esc(j.message||'Venter på en passende ledig printer med ryddet plade.')}</p></div><div class="attention-actions"><button class="secondary" data-resolve="${j.id}" data-result="cancelled">Fjern fra køen</button></div></div>`).join(''):'<p class="muted">Ingen print i kø.</p>';
}
function libraryEntryId(entry){return String(entry.digest||'')}
function renderLibrary(entries){
  libraryEntries=Array.isArray(entries)?entries:entries?.files||[];
  libraryPageData=Array.isArray(entries)?null:entries;
  $('library-list').innerHTML=libraryEntries.length?libraryEntries.map(entry=>{
    const id=libraryEntryId(entry),name=entry.filename||id,status=entry.latest_status||'',blocked=entry.in_use===true;
    return `<div class="attention-row library-row"><div><strong>${esc(name)}</strong><p>${esc(entry.last_used?new Date(entry.last_used*1000).toLocaleString('da-DK'):'')}${status?` · ${esc(statusText[status]||status)}`:''} · ${formatLibraryBytes(entry.size||0)}</p><p class="small-note">${blocked?'Beskyttet af køen eller en uafklaret plade.':entry.expired?'Kan ryddes op efter den valgte opbevaringsperiode.':''}</p></div><div class="attention-actions"><button class="secondary" data-library-download="${esc(id)}" data-library-name="${esc(name)}">Hent</button><button class="secondary" data-library-reprint="${esc(id)}" data-library-name="${esc(name)}" data-library-colour="${esc(entry.colour||'')}" data-library-quality="${esc(entry.quality||'standard')}">Send til køen</button><button class="secondary" data-library-delete="${esc(id)}" data-library-generation="${esc(entry.generation)}" data-library-name="${esc(name)}" ${blocked?'disabled':''}>Slet model</button>${status==='failed'?`<button class="secondary" data-diagnostic="${esc(entry.latest_job_id||'')}">Se fejl</button>`:''}</div></div>`;
  }).join(''):'<p class="muted">Ingen filer matcher søgningen.</p>';
  const storage=libraryPageData?.storage,policy=libraryPageData?.policy;
  for(const pending of storage?.pending_deletions||[]){
    if(!libraryDeletes.has(pending.digest))libraryDeletes.set(pending.digest,{name:'Model '+pending.digest.slice(0,12),failed:true,body:{request_id:pending.request_id,generation:pending.generation,confirmed:true}});
  }
  if($('library-storage'))$('library-storage').textContent=storage?`${formatLibraryBytes(storage.used_bytes)} af ${formatLibraryBytes(storage.quota_bytes)} i biblioteket · ${formatLibraryBytes(storage.free_bytes)} ledigt på drevet.`:'';
  if($('library-storage-warning')){
    const recovery=libraryPageData?.recovery;
    const notices=[];
    if(storage?.warning)notices.push('Biblioteket nærmer sig lagerloftet. Vælg gamle modeller til sletning eller justér loftet.');
    if(recovery?.preserved_count)notices.push(`${recovery.preserved_count} midlertidige indsendelser kræver kontrol af lagringen. Filerne er bevaret; systemet gentager kontrollen automatisk.`);
    if(recovery?.deferred||storage?.pending_deletions_deferred)notices.push('Genopretning fortsætter i mindre portioner.');
    $('library-storage-warning').hidden=!notices.length;
    $('library-storage-warning').textContent=notices.join(' ');
  }
  if(policy&&!libraryPolicyDirty&&$('library-retention-days')){
    $('library-retention-days').value=policy.retention_days??90;
    $('library-quota-mib').value=Math.ceil((policy.quota_bytes??1073741824)/1048576);
    libraryPolicyVersion=policy.version;
  }
  if($('library-prev'))$('library-prev').disabled=libraryPage<=1;
  if($('library-next'))$('library-next').disabled=!libraryPageData?.has_more;
  if($('library-page'))$('library-page').textContent=`Side ${libraryPage} · ${libraryPageData?.total??libraryEntries.length} modeller`;
  renderLibraryPending();
}
function formatLibraryBytes(bytes){return `${(Number(bytes||0)/1048576).toLocaleString('da-DK',{maximumFractionDigits:1})} MiB`}
function renderLibraryPending(){
  if($('library-pending'))$('library-pending').innerHTML=[...libraryDeletes.entries()].filter(([,request])=>request.failed).map(([id,request])=>`<p class="small-note">Sletningen af “${esc(request.name)}” er ikke bekræftet. <button class="secondary" type="button" data-library-delete-retry="${esc(id)}">Prøv samme sletning igen</button></p>`).join('');
}
async function refreshLibrary(){
  if(!adminReady)return;
  const read=++libraryRead,epoch=libraryEpoch,params=new URLSearchParams({page:String(libraryPage),page_size:'25',query:($('library-search')?.value||'').trim()});
  try{const data=await api('/api/library?'+params,undefined,true);if(!adminReady||epoch!==libraryEpoch||read!==libraryRead)return;showError('library-error','');renderLibrary(data)}catch(error){if(!adminReady||epoch!==libraryEpoch||read!==libraryRead)return;showError('library-error',error.message);$('library-list').innerHTML='<p class="muted">Filbiblioteket kunne ikke hentes.</p>'}
}
async function deleteLibrary(id,name,generation,retry=false){
  if(!adminReady||libraryInflight.has(id))return;
  let request=libraryDeletes.get(id);
  if(!retry&&(!request||request.body.generation!==generation)){
    if(!confirm(`Slet “${name}” fra biblioteket? Filnavne i afsluttet jobhistorik erstattes med et neutralt navn. Aktive print og uafklarede plader bevares.`))return;
    request={name,body:{request_id:uuid(),generation,confirmed:true},failed:false};libraryDeletes.set(id,request);
  }
  if(!request)return;
  const epoch=libraryEpoch;libraryInflight.add(id);request.failed=false;renderLibraryPending();
  try{await api(`/api/library/${encodeURIComponent(id)}/delete`,request.body,true);if(!adminReady||epoch!==libraryEpoch)return;libraryDeletes.delete(id);$('library-result').textContent=`Modellen “${name||request.name}” er slettet.`;await refreshLibrary()}
  catch(error){if(!adminReady||epoch!==libraryEpoch)return;request.failed=true;showError('library-error',error.message);renderLibraryPending()}
  finally{libraryInflight.delete(id)}
}
async function saveLibraryPolicy(event){
  event.preventDefault();if(!adminReady)return;
  const form=$('library-policy-form'),button=form.querySelector('button');if(button.disabled)return;
  const values={expected_version:libraryPolicyVersion,retention_days:Number($('library-retention-days').value),quota_bytes:Number($('library-quota-mib').value)*1048576};
  if(!libraryPolicyRequest||JSON.stringify(libraryPolicyRequest.values)!==JSON.stringify(values))libraryPolicyRequest={values,request_id:uuid()};
  const request=libraryPolicyRequest,epoch=libraryEpoch;button.disabled=true;
  try{await api('/api/library/policy',{...request.values,request_id:request.request_id},true);if(!adminReady||epoch!==libraryEpoch)return;libraryPolicyDirty=false;libraryPolicyRequest=null;$('library-result').textContent='Opbevaringsreglen er gemt. Ingen modeller er slettet.';await refreshLibrary()}
  catch(error){if(adminReady&&epoch===libraryEpoch)showError('library-error',error.message+' Samme forsøg bevares ved genforsøg.')}
  finally{button.disabled=false}
}
async function showDiagnostic(id){
  try{const d=await api(`/api/jobs/diagnostics/${encodeURIComponent(id)}`,undefined,true);$('diagnostic-content').textContent=[d.stage&&`Trin: ${d.stage}`,d.error_type&&`Type: ${d.error_type}`,d.message&&`Besked: ${d.message}`,d.created&&`Tid: ${new Date(d.created*1000).toLocaleString('da-DK')}`,d.log_tail&&`\nLog:\n${d.log_tail}`].filter(Boolean).join('\n');$('diagnostic-dialog').showModal()}catch(error){showError('admin-error',error.message)}
}
async function reprintLibrary(id,name,archivedColour,archivedQuality){
  if(libraryInflight.has(id))return;
  let request=libraryRequests.get(id);if(!request){request={request_id:uuid(),quality:archivedQuality||'standard',colour:archivedColour||null};libraryRequests.set(id,request)}
  const optionColour=request.colour||'vælg automatisk';
  if(!confirm(`Send “${name}” til køen igen?
Indstillinger: ${qualityText[request.quality]||request.quality}, ${optionColour}.`))return;
  libraryInflight.add(id);document.querySelector(`[data-library-reprint="${CSS.escape(id)}"]`)?.setAttribute('disabled','');
  try{await api(`/api/library/${encodeURIComponent(id)}/reprint`,request,true);libraryRequests.delete(id);await refreshLibrary();state=await api('/api/state');renderTeacher();}catch(error){showError('library-error',`${error.message} Samme request-id bevares, så du kan trykke igen uden dobbelt print.`)}finally{libraryInflight.delete(id);document.querySelector(`[data-library-reprint="${CSS.escape(id)}"]`)?.removeAttribute('disabled')}
}
async function downloadLibrary(id,name){try{const response=await fetch(`/api/library/${encodeURIComponent(id)}/download`,{credentials:'same-origin'});if(response.status===401)setTeacherSession(false);const data=await response.blob();if(!response.ok)throw new Error((await data.text())||'Filen kunne ikke hentes.');const url=URL.createObjectURL(data),link=document.createElement('a');link.href=url;link.download=name||'model.stl';document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(error){showError('library-error',error.message)}}
async function poll(){
  try{const next=await api('/api/state');const previous=state;state=next;connected=true;$('connection-error').hidden=true;$('demo-banner').hidden=!state.demo;
    if(JSON.stringify(availableColours(previous))!==JSON.stringify(availableColours()))renderColours();
    renderPrinters();renderQueue();renderSend();renderReceipt();renderTeacher();
    if(teacher&&!sessionChecked){const session=await api('/api/auth/status');sessionChecked=true;setTeacherSession(session.teacher)}
    if(teacher&&adminReady)window.refreshMaintenance?.();
    if(teacher&&adminReady)window.refreshFirmware?.();
    if(teacher&&adminReady)window.refreshPrintAccess?.();
    if(teacher&&adminReady)refreshLibrary();
  }catch(error){connected=false;$('connection-error').hidden=false;renderSend()}
  setTimeout(poll,2500);
}

async function selectFile(selected, imported=false){
  if(!selected||submitting)return;
  clearFile();
  const selection=fileSelection;
  showError('upload-error','');
  if(!selected.name.toLowerCase().endsWith('.stl')){showError('upload-error','Vælg en STL-fil. Den slutter med .stl.');return}
  if(selected.size>25*1024*1024){showError('upload-error','Filen er for stor. Vælg en STL-fil på højst 25 MB.');return}
  if(!selected.size){showError('upload-error','Filen er tom. Eksportér din model igen.');return}
  let buffer,size;
  try{
    buffer=await selected.arrayBuffer();if(selection!==fileSelection)return;
    size=window.modelPreview.validate(buffer);
  }catch(error){
    if(selection!==fileSelection)return;
    const message='Filen kan ikke bruges. Gem modellen som STL igen fra dit tegneprogram, eller spørg din lærer.';
    if(imported)throw Error(message);
    showError('upload-error',message);return;
  }
  file=selected;fileData=buffer;requestId=uuid();
  $('dropzone').hidden=true;$('file-preview').hidden=false;$('file-name').textContent=file.name;
  $('file-size').textContent=`${size.map(v=>v.toLocaleString('da-DK',{maximumFractionDigits:1})).join(' × ')} mm`;
  try{window.modelPreview.load(fileData,colour==='any'?'#c62b2f':colour)}
  catch(error){$('model-canvas').textContent='Filen er klar. Denne computer kan ikke vise modellen i 3D.'}
  renderSend();
}
function clearFile(){fileSelection++;file=null;fileData=null;requestId=null;$('file-input').value='';$('dropzone').hidden=false;$('file-preview').hidden=true;window.modelPreview?.clear();renderSend()}
window.ElevhubImportFile=async selected=>{if(!/\.stl$/i.test(selected.name)||!selected.size||selected.size>25*1024*1024)throw Error('Vælg en STL-fil på højst 25 MB.');await selectFile(selected,true);};
$('file-input').addEventListener('change',event=>selectFile(event.target.files[0]));
$('remove-file').addEventListener('click',()=>{if(!submitting)clearFile()});
$('dropzone').addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();$('file-input').click()}});
['dragenter','dragover'].forEach(type=>$('dropzone').addEventListener(type,event=>{event.preventDefault();$('dropzone').classList.add('dragging')}));
['dragleave','drop'].forEach(type=>$('dropzone').addEventListener(type,event=>{event.preventDefault();$('dropzone').classList.remove('dragging')}));
$('dropzone').addEventListener('drop',event=>{if(event.dataTransfer.files.length!==1){showError('upload-error','Vælg én STL-fil ad gangen.');return}selectFile(event.dataTransfer.files[0])});
$('colours').addEventListener('click',event=>{const button=event.target.closest('[data-colour]');if(!button||submitting)return;colour=button.dataset.colour;requestId=uuid();window.modelPreview?.colour(colour==='any'?'#c62b2f':colour);renderColours();renderSend()});
$('qualities').addEventListener('click',event=>{const button=event.target.closest('[data-quality]');if(!button||submitting)return;quality=button.dataset.quality;requestId=uuid();document.querySelectorAll('[data-quality]').forEach(b=>{b.classList.toggle('selected',b.dataset.quality===quality);b.setAttribute('aria-pressed',b.dataset.quality===quality)});renderSend()});
$('send-button').addEventListener('click',async()=>{
  if(!file||submitting)return;
  submitting=true;renderSend();showError('upload-error','');
  try{
    const filename=file.name;
    const response=await fetch('/api/jobs',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(filename),'X-Colour':colour,'X-Quality':quality,'X-Request-ID':requestId},body:fileData});
    const result=await response.json();if(!response.ok)throw new Error(result.error);
    recentJob=result.id;recentReceipt={id:result.id,status:result.status,filename};mine.add(result.id);
    // An accepted upload stays accepted even if local storage or the next poll fails.
    try{localStorage.setItem('pv_jobs',JSON.stringify([...mine].slice(-100)));localStorage.setItem('pv_receipt',JSON.stringify(recentReceipt))}catch{}
    clearFile();renderReceipt();renderQueue();$('print-confirmation').showModal();
    try{state=await api('/api/state');renderReceipt();renderQueue()}catch{}
  }
  catch(error){showError('upload-error',error instanceof TypeError?'Filen kunne ikke sendes. Prøv igen. Samme indsendelse oprettes kun én gang.':error.message)}
  finally{submitting=false;renderSend()}
});
$('receipt').addEventListener('click',event=>{if(event.target.closest('#new-print'))$('file-input').click()});
$('print-confirmation-close').addEventListener('click',()=>{$('print-confirmation').close();$('mini-queue-heading').scrollIntoView({behavior:'smooth',block:'nearest'})});
function setTeacherSession(ready){
  adminReady=ready;window.teacherSessionReady=ready;$('login-form').hidden=ready;$('teacher-content').hidden=!ready;$('teacher-logout').hidden=!ready;
  if(ready){renderTeacher();window.refreshPrintAccess?.(true);window.teacherPrinterSettings?.refresh?.()}
  else{libraryEpoch++;libraryRead++;libraryEntries=[];libraryPageData=null;libraryPage=1;libraryPolicyDirty=false;libraryPolicyRequest=null;libraryDeletes.clear();libraryInflight.clear();libraryRequests.clear();$('library-list').innerHTML='';if($('library-storage'))$('library-storage').textContent='';if($('library-result'))$('library-result').textContent='';if($('library-pending'))$('library-pending').innerHTML='';$('admin-token').value='';window.closeTeacherCamera?.();window.teacherPrinterSettings?.clear?.();document.querySelectorAll('#teacher-content dialog[open]').forEach(dialog=>dialog.close())}
}
async function login(){
  const button=$('login-form').querySelector('button');button.disabled=true;showError('login-error','');
  try{await api('/api/auth/login',{code:$('admin-token').value.trim()});sessionChecked=true;setTeacherSession(true)}
  catch(error){setTeacherSession(false);showError('login-error',error.message)}
  finally{$('admin-token').value='';button.disabled=false}
}
$('login-form').addEventListener('submit',event=>{event.preventDefault();login()});
$('teacher-logout').addEventListener('click',async()=>{try{await api('/api/auth/logout',{});setTeacherSession(false);showError('login-error','');$('admin-token').focus()}catch(error){showError('admin-error','Du er ikke logget ud endnu. '+error.message)}});
const releasingPrinters=new Set();
async function adminAction(path,body){
  const releasing=path==='/api/printers/release';
  if(releasing&&releasingPrinters.has(body.id))return false;
  if(releasing){releasingPrinters.add(body.id);$('release-sample-status').textContent='Frigiver den kontrollerede plade…';}
  showError('admin-error','');
  try{
    const result=await api(path,body,true);
    if(releasing)$('release-sample-status').textContent='Pladen er frigivet.';
    try{state=await api('/api/state');renderTeacher()}catch{showError('admin-error','Ændringen er gemt. Venter på opdateret status.')}return true;
  }catch(error){
    if(releasing)$('release-sample-status').textContent='Pladen blev ikke frigivet.';
    showError('admin-error',error.message);$('admin-error').scrollIntoView({behavior:'smooth',block:'nearest'});return false;
  }finally{if(releasing)releasingPrinters.delete(body.id);}
}
$('pause-queue').addEventListener('click',()=>adminAction('/api/queue/pause',{paused:!state.paused}));
$('teacher-printers').addEventListener('click',event=>{const resolve=event.target.closest('[data-resolve]');if(resolve){resolveJob(resolve);return}const release=event.target.closest('[data-release]'),finish=event.target.closest('[data-finish]');if(release)adminAction('/api/printers/release',{id:release.dataset.release});if(finish)adminAction('/api/demo/finish',{id:finish.dataset.finish})});
['attention-jobs','teacher-queue'].forEach(id=>$(id).addEventListener('click',event=>{const button=event.target.closest('[data-resolve]');if(button)resolveJob(button)}));
$('library-refresh').addEventListener('click',()=>{libraryPolicyDirty=false;libraryPolicyRequest=null;refreshLibrary()});
$('library-search').addEventListener('input',()=>{clearTimeout(librarySearchTimer);libraryPage=1;libraryRead++;librarySearchTimer=setTimeout(refreshLibrary,250)});
$('library-prev').addEventListener('click',()=>{if(libraryPage>1){libraryPage--;refreshLibrary()}});
$('library-next').addEventListener('click',()=>{if(libraryPageData?.has_more){libraryPage++;refreshLibrary()}});
$('library-policy-form').addEventListener('input',()=>{libraryPolicyDirty=true});
$('library-policy-form').addEventListener('submit',saveLibraryPolicy);
$('library-pending').addEventListener('click',event=>{const button=event.target.closest('[data-library-delete-retry]');if(button)deleteLibrary(button.dataset.libraryDeleteRetry,'','',true)});
$('library-list').addEventListener('click',event=>{const deletion=event.target.closest('[data-library-delete]');if(deletion){event.preventDefault();deleteLibrary(deletion.dataset.libraryDelete,deletion.dataset.libraryName,deletion.dataset.libraryGeneration);return}const download=event.target.closest('[data-library-download]');if(download){event.preventDefault();downloadLibrary(download.dataset.libraryDownload,download.dataset.libraryName);return}const button=event.target.closest('[data-library-reprint]');if(button){event.preventDefault();reprintLibrary(button.dataset.libraryReprint,button.dataset.libraryName,button.dataset.libraryColour,button.dataset.libraryQuality);return}const diagnostic=event.target.closest('[data-diagnostic]');if(diagnostic){event.preventDefault();showDiagnostic(diagnostic.dataset.diagnostic)}});
$('attention-jobs').addEventListener('click',event=>{const diagnostic=event.target.closest('[data-diagnostic]');if(diagnostic)showDiagnostic(diagnostic.dataset.diagnostic)});
$('attention-jobs').addEventListener('click',event=>{const button=event.target.closest('[data-library-reprint]');if(button)reprintLibrary(button.dataset.libraryReprint,button.dataset.libraryName,button.dataset.libraryColour,button.dataset.libraryQuality)});
if(teacher){$('student-view').hidden=true;$('teacher-view').hidden=false;$('student-link').classList.remove('current');$('teacher-link').classList.add('current');$('student-link').removeAttribute('aria-current');$('teacher-link').setAttribute('aria-current','page');document.title='Lærerside · Makerværkstedet'}
renderColours();poll();

