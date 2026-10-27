const $=id=>document.getElementById(id);
const LABELS={received:'Modtaget',ready:'Gennemset',done:'Færdig'};
let jobs=[],selectedId=null,busy=false,loggedIn=false,completionConfirmation=null,completing=false;
const node=(tag,text,className)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n;};
async function api(path,body){
  const response=await fetch('/api/laser/'+path,{...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(10000)});
  const data=await response.json();
  if(!response.ok){if(response.status===401&&path!=='login')showLogin();throw Error(data.error||'Forbindelsen til værkstedet blev afbrudt.');}
  return data;
}
function error(e){$('inbox-error').hidden=false;$('inbox-error').textContent=e.name==='TimeoutError'||e instanceof TypeError?'Ingen forbindelse til værkstedet. Prøv at opdatere køen.':e.message;}
function showLogin(){loggedIn=false;jobs=[];selectedId=null;$('job-list').replaceChildren();$('job-detail').replaceChildren();$('inbox-panel').hidden=true;$('login-panel').hidden=false;$('logout').hidden=true;}
async function refresh(){
  if(busy||!loggedIn||completionConfirmation)return;busy=true;
  try{
    const result=await api('jobs');jobs=result.jobs;$('inbox-error').hidden=true;
    const selectedClass=$('class-filter').value;const options=[new Option('Alle klasser','all'),...[...new Set(jobs.map(j=>j.classroom))].sort((a,b)=>a.localeCompare(b,'da')).map(c=>new Option(c,c))];
    $('class-filter').replaceChildren(...options);if(options.some(o=>o.value===selectedClass))$('class-filter').value=selectedClass;
    render();
  }catch(e){error(e);}finally{busy=false;}
}
function filtered(){const q=$('search').value.toLocaleLowerCase('da'),c=$('class-filter').value,s=$('status-filter').value;return jobs.filter(j=>(c==='all'||j.classroom===c)&&(s==='all'||s==='open'&&j.status!=='done'||j.status===s)&&`${j.student} ${j.project}`.toLocaleLowerCase('da').includes(q));}
function closeCompletionDialog(){if(completing)return;completionConfirmation=null;busy=false;$('done-confirm').disabled=false;$('done-cancel').disabled=false;$('done-confirm').textContent='Markér færdig';$('done-dialog-error').hidden=true;}
function openCompletionDialog(job){
  completionConfirmation={id:job.id,project:job.project,student:job.student,classroom:job.classroom};
  $('done-dialog-target').textContent=`${job.project} · ${job.student} · ${job.classroom}`;
  $('done-dialog-error').hidden=true;$('done-confirm').disabled=false;$('done-cancel').disabled=false;$('done-confirm').textContent='Markér færdig';busy=true;completing=false;
  $('done-dialog').showModal();$('done-cancel').focus();
}
async function completeConfirmed(){
  const confirmation=completionConfirmation;if(!confirmation||completing)return;completing=true;busy=true;
  $('done-confirm').disabled=true;$('done-cancel').disabled=true;$('done-confirm').textContent='Markerer…';
  try{
    await api(`jobs/${confirmation.id}/status`,{status:'done'});
    completionConfirmation=null;completing=false;busy=false;
    $('done-dialog').close('confirm');
    await refresh();
  }catch(e){
    if(!loggedIn){completionConfirmation=null;completing=false;busy=false;$('done-dialog').close('login-expired');return;}
    completing=false;$('done-dialog-error').hidden=false;$('done-dialog-error').textContent=e.message||'Afleveringen kunne ikke markeres som færdig.';$('done-confirm').disabled=false;$('done-cancel').disabled=false;$('done-confirm').textContent='Prøv igen';
  }
}
function render(){
  const list=filtered();$('job-count').textContent=`${list.length} ${list.length===1?'aflevering':'afleveringer'}`;$('queue-empty').hidden=list.length>0;
  if(!list.some(j=>j.id===selectedId))selectedId=list[0]?.id||null;
  $('job-list').replaceChildren();
  for(const job of list){
    const button=node('button',undefined,'job-card'+(selectedId===job.id?' active':''));button.dataset.id=job.id;button.setAttribute('aria-pressed',selectedId===job.id);
    button.append(node('strong',job.student+' · '+job.classroom),node('span',job.project,'project'));
    const meta=node('div',undefined,'job-meta');meta.append(node('span',new Date(job.created_at).toLocaleString('da-DK',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})),node('span',LABELS[job.status],'status-label'));button.append(meta);button.onclick=()=>{selectedId=job.id;render();};$('job-list').append(button);
  }
  detail();
}
function detail(){
  const panel=$('job-detail');panel.replaceChildren();const job=jobs.find(j=>j.id===selectedId);
  if(!job){panel.append(node('div','Vælg en aflevering for at se tegningen.','detail-empty'));return;}
  const heading=node('div',undefined,'detail-heading'),names=node('div');names.append(node('h3',job.project),node('p',job.student+' · '+job.classroom));heading.append(names,node('span',LABELS[job.status],'status-label'));panel.append(heading);
  const payloadAvailable=job.payload_available!==false&&job.payload_available!==0&&job.status!=='done';
  if(payloadAvailable){const imageBox=node('div',undefined,'preview-image'),img=node('img');img.src=`/api/laser/jobs/${job.id}/svg?preview=1&methods=${encodeURIComponent(JSON.stringify(job.imageModes||{}))}`;img.alt=`Tegningen ${job.project} fra ${job.student}`;imageBox.append(img);panel.append(imageBox);}
  else panel.append(node('p','Filen er slettet fra afleveringskøen. Historikken er bevaret, men der er ikke længere en forhåndsvisning eller download.','payload-gone'));
  const ops={...job.details.operations};for(const image of job.details.images||[]){const mode=job.imageModes?.[image.id];if(mode&&image.operation!=='cut'){ops[image.operation]--;ops[mode==='raster'?'engrave-fill':'engrave-line']++;}}
  panel.append(node('p',`${job.details.width} × ${job.details.height} mm · ${ops.cut} skæredele · ${ops['engrave-fill']} fladegraveringer · ${ops['engrave-line']} streggraveringer`,'process-summary'));
  const images=job.details.images||[],engraved=images.filter(i=>i.operation!=='cut'),modes=job.imageModes||{},missing=engraved.some(i=>!modes[i.id]);
  if(payloadAvailable&&images.length){
    const section=node('section',undefined,'image-methods');section.append(node('h3','Vælg metode til billederne'),node('p','Raster bruger billedet i gråtoner. Vektor følger de konturer, eleven har lavet med tracing.','muted'));
    for(const image of images){
      const row=node('fieldset',undefined,'image-method');row.dataset.imageId=image.id;row.append(node('legend',image.name));
      if(image.operation==='cut'){row.append(node('p','Skæring · følger de tracede konturer.','muted'));section.append(row);continue;}
      for(const [mode,title,description] of [['raster','Rastergravering','Billedets flader og nuancer'],['vector','Vektorgravering','Følg konturerne som streger']]){
        const label=node('label',undefined,'method-option'),input=node('input');input.type='radio';input.name='method-'+image.id;input.value=mode;input.checked=modes[image.id]===mode;input.disabled=mode==='vector'&&!image.hasTrace;
        const text=node('span');text.append(node('strong',title),node('small',input.disabled?'Eleven har ikke lavet konturer':description));label.append(input,text);row.append(label);
        input.onchange=async()=>{
          if(!input.checked)return;if(busy){render();return;}busy=true;$('download-svg').disabled=true;section.querySelectorAll('input').forEach(n=>n.disabled=true);
          try{const result=await api(`jobs/${job.id}/image-mode`,{imageId:image.id,mode});job.imageModes=result.imageModes;$('inbox-error').hidden=true;render();}catch(e){error(e);if(loggedIn)render();}finally{busy=false;}
        };
      }
      if(!modes[image.id])row.append(node('p','Vælg en metode, før du henter filen til Beam Studio.','method-pending'));
      section.append(row);
    }
    panel.append(section);
  }
  const actions=node('div',undefined,'detail-actions'),download=node('button','Hent SVG / gem på USB ↓','primary');download.id='download-svg';download.disabled=missing;download.title=missing?'Vælg graveringsmetode for alle billeder først.':'Hent SVG med de valgte billedmetoder';
  async function downloadFile(source=false){
    try{const r=await fetch(`/api/laser/jobs/${job.id}/svg${source?'?source=1':''}`);if(!r.ok){if(r.status===401)showLogin();const response=await r.json();throw Error(response.error||'Filen kunne ikke hentes.');}const url=URL.createObjectURL(await r.blob()),a=node('a');a.href=url;a.download=`${source?'Elevtegning - ':''}${job.classroom} - ${job.student} - ${job.project}.svg`.replace(/[<>:"/\\|?*\x00-\x1f]/g,'');a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){error(e);}
  }
  if(payloadAvailable){download.onclick=()=>downloadFile();actions.append(download);}
  if(payloadAvailable&&images.length){const original=node('button','Hent elevens redigerbare SVG','secondary');original.id='download-source';original.onclick=()=>downloadFile(true);actions.append(original);}
  for(const [status,label] of [['ready','Markér gennemset'],['done','Markér færdig'],['received','Tilbage til modtaget']]){
    if(job.status==='done')continue;
    if(job.status===status)continue;
    if(job.status==='received'&&status==='done')continue;
    const button=node('button',label,'secondary');button.dataset.status=status;
    if(status==='done')button.onclick=()=>openCompletionDialog(job);
    else button.onclick=async()=>{button.disabled=true;try{await api(`jobs/${job.id}/status`,{status});await refresh();}catch(e){error(e);button.disabled=false;}};
    actions.append(button);
  }
  panel.append(actions,node('p','Aflevering '+job.id.slice(0,8).toUpperCase(),'receipt'));
}
$('login-form').onsubmit=async e=>{
  e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;$('login-error').textContent='';
  try{await api('login',{code:$('teacher-code').value});$('teacher-code').value='';loggedIn=true;$('login-panel').hidden=true;$('inbox-panel').hidden=false;$('logout').hidden=false;await refresh();}catch(e){$('login-error').textContent=e.message;}finally{button.disabled=false;}
};
$('logout').onclick=async()=>{try{await api('logout',{});showLogin();}catch(e){error(e);}};
$('refresh').onclick=refresh;for(const id of ['class-filter','status-filter'])$(id).onchange=render;$('search').oninput=render;
const doneDialog=$('done-dialog');$('done-form').onsubmit=e=>{if(e.submitter?.value==='confirm'){e.preventDefault();completeConfirmed();}else if(completing){e.preventDefault();}else closeCompletionDialog();};doneDialog.addEventListener('cancel',e=>{if(completing)e.preventDefault();else closeCompletionDialog();});doneDialog.addEventListener('close',()=>{if(completionConfirmation&&!completing)closeCompletionDialog();});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});setInterval(()=>{if(!document.hidden)refresh();},15000);
(async()=>{try{const s=await api('health');if(s.surface==='teacher'&&s.teacher){loggedIn=true;$('login-panel').hidden=true;$('inbox-panel').hidden=false;$('logout').hidden=false;await refresh();}}catch{}})();
