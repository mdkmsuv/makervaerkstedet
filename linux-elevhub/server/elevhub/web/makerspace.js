'use strict';
const $ = id => document.getElementById(id);
let state = {machines:[], workflows:[], jobs:[], observed_machines:[], templates:{}};
let busy = false, generation = 0, pending = null, activeController = null, initialized = false;
let pollTimer = null, ageTimer = null, pollInFlight = false, editorDirty = false, pageGone = false, loadSequence = 0;
let machineEditVersion = null, workflowEditVersion = null;
let uncertainCommand = null;
const ACTIONS = ['machine.create','machine.update','workflow.create','workflow.edit','workflow.publish','workflow.rollback','job.create','job.advance'];
function text(value){ return value === null || value === undefined ? '' : String(value); }
function setError(message){ $('error').textContent = message || ''; }
function empty(parent, message){ parent.replaceChildren(); const p=document.createElement('p'); p.className='empty'; p.textContent=message; parent.append(p); }
function button(label, action, value){ const b=document.createElement('button'); b.type='button'; b.className='sekundaer'; b.textContent=label; if(action)b.dataset.action=action; if(value)b.dataset.value=value; return b; }
function api(path, data){
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),8000); activeController=controller; const g=generation;
  const request=fetch(path,{cache:'no-store',redirect:'error',signal:controller.signal,...(data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})})
    .then(async response=>{let payload;try{payload=await response.json()}catch{const e=new Error('Serverens svar kunne ikke læses.');if(!response.ok)e.status=response.status;throw e} if(!response.ok){const e=new Error(payload.error||'Serveren kunne ikke gennemføre handlingen.');e.status=response.status;throw e}return {payload,g};})
    .catch(e=>{if(e.name==='AbortError')throw new Error('Serveren svarede ikke i tide. Kontrollér status, før du prøver igen.');throw e;}).finally(()=>{clearTimeout(timer);if(activeController===controller)activeController=null});
  pending=request; return request.finally(()=>{if(pending===request)pending=null});
}
function editorIsActive(){const active=document.activeElement;return editorDirty||!!active?.closest('form');}
async function load(options={}){
  const poll=options.poll===true;
  if(pageGone || (poll && (busy || editorIsActive() || pollInFlight)))return false;
  if(poll)pollInFlight=true; const sequence=++loadSequence;
  try { const result=await api('/api/makerspace/state'); if(result.g!==generation||sequence!==loadSequence)return false; if(poll && (busy || editorIsActive()))return false; state=result.payload||state; render(); if(!initialized){resetMachine();resetWorkflow();initialized=true} $('status').textContent='Overblikket er opdateret kl. '+new Date().toLocaleTimeString('da-DK'); return true; }
  catch(e){if(sequence===loadSequence && !pageGone){$('status').textContent='Forbindelsen til serveren er afbrudt. Det viste overblik er sidst kendt; brug Opdatér overblik for at prøve igen.';if(e.status===401){generation++;location.href='/kontrolpanel';}}throw e;}
  finally { if(poll)pollInFlight=false; }
}
function schedulePoll(){clearTimeout(pollTimer);if(pageGone||document.hidden)return;pollTimer=setTimeout(async()=>{try{await load({poll:true})}catch{}finally{schedulePoll()}},15000);pollTimer.unref?.()}
function workflowName(id){const w=state.workflows.find(item=>item.id===id);return w?w.name:'Ingen workflow';}
const KIND_LABELS={manual:'Manuel station','3d_print':'3D-print',laser:'Laser'}, ROLE_LABELS={operator:'Deltager',teacher:'Lærer'}, JOB_LABELS={active:'I gang',queued:'I kø',done:'Færdig'};
function renderSelects(){
  const machineWorkflow=$('machine-workflow'), jobMachine=$('job-machine'); const currentWorkflow=machineWorkflow.value, currentMachine=jobMachine.value;
  machineWorkflow.replaceChildren(new Option('Ingen workflow','')); state.workflows.filter(w=>w.kind==='manual').forEach(w=>machineWorkflow.add(new Option(w.name,w.id))); machineWorkflow.value=currentWorkflow;
  jobMachine.replaceChildren(new Option('Vælg station','')); state.machines.filter(m=>m.enabled && state.workflows.some(w=>w.id===m.workflow_id&&w.active_revision)).forEach(m=>jobMachine.add(new Option(m.name,m.id))); jobMachine.value=currentMachine;
}
function renderMachines(){const root=$('machines');root.replaceChildren();if(!state.machines.length){empty(root,'Ingen manuelle stationer er registreret endnu.');return}state.machines.forEach(m=>{const card=document.createElement('article');card.className='item '+(m.enabled?'active':'');const h=document.createElement('h3');h.textContent=m.name;card.append(h);const p=document.createElement('p');p.textContent=m.location+' · '+(m.enabled?'Aktiv':'Inaktiv')+' · '+workflowName(m.workflow_id);card.append(p);const actions=document.createElement('div');actions.className='item-actions';const edit=button('Redigér','machine-edit',m.id);actions.append(edit);card.append(actions);root.append(card)})}
function workflowImpact(id){const manual=state.machines.filter(m=>m.workflow_id===id).length;const printers=state.printer_administration?.printers||[];const lasers=state.laser_administration?.stations||[];const knownPrinters=state.printer_administration?.status==='ok'&&Array.isArray(state.printer_administration?.printers),knownLasers=state.laser_administration?.status==='ok'&&Array.isArray(state.laser_administration?.stations);const printerCount=printers.filter(m=>m.workflow?.id===id).length;const laserCount=lasers.filter(m=>m.workflow?.id===id).length;const manualJobs=state.jobs.filter(j=>j.workflow_id===id&&j.status!=='done').length;const printJobs=(state.printer_administration?.workflow_jobs||[]).filter(j=>j.workflow_id===id&&j.status!=='done').length;const laserJobs=(state.laser_administration?.jobs||[]).filter(j=>j.workflow_id===id&&j.status!=='done').length;return {manual,printers:printerCount,lasers:laserCount,jobs:manualJobs+printJobs+laserJobs,unknownPrinters:!knownPrinters,unknownLasers:!knownLasers};}
function renderWorkflows(){const root=$('workflows');root.replaceChildren();if(!state.workflows.length){empty(root,'Ingen workflows er oprettet endnu.');return}state.workflows.forEach(w=>{const card=document.createElement('article');card.className='item '+(w.active_revision?'active':'');const h=document.createElement('h3');h.textContent=w.name;card.append(h);const p=document.createElement('p');p.textContent=(KIND_LABELS[w.kind]||'Ukendt type')+' · '+(w.active_revision?'Aktiv revision '+w.active_revision:'Ingen aktiv revision');card.append(p);const draftSteps=Array.isArray(w.draft?.steps)?w.draft.steps:[], activeSteps=Array.isArray(w.active_steps)?w.active_steps:[];const steps=document.createElement('p');steps.textContent='Kladdens trin: '+draftSteps.length+' · Aktiv revision: '+(w.active_revision?('revision '+w.active_revision+' · '+activeSteps.length+' trin'):'ingen');card.append(steps);if(w.has_unpublished_changes){const changed=document.createElement('p');changed.textContent='Ikkeaktiverede ændringer i kladden.';card.append(changed)}if(w.active_revision){const impact=document.createElement('p');const binding=workflowImpact(w.id);impact.textContent='Ved aktivér: '+binding.manual+' manuelle stationer får den nye revision til nye opgaver. '+(binding.printers?' '+binding.printers+' printerbinding(er) skal tildeles den nye revision i printersektionen. ':'')+(binding.lasers?' '+binding.lasers+' laserbinding(er) skal tildeles den nye revision i lasersektionen. ':'')+binding.jobs+' aktive job beholder deres fastlåste revision.'+((binding.unknownPrinters||binding.unknownLasers)?' Andre integrationer har ukendt status.':'');card.append(impact)}const actions=document.createElement('div');actions.className='item-actions';actions.append(button('Redigér','workflow-edit',w.id),button('Kopiér','workflow-copy',w.id),button('Aktivér','workflow-publish',w.id));(w.revisions||[]).forEach(revision=>{const rollback=button('Tilbagefør til revision '+revision,'workflow-rollback',w.id);rollback.dataset.revision=revision;actions.append(rollback)});card.append(actions);root.append(card)})}
function renderJobs(){const root=$('jobs');root.replaceChildren();if(!state.jobs.length){empty(root,'Ingen manuelle opgaver er startet endnu.');return}state.jobs.forEach(j=>{const card=document.createElement('article');card.className='item';const h=document.createElement('h3');h.textContent=j.title;card.append(h);const p=document.createElement('p');const machine=state.machines.find(m=>m.id===j.machine_id);const steps=Array.isArray(j.definition?.steps)?j.definition.steps:[];const step=steps[j.step_index];p.textContent=(machine?machine.name:'Ukendt station')+' · '+(JOB_LABELS[j.status]||'Status ukendt')+' · workflowrevision '+text(j.workflow_revision);card.append(p);if(step){const current=document.createElement('p');current.textContent='Aktuelt trin: '+text(step.label)+' · '+(ROLE_LABELS[step.role]||'Ukendt ansvar');card.append(current)}const actions=document.createElement('div');actions.className='item-actions';if(j.status!=='done'&&step){const next=button('Bekræft næste trin','job-advance',j.id);actions.append(next)}card.append(actions);root.append(card)})}
function observationTime(value){const date=typeof value==='number'&&Number.isFinite(value)?new Date(value*1000):typeof value==='string'?new Date(value):null;if(!date||Number.isNaN(date.valueOf())||date.getTime()>Date.now())return null;return date}
function observationAgeText(m){const date=observationTime(m.observed_at),age=date?Date.now()-date.getTime():null,stale=age===null||age>120000;return {text:'Observeret: '+(date?date.toLocaleString('da-DK'):'ukendt')+' · '+(age===null?'Alder ukendt':age<60000?'lige nu':Math.floor(age/60000)+' min siden')+(stale?' · Gammel eller ukendt status':''),stale}}
function updateObservationAges(){const root=$('observed');(state.observed_machines||[]).forEach((m,index)=>{const observed=root.children[index]?.children?.[2];if(!observed)return;const age=observationAgeText(m);observed.textContent=age.text;observed.className=age.stale?'stale-observation':''})}
function scheduleAgeRefresh(){clearTimeout(ageTimer);if(pageGone)return;ageTimer=setTimeout(()=>{updateObservationAges();scheduleAgeRefresh()},30000);ageTimer.unref?.()}
function renderObserved(){const root=$('observed');clearTimeout(ageTimer);root.replaceChildren();if(!state.observed_machines?.length){empty(root,'Ingen adapterstatus er modtaget.');return}state.observed_machines.forEach(m=>{const card=document.createElement('article');card.className='item';const h=document.createElement('h3');h.textContent=m.name||m.id||'Ukendt maskine';card.append(h);const p=document.createElement('p');p.textContent=m.supported?'Kendt maskine · ':'Ukendt maskine · ';p.append(document.createTextNode(text(m.status === 'unknown' ? 'Fysisk status ukendt' : (m.online ? 'Online' : 'Offline'))+' · '+text(m.status||'Status ikke oplyst')));card.append(p);const observed=document.createElement('p');const age=observationAgeText(m);observed.textContent=age.text;observed.className=age.stale?'stale-observation':'';card.append(observed);root.append(card)});scheduleAgeRefresh()}
function render(){renderSelects();renderMachines();renderWorkflows();renderJobs();renderObserved();}
function resetMachine(){ machineEditVersion=null;$('machine-id').value='';$('machine-name').value='';$('machine-location').value='';$('machine-enabled').checked=false;$('machine-workflow').value='';editorDirty=false; }
function editMachine(id){const m=state.machines.find(x=>x.id===id);if(!m)return;machineEditVersion=m.version;$('machine-id').value=m.id;$('machine-name').value=m.name;$('machine-location').value=m.location;$('machine-enabled').checked=!!m.enabled;$('machine-workflow').value=m.workflow_id||'';editorDirty=false;$('machine-name').focus()}
const ROLES=[['operator','Deltager'],['teacher','Lærer']], STEP_ACTIONS=[['submit','Indsend'],['prepare','Forbered'],['approve','Godkend'],['produce','Producer'],['collect','Aflever eller afhent'],['clear','Ryd arbejdsplads']];
function selectField(field, choices, value){const select=document.createElement('select');select.dataset.field=field;select.required=true;choices.forEach(([v,label])=>select.add(new Option(label,v)));select.value=text(value);return select}
function stepRow(step={}){
  const row=document.createElement('div');row.className='step-row';
  row.dataset.id=step.id||'step-'+crypto.randomUUID().replaceAll('-','');
  const label=document.createElement('input');label.dataset.field='label';label.placeholder='Trinnets navn';
  label.ariaLabel='Trinnets navn';label.maxLength=100;label.value=text(step.label);label.required=true;
  const role=selectField('role',ROLES,step.role||'operator');role.ariaLabel='Ansvar for trinnet';
  const action=selectField('action',STEP_ACTIONS,step.action||'prepare');action.ariaLabel='Trinnets handling';
  row.append(label,role,action);
  const controls=document.createElement('div');controls.className='step-controls';
  const up=button('Op'),down=button('Ned'),remove=button('Fjern');
  const changed=()=>{editorDirty=true;$('workflow-validation').textContent=''};
  up.ariaLabel='Flyt trinnet op';down.ariaLabel='Flyt trinnet ned';remove.ariaLabel='Fjern trinnet';
  up.addEventListener('click',()=>{changed();if(row.previousElementSibling)row.parentNode.insertBefore(row,row.previousElementSibling)});
  down.addEventListener('click',()=>{changed();if(row.nextElementSibling)row.parentNode.insertBefore(row.nextElementSibling,row)});
  remove.addEventListener('click',()=>{changed();row.remove()});controls.append(up,down,remove);row.append(controls);
  return row;
}
function readSteps(){return Array.from($('workflow-steps').children).map((row,index)=>({id:row.dataset.id||'step-'+(index+1),label:row.querySelector('[data-field="label"]').value,role:row.querySelector('[data-field="role"]').value,action:row.querySelector('[data-field="action"]').value}))}
function templateSteps(kind){const definition=state.templates?.[kind];return Array.isArray(definition?.steps)?definition.steps:[]}
function resetWorkflow(){ $('workflow-validation').textContent='';workflowEditVersion=null;$('workflow-id').value='';$('workflow-name').value='';$('workflow-kind').value='manual';$('workflow-steps').replaceChildren(...templateSteps('manual').map(stepRow));if(!$('workflow-steps').children.length)$('workflow-steps').append(stepRow());editorDirty=false; }
function fillWorkflow(id,copy=false){$('workflow-validation').textContent='';const w=state.workflows.find(x=>x.id===id);if(!w)return;workflowEditVersion=copy?null:w.version;$('workflow-id').value=copy?'':w.id;$('workflow-name').value=copy?w.name+' (kopi)':w.name;$('workflow-kind').value=w.kind;$('workflow-steps').replaceChildren(...(w.draft?.steps||[]).map(stepRow));if(!w.draft?.steps?.length)$('workflow-steps').append(...templateSteps(w.kind).map(stepRow));editorDirty=false;$('workflow-name').focus()}
async function command(action,params,expectedVersion){
  if(busy)return false;
  const key=JSON.stringify({action,params,expected_version:expectedVersion});
  if(uncertainCommand && uncertainCommand.key!==key){setError('Afklar den sidste ændring, før du foretager en ny.');return false;}
  const payload=uncertainCommand?.payload || {action,params,expected_version:expectedVersion,request_id:crypto.randomUUID().replaceAll('-','')};
  busy=true;setError('');document.querySelectorAll('button').forEach(b=>{if(!b.closest('[aria-labelledby="printers-title"], [aria-labelledby="print-workflows-title"], [aria-labelledby="lasers-title"]'))b.disabled=true});
  try{
    const response=await api('/api/makerspace/commands',payload);
    if(response.g!==generation)return false;
    if(response.payload?.ok!==true || response.payload?.action!==payload.action || typeof response.payload?.result?.id!=='string')throw new Error('Ændringen kunne ikke bekræftes.');
    uncertainCommand=null;$('retry-command').hidden=true;
    generation++;
    try{await load()}catch(e){setError('Ændringen er gemt, men overblikket kunne ikke opdateres. Genindlæs siden.')}
    return true;
  }catch(e){
    if(!e.status||e.status>=500){uncertainCommand={key,payload};$('retry-command').hidden=false;setError('Resultatet er ukendt. Brug Afklar sidste ændring; samme request-ID forhindrer dubletter.');}
    else{
      uncertainCommand=null;$('retry-command').hidden=true;
      if(e.status===401){generation++;location.href='/kontrolpanel';return false;}
      if(e.status===409){setError(e.message||'Data er ændret. Formularen er bevaret; vælg Redigér igen for at hente den aktuelle version.');generation++;try{await load()}catch(loadError){setError(loadError.message)}}
      else setError(e.message);
    }
    return false;
  }finally{busy=false;document.querySelectorAll('button').forEach(b=>{if(!b.closest('[aria-labelledby="printers-title"], [aria-labelledby="print-workflows-title"], [aria-labelledby="lasers-title"]'))b.disabled=false})}
}
$('retry-command').addEventListener('click',async()=>{
  if(!uncertainCommand)return;
  const p=uncertainCommand.payload;
  if(await command(p.action,p.params,p.expected_version))clearAppliedForm(p.action,p.params);
});
function machineParams(){const value={name:$('machine-name').value,location:$('machine-location').value,enabled:$('machine-enabled').checked,workflow_id:$('machine-workflow').value||null};if($('machine-id').value)value.id=$('machine-id').value;return value;}
function workflowParams(){const value={definition:{name:$('workflow-name').value,kind:$('workflow-kind').value,steps:readSteps()}};if($('workflow-id').value)value.id=$('workflow-id').value;return value;}
function clearAppliedForm(action,params){
  // Clear only the submitted form, and only if it has not been edited since.
  if(['machine.create','machine.update'].includes(action) && JSON.stringify(machineParams())===JSON.stringify(params))resetMachine();
  if(['workflow.create','workflow.edit'].includes(action) && JSON.stringify(workflowParams())===JSON.stringify(params))resetWorkflow();
  if(action==='job.create' && $('job-machine').value===params.machine_id && $('job-title').value===params.title)$('job-title').value='';
}
async function machineSubmit(e){e.preventDefault();const params=machineParams(),action=params.id?'machine.update':'machine.create';if(await command(action,params,params.id?machineEditVersion:0))clearAppliedForm(action,params);}
async function workflowSubmit(e){e.preventDefault();const params=workflowParams(),action=params.id?'workflow.edit':'workflow.create';if(await command(action,params,params.id?workflowEditVersion:0))clearAppliedForm(action,params);}
async function jobSubmit(e){e.preventDefault();const params={machine_id:$('job-machine').value,title:$('job-title').value};if(await command('job.create',params,0))clearAppliedForm('job.create',params);}
async function validateWorkflow(){
  if(busy)return;
  const definition=workflowParams().definition, fingerprint=JSON.stringify(definition);
  const control=$('workflow-validate');control.disabled=true;$('workflow-validation').textContent='Kontrollerer kladden …';
  try{const result=await api('/api/makerspace/workflows/validate',{definition});if(result.g!==generation||fingerprint!==JSON.stringify(workflowParams().definition))return;
    $('workflow-validation').textContent=result.payload?.valid===true?'Kladden er gyldig. Gem den, før den aktiveres.':'Kladden kunne ikke valideres.';
  }catch(e){if(e.status===401){generation++;location.href='/kontrolpanel';return;}if(fingerprint===JSON.stringify(workflowParams().definition))$('workflow-validation').textContent=e.status?e.message:'Kladden kunne ikke kontrolleres. Kontrollér forbindelsen, og prøv igen.';}
  finally{control.disabled=busy;}
}
$('workflow-validate').addEventListener('click',validateWorkflow);
$('state-refresh').addEventListener('click',async()=>{if(busy)return;const control=$('state-refresh');control.disabled=true;try{await load()}catch{}finally{control.disabled=busy;}});
document.addEventListener('click',e=>{const b=e.target.closest('[data-action]');if(!b)return;const id=b.dataset.value;const action=b.dataset.action;if(action==='machine-edit')editMachine(id);if(action==='workflow-edit')fillWorkflow(id);if(action==='workflow-copy')fillWorkflow(id,true);if(action==='workflow-publish')command('workflow.publish',{id},state.workflows.find(x=>x.id===id)?.version);if(action==='workflow-rollback'&&/^\d+$/.test(b.dataset.revision||''))command('workflow.rollback',{id,revision:Number(b.dataset.revision)},state.workflows.find(x=>x.id===id)?.version);if(action==='job-advance'){const job=state.jobs.find(x=>x.id===id);const steps=Array.isArray(job?.definition?.steps)?job.definition.steps:[];const step=steps[job?.step_index];if(job&&step)command('job.advance',{id,step_id:step.id,confirmed:true},job.version)}});
$('machine-form').addEventListener('submit',machineSubmit);$('machine-reset').addEventListener('click',resetMachine);$('machine-cancel').addEventListener('click',resetMachine);$('workflow-form').addEventListener('submit',workflowSubmit);$('workflow-reset').addEventListener('click',resetWorkflow);$('workflow-cancel').addEventListener('click',resetWorkflow);$('workflow-kind').addEventListener('change',()=>{editorDirty=true;$('workflow-validation').textContent='';if(!$('workflow-id').value)$('workflow-steps').replaceChildren(...templateSteps($('workflow-kind').value).map(stepRow))});$('add-step').addEventListener('click',()=>{editorDirty=true;$('workflow-validation').textContent='';$('workflow-steps').append(stepRow())});$('job-form').addEventListener('submit',jobSubmit);document.querySelectorAll('#machine-form,#workflow-form').forEach(form=>form.addEventListener('input',()=>{editorDirty=true;$('workflow-validation').textContent=''}));window.addEventListener('pagehide',()=>{pageGone=true;generation++;clearTimeout(pollTimer);clearTimeout(ageTimer);if(activeController)activeController.abort()});window.addEventListener('pageshow',()=>{pageGone=false;scheduleAgeRefresh();schedulePoll()});document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(pollTimer)}else{schedulePoll()} });
load().then(()=>schedulePoll()).catch(e=>{if(e.status===401)location.href='/kontrolpanel';else{$('status').textContent='Overblikket kunne ikke hentes.';setError(e.message);schedulePoll()}});
