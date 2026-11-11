const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {execFileSync}=require('node:child_process');

class Element {
  constructor(tag='div',id=''){this.tagName=tag.toUpperCase();this.id=id;this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.checked=false;this.open=false;this.dataset={};this.children=[];this.listeners={};this.parentNode=null;}
  appendChild(child){child.parentNode=this;this.children.push(child);return child;}
  removeChild(child){const i=this.children.indexOf(child);if(i>=0)this.children.splice(i,1);return child;}
  get firstChild(){return this.children[0]||null;}
  addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
  dispatch(type,extra={}){const event={defaultPrevented:false,preventDefault(){this.defaultPrevented=true},target:this,currentTarget:this,...extra};for(const fn of this.listeners[type]||[])fn(event);return event;}
  closest(selector){if(selector==='[data-printer-id]'&&this.dataset.printerId)return this;return null;}
  focus(){}
  showModal(){this.open=true;}
  close(){this.open=false;}
  reset(){for(const child of this.children)child.value='';}
}

const ids=['printer-settings-section','printer-settings-form','printer-settings-list','printer-settings-error','printer-settings-status','printer-settings-model','printer-settings-enabled','printer-settings-access','printer-settings-name','printer-settings-host','printer-settings-serial','printer-settings-bed','printer-settings-dialog','printer-settings-dialog-title','printer-settings-access-help','printer-settings-model-help','printer-settings-form-error','printer-settings-save','printer-settings-cancel','printer-settings-cancel-bottom','printer-settings-add'];
const elements=new Map(ids.map(id=>[id,new Element(id==='printer-settings-form'?'form':id==='printer-settings-dialog'?'dialog':'div',id)]));
const document={getElementById:id=>elements.get(id)||null,createElement:tag=>new Element(tag),body:new Element('body')};
const calls=[];let mode='normal';let pendingSave=null;
const settings=()=>({printers:[{id:'p1',name:'Eins',model:'A1 mini',host:'192.168.1.40',serial:'SERIAL1',bed_type:'Textured PEI Plate',enabled:true}],models:['A1 mini','P1S','H2D'],demo:false});
const context=vm.createContext({document,console,window:{teacherSessionReady:true,teacherPrinterApi:async(path,body)=>{
  calls.push({path,body});
  if(path==='/api/printers/certificates')return {printers:[],certificate_operations:[],queue:{paused:false}};
  if(path==='/api/settings'){if(mode==='unauthorized'){const error=new Error('401');error.status=401;context.window.teacherSessionReady=false;context.window.teacherPrinterSettings?.clear();throw error}return settings()}
  if(path==='/api/printers/save'){assert.equal(typeof body.enabled,'boolean');if(body.model==='H2D')assert.equal(body.enabled,false);if(mode==='hold-save')return new Promise(resolve=>{pendingSave={resolve,body}});return {ok:true}}
  throw new Error('Unexpected route '+path);
}}});
vm.runInContext(fs.readFileSync('web/printer-settings.js','utf8'),context,{filename:'web/printer-settings.js'});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const field=id=>elements.get(id);

(async()=>{
  await flush();
  assert.equal(calls[0].path,'/api/settings','settings load uses the teacher API');
  assert.equal(calls[0].body,undefined,'settings is a GET');
  assert.equal(elements.get('printer-settings-access').value,'','the password is never populated from GET');

  elements.get('printer-settings-add').dispatch('click');
  assert.equal(field('printer-settings-enabled').checked,false,'new printers start out of the pupil queue');
  field('printer-settings-access').value='escape-secret';const cancel=field('printer-settings-dialog').dispatch('cancel');assert.equal(cancel.defaultPrevented,false,'Escape/cancel is allowed when idle');field('printer-settings-dialog').close();field('printer-settings-dialog').dispatch('close');assert.equal(field('printer-settings-access').value,'','native dialog close clears the password');
  elements.get('printer-settings-add').dispatch('click');
  field('printer-settings-name').value='Ny printer';field('printer-settings-model').value='A1 mini';field('printer-settings-host').value='192.168.1.41';field('printer-settings-serial').value='SERIAL2';field('printer-settings-bed').value='Textured PEI Plate';field('printer-settings-access').value='new-secret';field('printer-settings-enabled').checked=false;
  elements.get('printer-settings-form').dispatch('submit');await flush();await flush();
  const newSave=calls.find(call=>call.path==='/api/printers/save'&&call.body.serial==='SERIAL2');
  assert.ok(newSave,'new printer is saved');assert.equal(newSave.body.enabled,false,'new printer is disabled by default');assert.equal(field('printer-settings-access').value,'','password is cleared after save');

  const editButton=elements.get('printer-settings-list').children[0].children.find(child=>child.dataset.printerId==='p1');elements.get('printer-settings-list').dispatch('click',{target:editButton});
  assert.equal(field('printer-settings-access').value,'','edit never receives the old password');field('printer-settings-name').value='Eins redigeret';elements.get('printer-settings-form').dispatch('submit');await flush();await flush();
  const editSave=calls.find(call=>call.path==='/api/printers/save'&&call.body.id==='p1');
  assert.ok(editSave,'existing printer is saved');assert.equal(editSave.body.access_code,'','blank edit password preserves the backend value');

  elements.get('printer-settings-add').dispatch('click');field('printer-settings-name').value='Aktiv printer';field('printer-settings-model').value='A1 mini';field('printer-settings-host').value='192.168.1.43';field('printer-settings-serial').value='SERIAL3';field('printer-settings-access').value='active-secret';field('printer-settings-enabled').checked=true;elements.get('printer-settings-form').dispatch('submit');await flush();await flush();
  const enabledSave=calls.find(call=>call.path==='/api/printers/save'&&call.body.serial==='SERIAL3');assert.equal(enabledSave.body.enabled,true,'teacher can explicitly enable a new printer');assert.match(field('printer-settings-status').textContent,/med i børnekøen/,'enabled new printer gets an accurate success message');

  elements.get('printer-settings-add').dispatch('click');field('printer-settings-name').value='H2D';field('printer-settings-model').value='H2D';elements.get('printer-settings-model').dispatch('change');assert.equal(field('printer-settings-enabled').disabled,true,'H2D cannot enter the pupil queue');field('printer-settings-host').value='192.168.1.42';field('printer-settings-serial').value='SERIAL4';field('printer-settings-access').value='h2d-secret';elements.get('printer-settings-form').dispatch('submit');await flush();await flush();
  const h2d=calls.find(call=>call.path==='/api/printers/save'&&call.body.serial==='SERIAL4');assert.equal(h2d.body.enabled,false,'H2D is always excluded from the pupil queue');

  elements.get('printer-settings-add').dispatch('click');field('printer-settings-name').value='Afbrudt printer';field('printer-settings-model').value='A1 mini';field('printer-settings-host').value='192.168.1.44';field('printer-settings-serial').value='SERIAL5';field('printer-settings-access').value='pending-secret';mode='hold-save';elements.get('printer-settings-form').dispatch('submit');await flush();assert.ok(pendingSave,'save request is pending');const busyCancel=field('printer-settings-dialog').dispatch('cancel');assert.equal(busyCancel.defaultPrevented,true,'cancel is blocked while save is pending');assert.equal(field('printer-settings-access').value,'','cancel clears the password even while the save remains pending');context.window.teacherSessionReady=false;context.window.teacherPrinterSettings.clear();assert.equal(field('printer-settings-access').value,'','logout clears pending password');mode='normal';pendingSave.resolve({ok:true});await flush();await flush();assert.match(field('printer-settings-list').children[0].textContent,/Log ind/,'logout keeps the cleared settings view');assert.equal(field('printer-settings-status').textContent,'','stale save cannot report success after logout');assert.equal(field('printer-settings-form-error').textContent,'','stale save cannot report an error after logout');

  const before401=calls.length;context.window.teacherSessionReady=false;await context.window.teacherPrinterSettings.refresh();assert.equal(calls.length,before401,'unauthenticated refresh performs no settings fetch');
  context.window.teacherSessionReady=true;mode='unauthorized';await context.window.teacherPrinterSettings.refresh();assert.match(elements.get('printer-settings-list').children[0].textContent,/Log ind/,'401 clears the settings view');assert.equal(field('printer-settings-access').value,'','logout clears password field');

  execFileSync(process.execPath,['--check','web/printer-settings.js'],{stdio:'inherit'});execFileSync(process.execPath,['--check','web/app.js'],{stdio:'inherit'});
  const html=fs.readFileSync('web/index.html','utf8');assert.match(html,/printer-settings\.js/);assert.match(html,/printer-settings-section/);assert.match(html,/printer-settings\.css/);
  console.log('Printer settings: new/edit blank-password/H2D disabled/logout 401 and syntax checks OK');
})().catch(error=>{console.error(error);process.exitCode=1});
