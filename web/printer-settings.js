(function(){
  'use strict';

  const $=id=>document.getElementById(id);
  const section=$('printer-settings-section');
  if(!section)return;
  const form=$('printer-settings-form');
  const list=$('printer-settings-list');
  const errorBox=$('printer-settings-error');
  const formError=$('printer-settings-form-error');
  const status=$('printer-settings-status');
  const modelSelect=$('printer-settings-model');
  const enabled=$('printer-settings-enabled');
  const access=$('printer-settings-access');
  let printers=[];
  let models=[];
  let editingId='';
  let busy=false;
  let loaded=false;
  let sessionGeneration=0;
  let certificatePaused=false;
  let certificateOperations=[];
  const certificateRequests=new Map();
  const certificateBusy=new Set();

  function message(node,text){node.textContent=text||'';node.hidden=!text}
  function request(path,body){
    if(typeof window.teacherPrinterApi!=='function')throw new Error('Lærersessionen er ikke klar.');
    return window.teacherPrinterApi(path,body);
  }
  function clearList(){while(list.firstChild)list.removeChild(list.firstChild)}
  function text(tag,value,className){
    const node=document.createElement(tag);node.textContent=String(value??'');
    if(className)node.className=className;
    return node;
  }
  function modelName(model){return model==='H2D'?'H2D · ikke i børnekøen':model}
  function setAddAvailability(available){$('printer-settings-add').disabled=!available}
  function updateModelHelp(){
    const h2d=modelSelect.value==='H2D';
    enabled.disabled=h2d;
    if(h2d)enabled.checked=false;
    $('printer-settings-model-help').textContent=h2d?'H2D kan sættes op til lærerprint, men kan ikke medtages i børnekøen.':'';
  }
  function renderModels(selected){
    while(modelSelect.firstChild)modelSelect.removeChild(modelSelect.firstChild);
    models.forEach(model=>{
      const option=document.createElement('option');option.value=model;option.textContent=modelName(model);modelSelect.appendChild(option);
    });
    if(selected&&models.includes(selected))modelSelect.value=selected;
    updateModelHelp();
  }
  function renderList(){
    clearList();
    if(!printers.length){list.appendChild(text('p','Ingen printere er tilføjet endnu.','muted'));return}
    printers.forEach(printer=>{
      const effectiveEnabled=printer.model==='H2D'?false:printer.enabled===true;
      const card=document.createElement('article');card.className='printer-settings-row';
      const heading=document.createElement('div');heading.className='printer-settings-row-heading';
      heading.appendChild(text('h3',printer.name||'Printer uden navn'));
      heading.appendChild(text('span',effectiveEnabled?'I børnekøen':'Slået fra','printer-settings-state '+(effectiveEnabled?'enabled':'disabled')));
      card.appendChild(heading);
      const details=document.createElement('p');details.className='small-note';
      details.textContent=[modelName(printer.model),printer.host,printer.serial,printer.bed_type].filter(Boolean).join(' · ');
      card.appendChild(details);
      if(printer.model==='H2D')card.appendChild(text('p','H2D er kun til lærerprint.','small-note'));
      const edit=document.createElement('button');edit.type='button';edit.className='secondary';edit.textContent='Redigér';edit.dataset.printerId=printer.id||'';
      card.appendChild(edit);
      const probe=document.createElement('button');probe.type='button';probe.className='secondary';probe.textContent='Kontrollér certifikater';probe.dataset.certificateProbe=printer.id||'';probe.disabled=certificateBusy.has(printer.id);
      card.appendChild(probe);
      const channels=printer.certificate_trust?.channels||{};
      ['mqtt','ftp'].forEach(channel=>{
        const trust=channels[channel],pending=trust?.pending,label=channel==='ftp'?'FTPS':'MQTT';
        card.appendChild(text('p',`${label}: ${trust?.status==='trusted'?'Godkendt certifikat':pending?'Afventer din godkendelse':'Certifikatet er ikke bekræftet'}`,'small-note'));
        if(trust?.error)card.appendChild(text('p',trust.error,'small-note'));
        if(!pending)return;
        card.appendChild(text('p',`Printer: ${pending.host} · ${pending.serial}. Gammelt fingeraftryk: ${pending.old_fingerprint||'ingen'}. Nyt: ${pending.new_fingerprint}`,'small-note'));
        const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=`Bekræft ${label}-certifikat`;button.dataset.certificateConfirm=printer.id;button.dataset.certificateChannel=channel;button.disabled=certificateBusy.has(printer.id);card.appendChild(button);
      });
      list.appendChild(card);
    });
    if(certificateOperations.length){
      list.appendChild(text('h3','Afventende certifikatforsøg'));
      certificateOperations.forEach(operation=>{
        const printer=printers.find(item=>item.id===operation.id);
        const row=document.createElement('div');row.className='small-note';
        row.appendChild(text('p',`${printer?.name||'Printer'} · ${String(operation.channel||'').toUpperCase()}: ${operation.error||'Et tidligere bekræftet forsøg mangler afslutning.'}`));
        if(printer&&operation.request_id&&operation.actor){
          const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='Afslut gammelt forsøg';
          button.dataset.certificateCancel=printer.id;button.dataset.operationRequest=operation.request_id;button.dataset.operationActor=operation.actor;button.disabled=certificateBusy.has(printer.id);row.appendChild(button);
        }
        list.appendChild(row);
      });
    }
  }
  function resetForm(){
    form.reset();editingId='';access.value='';message(formError,'');
    $('printer-settings-save').disabled=false;$('printer-settings-cancel').disabled=false;$('printer-settings-cancel-bottom').disabled=false;
    $('printer-settings-dialog-title').textContent='Tilføj printer';
    $('printer-settings-access-help').textContent='På en eksisterende printer: lad feltet stå tomt for at beholde koden.';
    renderModels();updateModelHelp();
  }
  function openForm(printer){
    resetForm();
    editingId=printer?.id||'';
    $('printer-settings-dialog-title').textContent=editingId?'Redigér printer':'Tilføj printer';
    if(printer){
      $('printer-settings-name').value=printer.name||'';
      $('printer-settings-host').value=printer.host||'';
      $('printer-settings-serial').value=printer.serial||'';
      $('printer-settings-bed').value=printer.bed_type||'Textured PEI Plate';
      enabled.checked=printer.enabled===true;
      renderModels(printer.model);
      $('printer-settings-access-help').textContent='Lad feltet stå tomt for at beholde den eksisterende LAN-adgangskode.';
    }
    message(status,'');
    $('printer-settings-dialog').showModal();
    $('printer-settings-name').focus();
  }
  function closeForm(){
    if(busy){access.value='';return}
    access.value='';
    if($('printer-settings-dialog').open)$('printer-settings-dialog').close();
    resetForm();
  }
  async function refresh(){
    if(window.teacherSessionReady!==true||busy)return;
    const generation=sessionGeneration;
    setAddAvailability(false);
    message(errorBox,'');
    try{
      const [settings,certificates]=await Promise.all([request('/api/settings'),request('/api/printers/certificates')]);
      if(generation!==sessionGeneration||window.teacherSessionReady!==true)return;
      printers=Array.isArray(settings.printers)?settings.printers:[];
      const trustById=new Map((certificates.printers||[]).map(item=>[item.id,item.certificate_trust]));
      printers=printers.map(printer=>({...printer,certificate_trust:trustById.get(printer.id)}));
      certificatePaused=certificates.queue?.paused===true;
      certificateOperations=Array.isArray(certificates.certificate_operations)?certificates.certificate_operations:[];
      models=Array.isArray(settings.models)?settings.models:[];
      loaded=true;renderModels();renderList();
      setAddAvailability(settings.demo!==true);
      if(settings.demo)message(status,'Printeropsætning er kun tilgængelig i skoleversionen.');
    }catch(err){
      if(generation!==sessionGeneration||err?.status===401||window.teacherSessionReady!==true){if(generation===sessionGeneration)clear();return}
      loaded=false;setAddAvailability(false);message(errorBox,err.message||'Printeropsætningen kunne ikke hentes.');
      clearList();list.appendChild(text('p','Printeropsætningen kunne ikke hentes.','muted'));
    }
  }
  function clear(){
    sessionGeneration++;printers=[];models=[];certificateOperations=[];loaded=false;busy=false;editingId='';access.value='';certificateRequests.clear();certificateBusy.clear();setAddAvailability(false);
    message(errorBox,'');message(formError,'');message(status,'');clearList();list.appendChild(text('p','Log ind for at hente printeropsætningen.','muted'));
    if($('printer-settings-dialog').open)$('printer-settings-dialog').close();
  }
  function certificateRequestId(){return crypto.randomUUID().replace(/-/g,'')}
  async function cancelCertificateOperation(id,operationId,actor){
    if(window.teacherSessionReady!==true||certificateBusy.has(id))return;
    const printer=printers.find(item=>item.id===id);
    if(!printer||!certificateOperations.some(item=>item.id===id&&item.request_id===operationId&&item.actor===actor))return;
    if(!confirm(`Afslut det gamle certifikatforsøg for ${printer.name}? Allerede godkendt tillid bevares. Job, kø og pladestatus ændres ikke. Du kan derefter kontrollere certifikaterne igen.`))return;
    const key=['cancel',id,actor,operationId,printer.version].join(':');
    if(!certificateRequests.has(key))certificateRequests.set(key,{id,operation_request_id:operationId,operation_actor:actor,expected_version:printer.version||1,request_id:certificateRequestId()});
    const generation=sessionGeneration;certificateBusy.add(id);renderList();message(errorBox,'');
    try{await request('/api/printers/certificates/cancel',certificateRequests.get(key));if(generation!==sessionGeneration||window.teacherSessionReady!==true)return;certificateRequests.delete(key);message(status,'Det gamle forsøg er afsluttet. Allerede godkendt tillid bevares. Kontrollér certifikatstatus nedenfor.')}
    catch(err){if(generation===sessionGeneration&&window.teacherSessionReady===true)message(errorBox,(err.message||'Forsøget kunne ikke afsluttes.')+' Samme afslutning bevares ved genforsøg.')}
    finally{certificateBusy.delete(id);if(generation===sessionGeneration&&window.teacherSessionReady===true)await refresh()}
  }
  async function certificateAction(id,channel){
    if(window.teacherSessionReady!==true||certificateBusy.has(id))return;
    const printer=printers.find(item=>item.id===id);if(!printer)return;
    let path='/api/printers/certificates/probe',body={id,expected_version:printer.version||1,request_id:certificateRequestId()};
    let key;
    if(channel){
      const pending=printer.certificate_trust?.channels?.[channel]?.pending;if(!pending)return;
      if(!confirm(`Bekræft ${channel==='ftp'?'FTPS':'MQTT'}-certifikatet for ${printer.name} (${printer.host}, ${printer.serial})?\nGammelt: ${pending.old_fingerprint||'ingen'}\nNyt: ${pending.new_fingerprint}\n${certificatePaused?'Køen er pauset. Dette kan reparere forbindelsen; job og pladekontrol bevares.':'Kontrollér, at adressen og serienummeret tilhører den tilsigtede printer.'}\nPladen skal godkendes på ny før næste print.`))return;
      key=[id,channel,pending.new_fingerprint,printer.version,certificatePaused].join(':');
      if(!certificateRequests.has(key))certificateRequests.set(key,{id,channel,fingerprint:pending.new_fingerprint,confirmed:true,repair:certificatePaused,expected_version:printer.version||1,request_id:certificateRequestId()});
      body=certificateRequests.get(key);path='/api/printers/certificates/confirm';
    }
    const generation=sessionGeneration;certificateBusy.add(id);renderList();message(errorBox,'');
    try{await request(path,body);if(generation!==sessionGeneration||window.teacherSessionReady!==true)return;if(key)certificateRequests.delete(key);message(status,channel?'Certifikatbekræftelsen er gemt. Kontrollér og godkend den ryddede plade før næste print.':'Certifikaterne er kontrolleret. Bekræft de observerede fingeraftryk nedenfor.')}
    catch(err){if(generation===sessionGeneration&&window.teacherSessionReady===true)message(errorBox,(err.message||'Certifikatet kunne ikke bekræftes.')+(key?' Samme bekræftelse bevares ved genforsøg.':''))}
    finally{certificateBusy.delete(id);if(generation===sessionGeneration&&window.teacherSessionReady===true)await refresh()}
  }
  async function save(){
    if(busy)return;
    if(window.teacherSessionReady!==true){clear();return}
    if(!loaded){message(formError,'Printeropsætningen er ikke klar endnu.');return}
    message(formError,'');
    const payload={
      name:$('printer-settings-name').value.trim(),model:modelSelect.value,
      host:$('printer-settings-host').value.trim(),serial:$('printer-settings-serial').value.trim(),
      bed_type:$('printer-settings-bed').value,access_code:access.value.trim(),
      enabled:modelSelect.value==='H2D'?false:enabled.checked
    };
    if(editingId)payload.id=editingId;
    if(!payload.name||!payload.model||!payload.host||!payload.serial){message(formError,'Udfyld navn, model, lokal IPv4-adresse og serienummer.');return}
    if(!editingId&&!payload.access_code){message(formError,'Udfyld printerens LAN-adgangskode.');return}
    busy=true;$('printer-settings-save').disabled=true;$('printer-settings-cancel').disabled=true;$('printer-settings-cancel-bottom').disabled=true;
    const wasEdit=Boolean(editingId);
    const generation=sessionGeneration;
    try{
      await request('/api/printers/save',payload);
      if(generation!==sessionGeneration||window.teacherSessionReady!==true)return;
      access.value='';busy=false;closeForm();message(status,wasEdit?'Printeren er opdateret.':payload.enabled?'Printeren er tilføjet og med i børnekøen.':'Printeren er tilføjet og slået fra i børnekøen.');
      await refresh();
    }catch(err){if(generation===sessionGeneration&&window.teacherSessionReady===true)message(formError,err.message||'Printeren kunne ikke gemmes.');access.value=''}
    finally{if(generation===sessionGeneration){busy=false;$('printer-settings-save').disabled=false;$('printer-settings-cancel').disabled=false;$('printer-settings-cancel-bottom').disabled=false}access.value=''}
  }
  setAddAvailability(false);
  $('printer-settings-add').addEventListener('click',()=>{if(!busy&&loaded&&window.teacherSessionReady===true)openForm(null)});
  list.addEventListener('click',event=>{
    const cancellation=event.target.closest('[data-certificate-cancel]');if(cancellation){cancelCertificateOperation(cancellation.dataset.certificateCancel,cancellation.dataset.operationRequest,cancellation.dataset.operationActor);return}
    const probe=event.target.closest('[data-certificate-probe]');if(probe){certificateAction(probe.dataset.certificateProbe);return}
    const confirmation=event.target.closest('[data-certificate-confirm]');if(confirmation){certificateAction(confirmation.dataset.certificateConfirm,confirmation.dataset.certificateChannel);return}
    const button=event.target.closest('[data-printer-id]');if(!button)return;
    const printer=printers.find(item=>String(item.id)===String(button.dataset.printerId));if(printer)openForm(printer);
  });
  $('printer-settings-model').addEventListener('change',updateModelHelp);
  form.addEventListener('submit',event=>{event.preventDefault();save()});
  $('printer-settings-cancel').addEventListener('click',closeForm);
  $('printer-settings-cancel-bottom').addEventListener('click',closeForm);
  const dialog=$('printer-settings-dialog');
  dialog.addEventListener('cancel',event=>{access.value='';if(busy)event.preventDefault()});
  dialog.addEventListener('close',()=>{access.value='';if(!busy)resetForm()});
  window.teacherPrinterSettings={refresh,clear};
  if(window.teacherSessionReady===true)refresh();
})();
