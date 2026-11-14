/* A bounded local identity form; drafts never replace the saved profile. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id),path='/api/admin/school-profile';
  let active=false,epoch=0,state=null,busy=false,dirty=false,pending=null,logo=null,logoMode='keep';
  const errors={invalid_school_name:'Skriv skolens navn med højst 120 tegn.',invalid_profile_colour:'Vælg en farve i formatet #123abc.',invalid_profile_font:'Vælg en af skrifttyperne på listen.',profile_font_unavailable:'Skrifttypen er ikke tilgængelig på serveren. Vælg Standard.',invalid_logo_png:'Vælg en almindelig PNG-fil uden animation.',logo_upload_too_large:'Logoet må højst fylde 2 MB.',logo_dimensions_exceeded:'Logoet må højst være 2048 × 2048 pixels.',stale_profile_revision:'Profilen er ændret et andet sted. Indlæs den gemte profil før næste forsøg.',profile_changed_externally:'Profilen er ændret et andet sted. Indlæs den gemte profil.',logo_processing_unavailable:'Serveren mangler billedbehandling til PNG. IT skal klargøre den først.'};
  function status(text,error=false){$('profile-status').textContent=text;$('profile-status').classList.toggle('fejl',error);}
  function sync(){const locked=!active||busy||!!pending||!state;$('school-profile-form').querySelectorAll('input,select,button').forEach(el=>el.disabled=locked);$('profile-refresh').disabled=busy||!!pending;$('profile-retry').hidden=!pending;$('profile-retry').disabled=busy||!active;}
  async function request(body){const response=await fetch(path,{credentials:'same-origin',redirect:'error',cache:'no-store',signal:AbortSignal.timeout(15000),...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});const value=await response.json();if(!response.ok){const error=Error(value.error||'request_failed');error.status=response.status;throw error;}return value;}
  function dark(value){const rgb=[1,3,5].map(i=>parseInt(value.slice(i,i+2),16));const luminance=()=>rgb.map(v=>v/255<=.04045?v/255/12.92:((v/255+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);while(luminance()>.175)rgb.forEach((v,i)=>rgb[i]=Math.floor(v*.9));return '#'+rgb.map(v=>v.toString(16).padStart(2,'0')).join('');}
  function preview(){
    const value=$('profile-colour-text').value.trim();if(/^#[a-f0-9]{6}$/i.test(value)){$('profile-preview').style.setProperty('--profile-colour',value);$('profile-preview-button').style.backgroundColor=dark(value);$('profile-colour').value=value;}
    $('profile-preview-name').textContent=$('profile-name').value||'Skolens navn';
    $('profile-preview').style.fontFamily=({standard:'system-ui, sans-serif',manrope:'Manrope, sans-serif',metrophobic:'Metrophobic, sans-serif'})[$('profile-font').value];
    const source=logoMode==='remove'?null:logoMode==='replace'?'data:image/png;base64,'+logo:state?.logoPreviewPath?state.logoPreviewPath+'?revision='+state.revision:null;
    $('profile-preview-logo').hidden=!source;if(source)$('profile-preview-logo').src=source;else $('profile-preview-logo').removeAttribute('src');
    $('profile-remove-logo').disabled=busy||!!pending||!source;
  }
  function populate(){
    $('profile-name').value=state.profile.school_name;$('profile-colour').value=state.profile.colour;$('profile-colour-text').value=state.profile.colour;
    $('profile-font').replaceChildren(...state.fonts.map(font=>{const option=new Option(font.label+(font.available?'':' (ikke tilgængelig)'),font.id);option.disabled=!font.available;return option;}));$('profile-font').value=state.profile.font;
    $('profile-legacy').hidden=state.state!=='legacy';logo=null;logoMode='keep';$('profile-logo').value='';$('profile-logo-info').textContent='PNG · højst 2 MB og 2048 × 2048 pixels. Gennemsigtig baggrund understøttes.';dirty=false;sync();preview();
  }
  async function load(){if(!active||busy)return;busy=true;const run=epoch;sync();try{const value=await request();if(!active||run!==epoch)return;if(!Number.isSafeInteger(value.revision)||!value.profile||!Array.isArray(value.fonts))throw Error('invalid_response');state=value;populate();status('Den gemte profil er indlæst. Ændringer bruges først, når du gemmer.');}catch(error){if(active&&run===epoch)status(error.status===401?'Log ind igen for at ændre skoleprofilen.':'Skoleprofilen kunne ikke hentes. Prøv igen.',true);}finally{busy=false;sync();}}
  async function save(command){if(!active||busy)return;busy=true;pending=command;let confirmed=false;const run=epoch;sync();try{const receipt=await request(command);if(receipt.command_id!==command.command_id||receipt.status!=='saved'||!Number.isSafeInteger(receipt.revision))throw Error('invalid_response');confirmed=true;pending=null;dirty=false;if(active&&run===epoch){state=await request();populate();status('Skoleprofilen er gemt. Elevcomputere henter den via deres eksisterende skoleforbindelse.');}}
    catch(error){if(confirmed){state=null;if(active&&run===epoch)status('Skoleprofilen er gemt, men kunne ikke indlæses. Tryk Indlæs gemt profil.',true);}else if(error.status>=400&&error.status<500&&error.status!==401||error.message==='logo_processing_unavailable'){pending=null;if(active&&run===epoch)status(errors[error.message]||(error.status===409?'Profilen er ændret. Indlæs den gemte profil og kontroller dine valg.':error.status===401?'Log ind igen.':'Profilen blev ikke gemt. Kontroller felterne og prøv igen.'),true);}else if(active&&run===epoch)status(error.status===401?'Log ind igen, og afklar derefter samme gemning.':'Svaret mangler. Afklar samme gemning, før du ændrer flere felter.',true);}
    finally{busy=false;sync();}}
  $('school-profile-form').addEventListener('input',event=>{dirty=true;if(event.target.id==='profile-colour')$('profile-colour-text').value=event.target.value;preview();});
  $('profile-logo').addEventListener('change',async()=>{
    const file=$('profile-logo').files[0],run=epoch;if(!file)return;
    if(file.size>2*1024**2){status(errors.logo_upload_too_large,true);$('profile-logo').value='';return;}
    try{const bytes=new Uint8Array(await file.arrayBuffer());if(!active||run!==epoch)return;if([137,80,78,71,13,10,26,10].some((v,i)=>bytes[i]!==v))throw Error('invalid_logo_png');let binary='';for(let start=0;start<bytes.length;start+=8192)binary+=String.fromCharCode(...bytes.subarray(start,start+8192));logo=btoa(binary);logoMode='replace';dirty=true;$('profile-logo-info').textContent=file.name+' · klar til at gemme';preview();status('Forhåndsvisning opdateret. Gem for at bruge logoet.');}catch(error){status(errors[error.message]||'Logoet kunne ikke læses.',true);$('profile-logo').value='';}
  });
  $('profile-remove-logo').onclick=()=>{logo=null;logoMode='remove';dirty=true;$('profile-logo').value='';preview();status('Logoet fjernes fra profilen, når du gemmer.');};
  $('school-profile-form').addEventListener('submit',event=>{event.preventDefault();if(busy||pending||!state||!$('school-profile-form').reportValidity())return;void save({command_id:crypto.randomUUID(),expected_revision:state.revision,profile:{school_name:$('profile-name').value.trim(),colour:$('profile-colour-text').value.trim(),font:$('profile-font').value,logo_mode:logoMode,logo_png:logoMode==='replace'?logo:null}});});
  $('profile-refresh').onclick=()=>{if(dirty){$('profile-discard').showModal();$('profile-discard-cancel').focus();}else void load();};
  $('profile-discard-confirm').onclick=()=>{$('profile-discard').close();void load();};$('profile-discard-cancel').onclick=()=>$('profile-discard').close();
  $('profile-retry').onclick=()=>pending&&void save(pending);
  window.SchoolProfile={start(){if(active)return;active=true;epoch++;if(!state)void load();else sync();},stop(){active=false;epoch++;if($('profile-discard').open)$('profile-discard').close();sync();}};
})();
