import {NS,OPS,LABELS,COLORS,uid,round,el,loadFont,shape,textPath,gear,bounds,closed,move,scale,rotate,center,freehand,combine,nodes,moveNode,pointPathData,setNodeHandle,changeNode,nearestCurve,bendCurve,insertCurveNode,straightenCurve,setContourClosed,validPath,drawingIssues,exportSVG,importSVG} from './laser-geometry.js';
import {validBitmap,imageOutline,bitmapElement} from './laser-geometry.js';
import {readBitmap,traceBitmap,cancelTrace} from './laser-images.js';
import {MATS,vinylState,brotherIssues,brotherNotes,exportBrotherSVG,configureVinylUI} from './brother.js';
import {installVinylWorkshop} from './vinyl-workshop-ui.js';
import {installLaserWorkshop} from './laser-workshop-ui.js';
import {getLaserGuide} from './laser-project-guides.js';
import {offsetOutline} from './laser-offset.js';
import {buildProject,getProject} from './design-projects.js';
import {rotateText,scaleText,changedText,transformText} from './laser-text.js';
import {cleanLearning} from './laser-geometry.js';
import {appendLibraryItems,installLaserLibrary} from './laser-library.js';

const $=id=>document.getElementById(id);
const VINYL=location.pathname==='/folie.html';
const MANAGED=!!document.getElementById('elevhub');
const LASER_BED={width:600,height:375};
const STORAGE=VINYL?'kloster-folievaerksted-v1':'kloster-laservaerksted-v1';
const PENDING_PROJECT='kloster-designvaerksted-open-project';
const newState=()=>VINYL?vinylState({version:1,name:'Mit foliedesign',width:296,height:298,items:[]}):{version:1,name:'Min tegning',width:180,height:120,items:[]};
const issuesForDrawing=s=>[...(VINYL?brotherIssues(s):drawingIssues(s)),...projectIssues(s).map(issue=>issue.message)];
const exportDrawing=(s,filter='all')=>VINYL?exportBrotherSVG(s):exportSVG(s,filter);
let state=newState();
let selected=new Set(),tool='select',level=0,zoom=1,drag=null,undo=[],redo=[],savedJSON='',toastTimer;
let inboxAvailable=false,pendingSubmission=null,sending=false,sendDraft=null;
let latestReceipt=null;
let pointPath=[],pointRedo=[],pointHover=null;
let activeNode=null,activeCurve=null,addingNode=false,clipboard=[];
let imageDraft=null,imageTarget=null,traceTimer,traceRevision=0;
let offsetSources=[],offsetDraft=null,offsetTimer;
let boxDraft=null,boxRequest=0;
let inspiration=null,pendingProjectId=null;
const drawing=$('drawing'),itemsLayer=$('items'),selectionLayer=$('selection');
const objectById=id=>state.items.find(o=>o.id===id);
const chosen=()=>state.items.filter(o=>selected.has(o.id));
const one=()=>selected.size===1?objectById([...selected][0]):null;
const num=(id,min,max)=>{const v=Number($(id).value);if(!Number.isFinite(v)||v<min||v>max)throw Error(`Skriv et tal mellem ${min} og ${max}.`);return v;};
function notify(message,error=false){$('notice').textContent=message;$('notice').classList.toggle('error',error);$('notice').hidden=false;}
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,3000);}
function projectIssues(s){
  const learning=cleanLearning(s.learning);
  if(!learning||!getProject(learning.id,VINYL)||!learning.frameId)return [];
  const frame=s.items.find(o=>o.id===learning.frameId);
  if(!frame)return [];
  const area=bounds(frame.d),problems=[];
  for(const id of learning.contentIds||[]){
    const item=s.items.find(o=>o.id===id);if(!item)continue;
    const b=bounds(item.d);
    if(b.x<area.x-.02||b.y<area.y-.02||b.x+b.width>area.x+area.width+.02||b.y+b.height>area.y+area.height+.02)
      problems.push({id,message:`“${item.name}” går ud over projektets kant. Flyt delen ind, eller gør den mindre.`});
  }
  return problems;
}
function renderIssueList(container,issues){
  container.replaceChildren();
  for(const message of issues){
    const match=projectIssues(state).find(issue=>issue.message===message),name=message.match(/“([^”]+)”/)?.[1];
    const item=state.items.find(o=>o.id===match?.id)||state.items.find(o=>o.name===name);
    const row=document.createElement(item?'button':'p');row.textContent=message;
    if(item){row.type='button';row.className='text-button issue-action';row.title='Find delen på tegningen';row.onclick=()=>{if($('preview-dialog').open)$('preview-dialog').close();setTool('select');select(item.id);$('object-list').querySelector(`[data-id="${CSS.escape(item.id)}"]`)?.scrollIntoView({block:'nearest'});};}
    container.append(row);
  }
  if(container.id==='drawing-issues')$('drawing-check').hidden=!issues.length;
}
function requestStarter(id,mode='replace'){
  if(!VINYL)return startLaserGuide(id);
  if(!getProject(id,VINYL))throw Error('Projektet findes ikke i dette værksted.');
  if(!finishPendingPoints())return;
  if(mode==='insert'||!state.items.length&&!state.learning){applyStarter(id,mode);return;}
  pendingProjectId=id;$('starter-name').textContent=getProject(id,VINYL).title;$('starter-dialog').showModal();
}
function startLaserGuide(id){
  const guide=getLaserGuide(id);
  if(!guide)throw Error('Projektguiden findes ikke.');
  if(!finishPendingPoints())return false;
  // A guide attaches to the current drawing. It never creates or replaces parts.
  edit(()=>{state.learning={id,version:1,guide:true,stepIndex:0,helpHidden:false,completed:false};});
  setLevel(Math.max(level,guide.level));
  announceProjectChanged();
  return true;
}
function applyStarter(id,mode){
  const project=buildProject(id,VINYL),card=getProject(id,VINYL);
  if(mode==='insert'&&state.items.length+project.items.length>500)throw Error('Der kan højst være 500 dele i en tegning.');
  setTool('select');
  edit(()=>{
    if(mode==='insert'){
      state.items.push(...project.items);state.learning=project.learning;
      if(!VINYL){state.width=Math.max(state.width,project.width);state.height=Math.max(state.height,project.height);}
    }else state={...newState(),name:project.name,width:project.width,height:project.height,items:project.items,learning:project.learning};
    selected=new Set(project.selectedId?[project.selectedId]:[]);zoom=1;
  });
  setLevel(card.level);
  if($('starter-dialog').open)$('starter-dialog').close();pendingProjectId=null;
  announceProjectChanged();toast('Projektet er åbnet. Du kan hente hjælp, vælge flere værktøjer og fortryde.');
  if(card.action==='box')$('box-button').click();
}
function announceProjectChanged(){window.dispatchEvent(new Event('elevhub-project-changed'));}
function attempt(fn){try{return fn();}catch(e){notify(e.message,true);return null;}}
function snapshot(){return JSON.stringify(state);}
function persist(){
  const json=snapshot();
  try{localStorage.setItem(STORAGE,json);$('save-status').textContent=MANAGED?'Sikkerhedskopi · brug Gem projekt for at gemme':'Kladde på denne computer';savedJSON=json;return true;}catch{$('save-status').textContent='Gem projekt for at beholde din tegning';return false;}
}
function restoreReceipt(){
  if(VINYL)return;
  try{
    const raw=localStorage.getItem(STORAGE+'-receipt');
    if(!raw)return;
    const value=JSON.parse(raw);
    if(value&&['id','project','name','classroom','submittedAt'].every(key=>typeof value[key]==='string'&&value[key].length>0&&value[key].length<=100)&&Number.isFinite(Date.parse(value.submittedAt)))latestReceipt=value;
  }catch{latestReceipt=null;}
}
function persistReceipt(receipt){
  latestReceipt=receipt;
  try{localStorage.setItem(STORAGE+'-receipt',JSON.stringify(receipt));}catch{}
  renderReceipt();
}
function resetSendDialog(){
  $('send-question').hidden=false;$('send-note').hidden=false;$('send-success-title').hidden=true;
  $('send-preview').closest('.send-review-grid').hidden=false;$('send-status').textContent='';$('send-status').classList.remove('error');
  const confirm=$('confirm-send');confirm.type='submit';confirm.textContent='Ja, send til læreren';confirm.disabled=false;confirm.onclick=null;
}
function showSendSuccess(message){
  $('send-question').hidden=true;$('send-note').hidden=true;$('send-success-title').hidden=false;
  $('send-preview').closest('.send-review-grid').hidden=true;$('send-status').classList.remove('error');$('send-status').textContent=message;
  const confirm=$('confirm-send');confirm.type='button';confirm.textContent='Tegn videre';confirm.disabled=false;confirm.onclick=()=>$('send-dialog').close();
}
function renderReceipt(){
  const panel=$('latest-receipt');
  if(!panel||VINYL){if(panel)panel.hidden=true;return;}
  panel.hidden=!latestReceipt;
  if(!latestReceipt)return;
  const when=new Date(latestReceipt.submittedAt);
  const stamp=Number.isNaN(when.getTime())?'':` · ${when.toLocaleString('da-DK',{dateStyle:'short',timeStyle:'short'})}`;
  $('latest-receipt-text').textContent=`${latestReceipt.project} · ${latestReceipt.name} (${latestReceipt.classroom}) · Afleveringsnummer ${latestReceipt.id.slice(0,8).toUpperCase()}${stamp}`;
}
function checkpoint(){undo.push(snapshot());if(undo.length>60)undo.shift();redo=[];}
function startEditDrag(details){
  const previousHistory={undo:[...undo],redo:[...redo]};checkpoint();
  drag={...details,original:undo.at(-1),previousHistory};
}
function edit(fn){checkpoint();fn();render();persist();}
function history(back){
  if(tool==='polyline'){
    const from=back?pointPath:pointRedo,to=back?pointRedo:pointPath;
    if(from.length)to.push(from.pop());pointHover=null;render();return;
  }
  const from=back?undo:redo,to=back?redo:undo;if(!from.length)return;to.push(snapshot());state=JSON.parse(from.pop());selected.clear();activeNode=null;activeCurve=null;render();persist();
}
function groupBounds(objects=chosen()){
  if(!objects.length)return null;
  const all=objects.map(o=>bounds(o.d));const x=Math.min(...all.map(b=>b.x)),y=Math.min(...all.map(b=>b.y));
  const width=Math.max(...all.map(b=>b.x+b.width))-x,height=Math.max(...all.map(b=>b.y+b.height))-y;
  return {x,y,width,height,cx:x+width/2,cy:y+height/2};
}
function clearGenerator(o){delete o.text;delete o.gear;}
function scaledGenerator(o,sx,sy){
  if(o.text)scaleText(o,sx,sy);
  delete o.gear;
}
function fit(d,maxW,maxH){const b=bounds(d),s=Math.min(1,maxW/Math.max(b.width,.1),maxH/Math.max(b.height,.1));return scale(d,s,s,b.cx,b.cy);}
function makeItem(name,d,op='cut',extra={}){return {id:uid(),name,d,op:VINYL?'cut':op,fillRule:'nonzero',...extra};}
function addItem(o){
  if(state.items.length>=500)throw Error('Der kan højst være 500 dele i en tegning.');
  setTool('select');
  // The old modulo offset put the fifth starter shape back over the first
  // one. Find a bounded free slot instead, so duplicated forms remain
  // individually selectable and never start outside the material.
  const original=o.d,b=bounds(original),width=b.width,height=b.height,gap=4;
  const stepX=Math.max(width+gap,12),stepY=Math.max(height+gap,12);
  const occupied=state.items.map(item=>bounds(item.d));
  const fits=(x,y)=>x>=0&&y>=0&&x+width<=state.width&&y+height<=state.height;
  let placement=null;
  for(let row=0;row<Math.ceil(state.height/stepY)&&!placement;row++)
    for(let col=0;col<Math.ceil(state.width/stepX)&&!placement;col++){
      const x=Math.min(state.width-width,Math.max(0,col*stepX));
      const y=Math.min(state.height-height,Math.max(0,row*stepY));
      if(!fits(x,y))continue;
      const overlap=occupied.some(q=>x<q.x+q.width+gap&&x+width+gap>q.x&&y<q.y+q.height+gap&&y+height+gap>q.y);
      if(!overlap)placement={x,y};
    }
  if(!placement)throw Error('Der er ikke plads nok på materialet til en ny figur.');
  o.d=move(original,placement.x-b.x,placement.y-b.y);
  edit(()=>{state.items.push(o);selected=new Set([o.id]);});setTool('select');
}
function addKeyring(initial=false){
  if(!initial)setTool('select');
  const x=state.width/2-36,y=state.height/2-16;
  const outer=makeItem('Nøgleringens kant',move(shape('rect'),x,y));
  const b=bounds(outer.d);outer.d=scale(outer.d,72/b.width,32/b.height,b.x,b.y);
  const hole=makeItem('Hul til ring',`M${x+9} ${y+16}a2.5 2.5 0 1 0 -5 0a2.5 2.5 0 1 0 5 0Z`);
  const value='DIT NAVN',size=8;
  const text=makeItem(value,center(textPath(value,size),x+42,y+16),'engrave-fill',{text:{value,size}});
  const apply=()=>{state.items.push(outer,hole,text);selected=new Set([text.id]);};
  if(initial)apply();else edit(apply);
  setTool('select');
}
function setTool(next){
  if(['freehand','line'].includes(next)&&level<1||['polyline','nodes'].includes(next)&&level<2)return;
  if(tool==='polyline'&&next!==tool){
    if(pointPath.length>=2)finishPointPath(false,false);
    pointPath=[];pointRedo=[];pointHover=null;$('draft').replaceChildren();
  }
  if(next==='polyline'&&tool!==next)selected.clear();
  if(next!==tool){activeNode=null;activeCurve=null;addingNode=false;}
  tool=next;
  document.querySelectorAll('[data-tool]').forEach(b=>{const active=b.dataset.tool===tool;b.classList.toggle('active',active);b.setAttribute('aria-pressed',active);});
  $('workspace').classList.toggle('drawing-mode',['freehand','line','polyline'].includes(tool));
  document.querySelector('.drawing-panel').classList.toggle('point-mode',tool==='polyline');
  document.querySelector('.drawing-panel').classList.toggle('node-mode',tool==='nodes');
  $('tool-tip').textContent=({select:'Klik på en figur. Træk for at flytte den. Træk en ramme på papiret for at vælge flere.',freehand:'Hold musen nede og tegn. Din streg bliver en gravering.',line:'Træk fra stregens begyndelse til dens slutning.',polyline:'Klik for et hjørne. Hold musen nede og træk for at lave en kurve. Klik på det første punkt for at lukke figuren.',nodes:'Træk i en streg for at bøje den. Flyt punkter og håndtag. Dobbeltklik på stregen for at indsætte et punkt.'})[tool];
  if(VINYL&&tool==='freehand')$('tool-tip').textContent='Hold musen nede og tegn. Kniven følger din streg.';
  render();
}
function nearFirstPoint(p){return pointPath.length>=3&&Math.hypot(p.x-pointPath[0].x,p.y-pointPath[0].y)*screenScale()<=10;}
function renderPointPath(){
  $('point-toolbar').hidden=tool!=='polyline';
  if(tool!=='polyline')return;
  const n=pointPath.length,canClose=pointHover&&nearFirstPoint(pointHover);
  $('point-hint').textContent=n?`${n} ${n===1?'punkt':'punkter'} · ${n<3?'Klik for hjørner, træk for kurver.':'Første punkt lukker figuren. Træk et nyt punkt for kurver.'}`:'Klik for et hjørne. Hold musen nede og træk for en kurve.';
  $('point-finish').disabled=n<2;$('point-close').disabled=n<3;$('point-back').disabled=!n;
  $('undo').disabled=!n;$('redo').disabled=!pointRedo.length;
  const layer=$('draft');layer.replaceChildren();
  if(!n)return;
  layer.append(el('path',{d:pointPathData(pointPath),fill:'none',stroke:COLORS[VINYL?'cut':'engrave-line'],'stroke-width':1.8,'vector-effect':'non-scaling-stroke'}));
  if(pointHover){const p=canClose?pointPath[0]:pointHover,last=pointPath.at(-1);layer.append(el('path',{d:pointPathData([last,p]),fill:'none',stroke:canClose?COLORS.cut:COLORS[VINYL?'cut':'engrave-line'],'stroke-width':1.5,'stroke-dasharray':'4 3','vector-effect':'non-scaling-stroke'}));}
  const last=pointPath.at(-1);for(const v of [last.hin,last.hout])if(v&&Math.hypot(v.x,v.y)>.01){layer.append(el('line',{x1:last.x,y1:last.y,x2:last.x+v.x,y2:last.y+v.y,class:'curve-arm'}),el('circle',{cx:last.x+v.x,cy:last.y+v.y,r:3.5/screenScale(),class:'curve-handle'}));}
  pointPath.forEach((p,i)=>layer.append(el('circle',{cx:p.x,cy:p.y,r:(i?3.5:5.5)/screenScale(),class:'point-mark'+(i?'':' first')+(!i&&canClose?' closing':'')})));
}
function addPoint(p){
  if(p.x<0||p.y<0||p.x>state.width||p.y>state.height)return;
  if(nearFirstPoint(p)){finishPointPath(true);return;}
  if(pointPath.length>=500){toast('Afslut figuren, før du tilføjer flere end 500 punkter.');return;}
  if(pointPath.length&&Math.hypot(p.x-pointPath.at(-1).x,p.y-pointPath.at(-1).y)<.01)return;
  pointPath.push(p);pointRedo=[];pointHover=null;render();
}
function finishPointPath(close=false,leaveTool=true){
  if(pointPath.length<(close?3:2)){toast(close?'Sæt mindst tre punkter for at lukke figuren.':'Sæt mindst to punkter for at lave en streg.');return;}
  if(state.items.length>=500)throw Error('Der kan højst være 500 dele i en tegning.');
  const d=pointPathData(pointPath,close);
  const item=makeItem(close?'Min punktfigur':'Punktstreg',d,close?'cut':'engrave-line');
  pointPath=[];pointRedo=[];pointHover=null;$('draft').replaceChildren();
  edit(()=>{state.items.push(item);selected=new Set([item.id]);});
  if(leaveTool)setTool('select');
}
function cancelPointPath(){pointPath=[];pointRedo=[];pointHover=null;$('draft').replaceChildren();setTool('select');}
function finishPendingPoints(){
  if(pointPath.length===1){toast('Tilføj et punkt mere, eller annullér punkttegningen.');return false;}
  if(pointPath.length>=2)finishPointPath(false);
  return true;
}
function setLevel(next){
  level=next;
  document.querySelectorAll('.level-bar [data-level]').forEach(b=>{const active=Number(b.dataset.level)===level;b.classList.toggle('active',active);b.setAttribute('aria-pressed',active);});
  document.querySelectorAll('[data-min-level]').forEach(n=>n.hidden=level<Number(n.dataset.minLevel));
  const boxPanel=document.querySelector('.box-generator');if(boxPanel)boxPanel.hidden=VINYL||level<3;
  document.querySelector('[data-tool="freehand"]').hidden=level<1;
  document.querySelector('[data-tool="line"]').hidden=level<1;
  document.querySelector('.board-settings').hidden=VINYL||level<3;
  document.querySelector('.position').hidden=level<3;
  document.querySelector('.rotate-row').hidden=level<1;
  $('object-name').hidden=level<1;
  document.querySelector('label[for="object-name"]').hidden=level<1;
  setTool('select');renderProperties();
  inspiration?.update(state,level);
  try{localStorage.setItem(STORAGE+'-level',String(level));}catch{}
}
function strokeShape(o,preview=false){
  if(o.image)return bitmapElement(o);
  const fill=o.op==='engrave-fill';
  return el('path',{d:o.d,fill:fill?COLORS[o.op]:'none',stroke:fill?'none':COLORS[o.op],'stroke-width':o.op==='cut'?1.5:1.8,'vector-effect':'non-scaling-stroke','fill-rule':o.fillRule||'nonzero','stroke-dasharray':o.op==='undecided'?'5 3':'none',...(preview?{}:{'pointer-events':fill?'visiblePainted':'stroke'})});
}
function renderItems(){
  itemsLayer.replaceChildren();
  for(const o of state.items){
    const g=el('g',{'class':'design-item','data-id':o.id,'data-op':o.op,'aria-label':o.name});
    const visible=el('g',{'class':'visible-shape'});visible.append(strokeShape(o));
    const hit=el('g',{'class':'hit-shape'}),hitAttrs={d:o.d};
    if(!closed(o.d))hitAttrs['data-open-path']='1';
    hit.append(el('path',hitAttrs));
    g.append(visible,hit);itemsLayer.append(g);
  }
  $('empty-state').hidden=state.items.length>0;
}
function screenScale(){const m=drawing.getScreenCTM();return m?m.a:4;}
function renderSelection(){
  selectionLayer.replaceChildren();if(tool==='polyline')return;const b=groupBounds();
  if(!b)return;
  const handle=8/screenScale(),o=one();
  if(tool==='nodes'&&o&&!o.image){
    const points=nodes(o.d);
    if(points.length>800){$('selection-status').textContent='Denne figur har for mange punkter til punktredigering.';return;}
    for(const point of points){
      const active=activeNode?.id===o.id&&activeNode.ci===point.ci&&activeNode.si===point.si;
      const atCurve=activeCurve?.id===o.id&&activeCurve.ci===point.ci&&(activeCurve.index===point.si||(activeCurve.index+1)%point.count===point.si);
      if(active||atCurve)for(const [which,h] of [['handleIn',point.hin],['handleOut',point.hout]])if(Math.hypot(h.x-point.x,h.y-point.y)>.001){
        selectionLayer.append(el('line',{x1:point.x,y1:point.y,x2:h.x,y2:h.y,class:'curve-arm'}),el('circle',{class:'curve-handle',cx:h.x,cy:h.y,r:handle*.45,'data-handle':which,'data-ci':point.ci,'data-si':point.si}));
      }
    }
    for(const point of points){const active=activeNode?.id===o.id&&activeNode.ci===point.ci&&activeNode.si===point.si;selectionLayer.append(el('circle',{class:'node-dot'+(active?' active':''),cx:point.x,cy:point.y,r:handle*.5,'data-ci':point.ci,'data-si':point.si}));}
    return;
  }
  selectionLayer.append(el('rect',{class:'selection-box',x:b.x-1,y:b.y-1,width:Math.max(b.width,1)+2,height:Math.max(b.height,1)+2}));
  if(!chosen().some(o=>o.gear))for(const [corner,x,y] of [['nw',b.x,b.y],['ne',b.x+b.width,b.y],['se',b.x+b.width,b.y+b.height],['sw',b.x,b.y+b.height]])selectionLayer.append(el('rect',{class:'selection-handle',x:x-handle/2,y:y-handle/2,width:handle,height:handle,'data-corner':corner}));
  const label=el('text',{class:'selection-label',x:b.cx,y:b.y-3,'text-anchor':'middle','font-size':11/screenScale()},`${round(b.width).toFixed(1)} × ${round(b.height).toFixed(1)} mm`);label.style.fontSize=11/screenScale()+'px';selectionLayer.append(label);
}
function renderProperties(){
  const objects=chosen(),o=one(),b=groupBounds();
  $('duplicate').disabled=$('delete').disabled=!objects.length;
  for(const id of ['unite','subtract','intersect','exclude'])$(id).disabled=objects.length<2;
  for(const id of ['flip-x','flip-y','rotate-exact','bring-front','send-back','copy-objects'])$(id).disabled=!objects.length;
  $('group-objects').disabled=objects.length<2;$('ungroup-objects').disabled=!objects.some(o=>o.groupId);$('paste-objects').disabled=!clipboard.length;
  document.querySelectorAll('[data-align]').forEach(b=>b.disabled=!objects.length);
  document.querySelectorAll('[data-distribute]').forEach(b=>b.disabled=selectedUnits().length<3);
  $('operation-help').textContent=o?o.name:objects.length?`${objects.length} dele er valgt`:'Vælg en figur på tegningen.';
  const ops=new Set(objects.map(o=>o.op));
  document.querySelectorAll('[data-op]').forEach(button=>{
    if(button.tagName!=='BUTTON')return;
    button.disabled=!objects.length;const active=ops.size===1&&(button.dataset.op==='cut'?ops.has('cut'):[...ops][0]?.startsWith('engrave'));
    button.classList.toggle('active',!!active);button.setAttribute('aria-pressed',!!active);
  });
  $('engrave-options').hidden=VINYL||!objects.length||!objects.every(o=>o.op.startsWith('engrave'));
  if(ops.size===1&&[...ops][0]?.startsWith('engrave'))$('engrave-mode').value=[...ops][0];
  $('offset-button').disabled=!objects.length;
  $('object-properties').hidden=!o;
  if(o){
    $('object-name').value=o.name;$('object-width').value=round(b.width).toFixed(1);$('object-height').value=round(b.height).toFixed(1);
    $('object-width').disabled=$('object-height').disabled=!!o.gear;
    $('object-x').value=round(b.x).toFixed(1);$('object-y').value=round(b.y).toFixed(1);
    $('text-properties').hidden=!o.text;
    if(o.text){$('text-value').value=o.text.value;$('text-size').value=o.text.size;}
    $('gear-properties').hidden=!o.gear;
    $('image-properties').hidden=!o.image;
    if(o.image){$('image-to-path').disabled=!o.image.trace;$('image-resolution').textContent=`${o.image.width} × ${o.image.height} pixels · ${o.image.trace?'Konturer er klar':'Ingen konturer endnu'}`;}
    if(o.gear){$('gear-teeth').value=o.gear.teeth;$('gear-hole').value=o.gear.hole;}
  }
  $('selection-status').textContent=objects.length?`${objects.length===1?o.name:objects.length+' dele'} · ${b.width.toFixed(1)} × ${b.height.toFixed(1)} mm`:'Vælg en figur for at begynde';
  $('object-count').textContent=state.items.length;
  $('layer-summary').replaceChildren();
  for(const [name,ops,color] of [['Skæring',['cut'],COLORS.cut],['Gravering',['engrave-line','engrave-fill'],COLORS['engrave-line']],['Mangler valg',['undecided'],COLORS.undecided]]){
    const count=state.items.filter(item=>ops.includes(item.op)).length;
    if(!count)continue;const tag=document.createElement('span');tag.textContent=`${name} · ${count}`;tag.style.borderColor=color;$('layer-summary').append(tag);
  }
  $('object-list').replaceChildren();
  for(const item of state.items){
    const button=document.createElement('button');button.className='object-row'+(selected.has(item.id)?' active':'');button.dataset.id=item.id;button.setAttribute('aria-pressed',selected.has(item.id));
    const dot=document.createElement('i');dot.style.color=COLORS[item.op];if(item.op==='engrave-fill')dot.style.background=COLORS[item.op];
    const name=document.createElement('span');name.textContent=item.name+(item.groupId?' · gruppe':'');
    const operation=document.createElement('small');operation.textContent=LABELS[item.op];
    button.append(dot,name,operation);button.addEventListener('click',e=>select(item.id,e.shiftKey));$('object-list').append(button);
  }
}
function render(){
  $('project-name').value=state.name;$('board-width').value=state.width;$('board-height').value=state.height;
  for(const id of ['paper','paper-grid','paper-shadow']){$(id).setAttribute('width',state.width);$(id).setAttribute('height',state.height);}
  const w=(state.width+16)/zoom,h=(state.height+16)/zoom;drawing.setAttribute('viewBox',`${state.width/2-w/2} ${state.height/2-h/2} ${w} ${h}`);
  $('zoom-reset').textContent=Math.round(zoom*100)+' %';$('board-label').textContent=`${state.width} × ${state.height} mm`;
  $('undo').disabled=!undo.length;$('redo').disabled=!redo.length;
  renderItems();renderProperties();renderSelection();renderPointPath();renderNodeTools();
  const issues=issuesForDrawing(state);$('readiness').textContent=issues.length?issues[0]:VINYL?'Skærelinjerne er klar':'Alle dele har et valg';$('readiness').classList.toggle('needs-work',!!issues.length);
  $('readiness-detail').textContent=issues.length?'':' · Gem tegningen, når du er tilfreds.';
  $('export-button').disabled=$('preview-export').disabled=!!issues.length;
  $('send-button').disabled=!inboxAvailable||!!issues.length;
  $('send-button').title=!inboxAvailable?'Aflevering kræver forbindelse til værkstedets server. Du kan stadig gemme SVG.':issues.join('\n');
  $('export-button').title=issues.join('\n');
  inspiration?.update(state,level);
  renderIssueList($('drawing-issues'),issues);
  $('connection-state').hidden=VINYL;
  renderReceipt();
  $('connection-message').textContent=inboxAvailable?'Forbindelse til læreren er klar.':'Ingen forbindelse til læreren. Gem projektet, og prøv forbindelsen igen.';
  if(VINYL){$('vinyl-mat').value=state.mat;$('vinyl-mirror').disabled=!state.items.length;$('board-label').textContent=`Skæreområde: ${state.width} × ${state.height} mm`;}
}
function select(id,multiple=false){
  if(tool==='polyline')setTool('select');
  const o=objectById(id),ids=tool!=='nodes'&&o?.groupId?state.items.filter(p=>p.groupId===o.groupId).map(p=>p.id):[id];
  if(!multiple)selected=new Set(ids);else{const remove=ids.every(i=>selected.has(i));ids.forEach(i=>remove?selected.delete(i):selected.add(i));}
  activeNode=null;activeCurve=null;renderProperties();renderSelection();renderNodeTools();
}
function selectedUnits(){
  const map=new Map();for(const o of chosen()){const key=o.groupId||o.id;if(!map.has(key))map.set(key,[]);map.get(key).push(o);}return [...map.values()];
}
function renderNodeTools(){
  $('node-toolbar').hidden=tool!=='nodes';$('workspace').classList.toggle('add-node-mode',tool==='nodes'&&addingNode);
  if(tool!=='nodes')return;
  const selectedObject=one(),o=selectedObject?.image?null:selectedObject,point=o&&activeNode?.id===o.id?nodes(o.d).find(n=>n.ci===activeNode.ci&&n.si===activeNode.si):null;
  for(const id of ['node-smooth','node-corner','node-delete'])$(id).disabled=!point;
  $('node-add').disabled=!o;$('node-add').setAttribute('aria-pressed',addingNode);
  $('curve-straight').disabled=!o||activeCurve?.id!==o.id;
  const contour=o?selectedContour():null;$('contour-close').disabled=!contour||contour.closed;$('contour-open').disabled=!contour||!contour.closed;
  if(selectedObject?.image){$('node-hint').textContent='Vælg “Lav redigerbare vektorstreger” i billedets indstillinger for at flytte punkter.';return;}
  $('node-hint').textContent=!o?'Vælg én figur for at redigere punkter og kurver.':addingNode?'Klik på en streg for at tilføje et punkt.':point?`Punkt ${point.si+1} · Træk i håndtagene for at ændre buen. Shift låser retningen.`:'Træk i stregen for at bøje den. Dobbeltklik på stregen for at tilføje et punkt.';
}
function selectedContour(){
  const o=one();if(!o)return null;const points=nodes(o.d),indices=new Set(points.map(n=>n.ci));
  const ci=activeNode?.id===o.id?activeNode.ci:activeCurve?.id===o.id?activeCurve.ci:indices.size===1?0:null;
  return ci===null?null:points.find(n=>n.ci===ci);
}
function alterNode(kind){
  const o=one();if(!o||activeNode?.id!==o.id)return;
  const d=changeNode(o.d,activeNode.ci,activeNode.si,kind);
  edit(()=>{o.d=d;clearGenerator(o);if(kind==='delete')activeNode=null;activeCurve=null;});
}
function addNodeAt(o,p){
  if(o.image)return;
  const hit=nearestCurve(o.d,p.x,p.y);if(!hit||hit.distance*screenScale()>12)return;
  const result=insertCurveNode(o.d,hit);
  edit(()=>{o.d=result.d;clearGenerator(o);activeNode={id:o.id,ci:result.ci,si:result.si};activeCurve=null;addingNode=false;});
}
function setClosed(value){
  const o=one(),c=selectedContour();if(!o||!c)return;const d=setContourClosed(o.d,c.ci,value);
  edit(()=>{o.d=d;clearGenerator(o);activeCurve=null;if(!value&&o.op==='engrave-fill')o.op='engrave-line';});
}
function alignSelection(key){
  const units=selectedUnits();if(!units.length)return;
  const base=$('align-reference').value==='page'||units.length===1?{x:0,y:0,width:state.width,height:state.height,cx:state.width/2,cy:state.height/2}:groupBounds();
  const moves=units.map(objects=>{
    const b=groupBounds(objects),dx=key==='left'?base.x-b.x:key==='cx'?base.cx-b.cx:key==='right'?base.x+base.width-b.x-b.width:0;
    const dy=key==='top'?base.y-b.y:key==='cy'?base.cy-b.cy:key==='bottom'?base.y+base.height-b.y-b.height:0;
    return {objects,dx,dy};
  });
  edit(()=>moves.forEach(({objects,dx,dy})=>objects.forEach(o=>o.d=move(o.d,dx,dy))));
}
function distributeSelection(axis){
  const size=axis==='x'?'width':'height',units=selectedUnits().map(objects=>({objects,b:groupBounds(objects)})).sort((a,b)=>a.b[axis]-b.b[axis]);if(units.length<3)return;
  const first=units[0].b[axis],last=units.at(-1).b[axis]+units.at(-1).b[size],gap=(last-first-units.reduce((n,u)=>n+u.b[size],0))/(units.length-1);
  let position=first;const moves=units.map(u=>{const delta=position-u.b[axis];position+=u.b[size]+gap;return {...u,delta};});
  edit(()=>moves.forEach(u=>u.objects.forEach(o=>o.d=move(o.d,axis==='x'?u.delta:0,axis==='y'?u.delta:0))));
}
function groupObjects(ungroup=false){
  if(level<1||selected.size<(ungroup?1:2))return;
  const groupId=uid();edit(()=>chosen().forEach(o=>{if(ungroup)delete o.groupId;else o.groupId=groupId;}));
}
function arrangeObjects(front){if(!selected.size)return;edit(()=>{const moving=chosen(),others=state.items.filter(o=>!selected.has(o.id));state.items=front?[...others,...moving]:[...moving,...others];});}
function transformSelection(kind,value){
  const b=groupBounds();if(!b)return;
  edit(()=>chosen().forEach(o=>{o.d=kind==='rotate'?rotate(o.d,value,b.cx,b.cy):scale(o.d,kind==='x'?-1:1,kind==='y'?-1:1,b.cx,b.cy);if(kind==='rotate')rotateText(o,value);else transformText(o,kind==='x'?-1:1,0,0,kind==='y'?-1:1);delete o.gear;}));
}
function copiesOf(objects){
  const groups=new Map();return objects.map(o=>{const copy={...structuredClone(o),id:uid(),name:o.name+' kopi',d:move(o.d,5,5)};if(o.groupId){if(!groups.has(o.groupId))groups.set(o.groupId,uid());copy.groupId=groups.get(o.groupId);}return copy;});
}
function copyObjects(){if(!selected.size)return;clipboard=structuredClone(chosen());renderProperties();toast('Figurerne er kopieret. Brug Sæt ind eller Ctrl+V.');}
function pasteObjects(){if(!clipboard.length)return;if(state.items.length+clipboard.length>500)throw Error('Der kan højst være 500 dele i en tegning.');setTool('select');const copies=copiesOf(clipboard);edit(()=>{state.items.push(...copies);selected=new Set(copies.map(o=>o.id));});}
function snapDirection(anchor,p,shift){
  if(!shift)return p;const dx=p.x-anchor.x,dy=p.y-anchor.y,angle=Math.round(Math.atan2(dy,dx)/(Math.PI/4))*Math.PI/4,length=Math.hypot(dx,dy);return {x:anchor.x+Math.cos(angle)*length,y:anchor.y+Math.sin(angle)*length};
}
function setOp(op){
  if(VINYL)op='cut';
  if(!chosen().length)return;
  if(op==='engrave')op=chosen().every(o=>closed(o.d))?'engrave-fill':'engrave-line';
  if(['cut','engrave-line'].includes(op)&&chosen().some(o=>o.image&&!o.image.trace))throw Error('Trace billedet først, før du vælger at følge konturerne.');
  if(op==='engrave-fill'&&chosen().some(o=>!closed(o.d)))throw Error('Udfyldning kræver lukkede figurer. Vælg “Følg stregerne” til åbne streger.');
  edit(()=>chosen().forEach(o=>o.op=op));
}
function cursorPoint(e,snap=false){
  const p=new DOMPoint(e.clientX,e.clientY).matrixTransform(drawing.getScreenCTM().inverse());
  return {x:snap&&$('snap').checked?Math.round(p.x):p.x,y:snap&&$('snap').checked?Math.round(p.y):p.y};
}
function pointerDown(e){
  if(e.button!==0||drag)return;
  const point=cursorPoint(e),id=e.target.closest('[data-id]')?.dataset.id,corner=e.target.dataset.corner;
  $('workspace').focus({preventScroll:true});
  if(tool==='polyline'){
    e.preventDefault();const count=pointPath.length;addPoint(cursorPoint(e,true));
    if(tool==='polyline'&&pointPath.length===count+1){drag={type:'pen',index:count,pointer:e.pointerId};drawing.setPointerCapture(e.pointerId);}return;
  }
  if(tool==='freehand'||tool==='line'){
    if(point.x<0||point.y<0||point.x>state.width||point.y>state.height)return;
    drag={type:tool,start:point,points:[[point.x,point.y]],pointer:e.pointerId};
  }else if(tool==='nodes'&&(e.target.classList.contains('node-dot')||e.target.dataset.handle)&&one()){
    const o=one();activeNode={id:o.id,ci:Number(e.target.dataset.ci),si:Number(e.target.dataset.si)};activeCurve=null;
    startEditDrag({type:e.target.dataset.handle?'handle':'node',...activeNode,which:e.target.dataset.handle,initial:o.d,pointer:e.pointerId,start:point,moved:false});renderSelection();renderNodeTools();
  }else if(corner&&selected.size){
    startEditDrag({type:'scale',corner,start:point,b:groupBounds(),initial:chosen().map(o=>({id:o.id,d:o.d})),pointer:e.pointerId});
  }else if(id){
    if(e.shiftKey){select(id,true);return;}
    if(!selected.has(id))select(id);
    if(tool==='nodes'){
      if(selected.size!==1)select(id);
      const o=one();if(!o)return;
      if(o.image){toast('Brug billedets indstillinger til at lave redigerbare vektorstreger.');return;}
      if(addingNode){addNodeAt(o,point);return;}
      const hit=nearestCurve(o.d,point.x,point.y);if(!hit||hit.distance*screenScale()>12)return;
      activeNode=null;activeCurve={id:o.id,...hit};startEditDrag({type:'bend',id:o.id,hit,initial:o.d,start:point,pointer:e.pointerId,moved:false});renderSelection();renderNodeTools();drawing.setPointerCapture(e.pointerId);e.preventDefault();return;
    }
    startEditDrag({type:'move',start:point,initial:chosen().map(o=>({id:o.id,d:o.d})),pointer:e.pointerId,moved:false});
  }else{
    activeNode=null;activeCurve=null;
    if(tool==='select'){drag={type:'marquee',start:point,end:point,base:e.shiftKey?[...selected]:[],pointer:e.pointerId};if(!e.shiftKey)selected.clear();}
    else{selected.clear();renderProperties();renderSelection();renderNodeTools();return;}
  }
  drawing.setPointerCapture(e.pointerId);e.preventDefault();
}
function pointerMove(e){
  if(tool==='polyline'&&!drag){
    const p=cursorPoint(e,true);pointHover=p.x>=0&&p.y>=0&&p.x<=state.width&&p.y<=state.height?p:null;renderPointPath();return;
  }
  if(!drag||drag.pointer!==e.pointerId)return;
  const p=cursorPoint(e,drag.type!=='freehand');
  if(drag.type==='pen'){
    const anchor=pointPath[drag.index];if(!anchor)return;
    const handle=snapDirection(anchor,cursorPoint(e),e.shiftKey);
    if(Math.hypot(handle.x-anchor.x,handle.y-anchor.y)*screenScale()<3&&!anchor.hout)return;
    anchor.hout={x:handle.x-anchor.x,y:handle.y-anchor.y};anchor.hin=e.altKey?{x:0,y:0}:{x:-anchor.hout.x,y:-anchor.hout.y};pointHover=null;renderPointPath();return;
  }else if(drag.type==='marquee'){
    drag.end=p;const x=Math.min(p.x,drag.start.x),y=Math.min(p.y,drag.start.y),width=Math.abs(p.x-drag.start.x),height=Math.abs(p.y-drag.start.y);
    selectionLayer.replaceChildren(el('rect',{x,y,width,height,class:'marquee'}));return;
  }else if(drag.type==='move'){
    let dx=p.x-drag.start.x,dy=p.y-drag.start.y;if($('snap').checked){dx=Math.round(dx);dy=Math.round(dy);}
    drag.moved=drag.moved||Math.abs(dx)+Math.abs(dy)>.01;
    for(const initial of drag.initial)objectById(initial.id).d=move(initial.d,dx,dy);
  }else if(drag.type==='scale'){
    const b=drag.b,west=drag.corner.includes('w'),north=drag.corner.includes('n'),ax=west?b.x+b.width:b.x,ay=north?b.y+b.height:b.y;
    let sx=Math.max(.05,(p.x-ax)/(west?-Math.max(b.width,.1):Math.max(b.width,.1))),sy=Math.max(.05,(p.y-ay)/(north?-Math.max(b.height,.1):Math.max(b.height,.1)));
    if($('lock-ratio').checked||e.shiftKey||chosen().some(o=>o.text))sx=sy=Math.max(sx,sy);
    for(const initial of drag.initial){const o=objectById(initial.id);o.d=scale(initial.d,sx,sy,ax,ay);}
    drag.scaled=true;
    drag.sx=sx;drag.sy=sy;
  }else if(drag.type==='node'){
    drag.moved=drag.moved||Math.hypot(p.x-drag.start.x,p.y-drag.start.y)>.01;
    objectById(drag.id).d=moveNode(drag.initial,drag.ci,drag.si,p.x,p.y);
  }else if(drag.type==='handle'){
    const anchor=nodes(drag.initial).find(n=>n.ci===drag.ci&&n.si===drag.si),handle=snapDirection(anchor,cursorPoint(e),e.shiftKey);
    drag.moved=drag.moved||Math.hypot(handle.x-drag.start.x,handle.y-drag.start.y)>.01;
    objectById(drag.id).d=setNodeHandle(drag.initial,drag.ci,drag.si,drag.which,handle.x,handle.y,$('handle-link').checked&&!e.altKey);
  }else if(drag.type==='bend'){
    const raw=cursorPoint(e),dx=raw.x-drag.start.x,dy=raw.y-drag.start.y;
    drag.moved=drag.moved||Math.hypot(dx,dy)>.01;
    objectById(drag.id).d=bendCurve(drag.initial,drag.hit,drag.hit.x+dx,drag.hit.y+dy);
  }else{
    const point={x:Math.max(0,Math.min(state.width,p.x)),y:Math.max(0,Math.min(state.height,p.y))};
    if(drag.type==='freehand'&&drag.points.length<3000){const last=drag.points.at(-1);if(Math.hypot(point.x-last[0],point.y-last[1])>.15)drag.points.push([point.x,point.y]);}
    drag.end=point;let d;
    if(drag.type==='line')d=`M${drag.start.x},${drag.start.y}L${point.x},${point.y}`;
    else d='M'+drag.points.map(a=>a.join(',')).join('L');
    $('draft').replaceChildren(el('path',{d,fill:'none',stroke:COLORS[VINYL?'cut':'engrave-line'],'stroke-width':1.8,'vector-effect':'non-scaling-stroke'}));return;
  }
  renderItems();renderSelection();
}
function pointerUp(e,cancel=false){
  if(!drag||drag.pointer!==e.pointerId)return;
  const current=drag;drag=null;
  if(drawing.hasPointerCapture(e.pointerId))drawing.releasePointerCapture(e.pointerId);
  if(current.type==='pen'){if(cancel)pointPath.splice(current.index,1);renderPointPath();return;}
  if(current.type==='marquee'){
    if(!cancel){const a=current.start,b=current.end,x=Math.min(a.x,b.x),y=Math.min(a.y,b.y),right=Math.max(a.x,b.x),bottom=Math.max(a.y,b.y);selected=new Set(current.base);
      if(right-x>.5&&bottom-y>.5)for(const o of state.items){const box=bounds(o.d);if(box.x>=x&&box.y>=y&&box.x+box.width<=right&&box.y+box.height<=bottom){selected.add(o.id);if(o.groupId)state.items.filter(s=>s.groupId===o.groupId).forEach(s=>selected.add(s.id));}}
    }render();return;
  }
  $('draft').replaceChildren();
  if(cancel){if(current.previousHistory){state=JSON.parse(current.original);({undo,redo}=current.previousHistory);}render();return;}
  if(current.type==='freehand'||current.type==='line'){
    if(!current.end||Math.hypot(current.end.x-current.start.x,current.end.y-current.start.y)<.3&&current.points.length<4){render();return;}
    const d=current.type==='line'?`M${round(current.start.x)},${round(current.start.y)}L${round(current.end.x)},${round(current.end.y)}`:freehand(current.points);
    const o=makeItem(current.type==='line'?'Streg':'Min tegning',d,'engrave-line');
    edit(()=>{state.items.push(o);selected=new Set([o.id]);});setTool('select');
  }else{
    if(current.type==='scale'?!current.scaled:!current.moved){
      state=JSON.parse(current.original);({undo,redo}=current.previousHistory);
      // Keep the clicked path in the DOM so the browser can recognize a double-click.
      $('undo').disabled=!undo.length;$('redo').disabled=!redo.length;
      renderProperties();renderSelection();renderNodeTools();return;
    }
    if(['node','handle','bend'].includes(current.type)&&current.moved)clearGenerator(objectById(current.id));
    if(current.scaled)for(const before of current.initial){const o=objectById(before.id);scaledGenerator(o,current.sx,current.sy);}
    render();persist();
  }
}
function duplicate(){if(!selected.size)return;if(state.items.length+selected.size>500)throw Error('Der kan højst være 500 dele i en tegning.');edit(()=>{const copies=copiesOf(chosen());state.items.push(...copies);selected=new Set(copies.map(o=>o.id));});}
function removeSelected(){if(!selected.size)return;edit(()=>{state.items=state.items.filter(o=>!selected.has(o.id));selected.clear();});}
function resizeObject(axis){
  const o=one();if(!o||o.gear)return;
  const value=num('object-'+axis,.5,600),b=bounds(o.d),old=b[axis];if(old<.01)throw Error('En lige streg kan flyttes eller drejes.');
  let sx=axis==='width'?value/old:1,sy=axis==='height'?value/old:1;if($('lock-ratio').checked||o.text)sx=sy=value/old;
  edit(()=>{o.d=scale(o.d,sx,sy,b.x,b.y);scaledGenerator(o,sx,sy);});
}
function updateText(){const o=one();if(!o?.text)return;const value=$('text-value').value.normalize('NFC').trim(),size=num('text-size',2,60),d=changedText(o,value,size);edit(()=>{o.text={...o.text,value,size};o.name=value;o.d=d;});}
function updateGear(){const o=one();if(!o?.gear)return;const teeth=Math.round(num('gear-teeth',17,48)),hole=num('gear-hole',0,12),d=gear(teeth,hole),b=bounds(o.d);edit(()=>{o.gear={teeth,hole};o.name=`Tandhjul · ${teeth} tænder`;o.d=center(d,b.cx,b.cy);});}
async function openImageDialog(bitmap,target=null,name='Billede'){
  setTool('select');imageDraft={...structuredClone(bitmap),name};imageTarget=target;
  $('trace-threshold').value=bitmap.threshold;$('trace-smoothing').value=bitmap.smoothing;
  $('insert-trace').textContent=target?'Opdatér figur':'Indsæt figur';
  $('image-dialog').showModal();await updateTrace();
}
async function updateTrace(){
  if(!imageDraft)return;const revision=++traceRevision,threshold=Number($('trace-threshold').value),smoothing=Number($('trace-smoothing').value);
  $('trace-threshold-value').textContent=threshold;$('insert-trace').disabled=true;$('trace-status').textContent='Finder konturer …';
  $('image-trace-preview').replaceChildren();
  Object.assign(imageDraft,{trace:'',threshold,smoothing});
  try{
    const d=await traceBitmap(imageDraft,threshold,smoothing);if(revision!==traceRevision||!imageDraft)return;
    Object.assign(imageDraft,{trace:d,threshold,smoothing});
    const svg=el('svg',{viewBox:`0 0 ${imageDraft.width} ${imageDraft.height}`,role:'img','aria-label':'Forhåndsvisning af skærekonturer'});if(d)svg.append(el('path',{d,fill:'none',stroke:COLORS.cut,'stroke-width':1.5,'vector-effect':'non-scaling-stroke','fill-rule':'evenodd'}));$('image-trace-preview').replaceChildren(svg);
    $('trace-status').textContent=d?'Konturerne er klar. De røde streger viser, hvor figuren kan skæres.':'Der blev ikke fundet konturer. Flyt skyderen mod højre, eller vælg et andet billede.';
    $('insert-trace').disabled=!d;
  }catch(e){if(e.name==='AbortError'||revision!==traceRevision)return;$('trace-status').textContent=e.message;$('image-trace-preview').replaceChildren();}
}
function insertBitmap(){
  if(!imageDraft?.trace||$('insert-trace').disabled)return;
  const {name,...image}=imageDraft;image.show='trace';
  const previous=imageTarget?objectById(imageTarget):null;
  if(snapshot().length-(previous?JSON.stringify(previous.image).length:0)+JSON.stringify(image).length>1_800_000)throw Error('Tegningen har for mange billeddata. Brug færre eller mindre billeder.');
  if(previous)edit(()=>{previous.image=image;selected=new Set([previous.id]);});
  else{const width=Math.min(70,state.width*.7),height=width*image.height/image.width,ratio=Math.min(1,state.height*.7/height);addItem(makeItem(name,`M0 0H${width*ratio}V${height*ratio}H0Z`,'cut',{image,fillRule:'evenodd'}));}
  $('image-dialog').close();
}
function convertImage(){
  const o=one();if(!o?.image?.trace)return;const d=imageOutline(o);
  edit(()=>{o.d=d;o.fillRule='evenodd';delete o.image;});setTool('nodes');toast('Nu er motivet redigerbare streger. Fortryd gendanner billedet.');
}
function showOffset(){
  if(level<1)return;
  if(!finishPendingPoints())return;
  setTool('select');offsetSources=structuredClone(chosen());
  if(!offsetSources.length)throw Error('Vælg en figur først.');
  $('offset-dialog').showModal();updateOffset();
}
function updateOffset(){
  clearTimeout(offsetTimer);offsetDraft=null;$('insert-offset').disabled=true;
  $('offset-preview').replaceChildren();$('offset-status').classList.remove('error');
  try{
    const distance=num('offset-distance',.1,30),result=offsetOutline(offsetSources,{distance,corners:$('offset-corners').value,holes:$('offset-holes').checked});
    const b=bounds(result.d),padding=Math.max(2,distance*.5),svg=el('svg',{viewBox:`${b.x-padding} ${b.y-padding} ${b.width+2*padding} ${b.height+2*padding}`,role:'img','aria-label':'Grå original med ny rød skærelinje'});
    for(const o of offsetSources){const d=imageOutline(o);svg.append(el('path',{d,fill:closed(d)?'#d5d5cf':'none',stroke:'#777770','stroke-width':1,'fill-rule':o.image?'evenodd':o.fillRule||'nonzero','vector-effect':'non-scaling-stroke'}));}
    svg.append(el('path',{d:result.d,fill:'none',stroke:COLORS.cut,'stroke-width':2,'vector-effect':'non-scaling-stroke'}));$('offset-preview').append(svg);
    if(b.x<-.02||b.y<-.02||b.x+b.width>state.width+.02||b.y+b.height>state.height+.02)throw Error('Skærelinjen går uden for arbejdsområdet. Vælg mindre afstand, eller flyt originalen længere ind på papiret.');
    if(state.items.length>=500)throw Error('Der kan højst være 500 dele i en tegning.');
    offsetDraft={...result,distance};$('insert-offset').disabled=false;
    $('offset-status').textContent=`${result.contours} ${result.contours===1?'lukket kontur':'lukkede konturer'} · ${b.width.toFixed(1)} × ${b.height.toFixed(1)} mm.${result.hasOpenPaths?' Åbne streger får en kant på begge sider.':''}`;
  }catch(e){$('offset-status').classList.add('error');$('offset-status').textContent=e.message;}
}
function insertOffset(){
  if(!offsetDraft||$('insert-offset').disabled)return;
  const {d,fillRule,distance}=offsetDraft;
  const sourceName=offsetSources.length===1?offsetSources[0].name:'Valgte figurer';
  const item=makeItem(`Kant ${distance} mm · ${sourceName}`.slice(0,60),d,'cut',{fillRule});
  edit(()=>{state.items.push(item);selected=new Set([item.id]);});
  $('offset-dialog').close();toast('Skærelinjen er tilføjet som en ny figur.');
}
function renderDrawingPreview(target,source,width,height){
  const svg=new DOMParser().parseFromString(source,'image/svg+xml').documentElement;
  svg.removeAttribute('width');svg.removeAttribute('height');
  svg.setAttribute('viewBox',`-5 -5 ${width+10} ${height+10}`);
  svg.insertBefore(el('rect',{width,height,fill:'#ffffff'}),svg.firstChild);
  for(const node of svg.querySelectorAll('g path'))node.setAttribute('stroke-width',node.closest('g').dataset.operation==='cut'?.4:.5);
  if(VINYL)for(const node of svg.querySelectorAll('path'))node.setAttribute('stroke-width','.5');
  $(target).replaceChildren(document.importNode(svg,true));
}
function showPreview(filter='all'){
  if(!finishPendingPoints())return;
  const previewState=VINYL?{...state,items:state.items.filter(o=>!o.image||o.image.trace)}:state;
  renderDrawingPreview('preview-area',exportDrawing(previewState,filter),state.width,state.height);
  const issues=[...issuesForDrawing(state),...(VINYL?brotherNotes(state):[])];$('preview-issues').replaceChildren();
  renderIssueList($('preview-issues'),issues);
  document.querySelectorAll('[data-preview]').forEach(b=>{b.classList.toggle('active',b.dataset.preview===filter);b.setAttribute('aria-pressed',b.dataset.preview===filter);});
  if(!$('preview-dialog').open)$('preview-dialog').showModal();
}
function boxNumber(id,min,max){return num(id,min,max);}
function boxSettings(){
  if(level!==3)throw Error('Vælg Design og afprøv for at bruge kassegeneratoren.');
  return {level,kind:'abox',x:boxNumber('box-width',20,300),y:boxNumber('box-depth',20,300),h:boxNumber('box-height',20,300),thickness:boxNumber('box-thickness',1.5,10),burn:boxNumber('box-burn',0,1)};
}
async function generateBox(){
  const status=$('box-status'),button=$('box-insert');
  const request=++boxRequest;let settings;
  boxDraft=null;button.disabled=true;$('box-preview').replaceChildren();status.classList.remove('error');status.textContent='Genererer skærelinjer …';
  try{
    settings=boxSettings();
    const response=await fetch('/api/laser/boxes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(settings),signal:AbortSignal.timeout(7000)});
    const data=await response.json();if(request!==boxRequest)return;if(!response.ok)throw Error(data.error||'Kassen kunne ikke genereres.');
    const imported=importSVG(data.svg,{maxWidth:600,maxHeight:600,defaultOperation:'cut'});
    if(!imported.items.length)throw Error('Kassegeneratoren lavede ingen skærelinjer.');
    if(imported.width>LASER_BED.width||imported.height>LASER_BED.height)throw Error(`Kassen er større end laserpladen på ${LASER_BED.width} × ${LASER_BED.height} mm.`);
    const preview={version:1,name:'Kasse fra Boxes.py',width:imported.width,height:imported.height,items:imported.items};
    boxDraft={settings,source:data.svg,result:imported};
    renderDrawingPreview('box-preview',exportSVG(preview),preview.width,preview.height);
    button.disabled=false;status.textContent=`${data.details.generator}: ${data.details.width} × ${data.details.height} mm · ${imported.items.length} dele.`;
  }catch(e){if(request!==boxRequest)return;boxDraft=null;button.disabled=true;$('box-preview').replaceChildren();status.classList.add('error');status.textContent=e.name==='TimeoutError'?'Kassegeneratoren svarede ikke i tide. Prøv mindre mål.':e.message;}
}
function insertBox(){
  if(!boxDraft)return;
  const result=boxDraft.result,items=result.items.map(o=>({...o,name:`Kasse · ${o.name}`.slice(0,60),id:uid()}));
  const ids=items.map(o=>o.id);
  edit(()=>{state.width=Math.max(state.width,result.width);state.height=Math.max(state.height,result.height);state.items.push(...items);selected=new Set(ids);});
  $('box-dialog').close();boxDraft=null;toast('Kassen er indsat som redigerbare skærelinjer.');
}
function saveFile(source,suffix='.svg'){
  const url=URL.createObjectURL(new Blob([source],{type:'image/svg+xml'})),a=document.createElement('a');
  a.href=url;a.download=(state.name.replace(/[<>:"/\\|?*\x00-\x1f]/g,'').trim()||'Min tegning')+suffix;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function saveProject(){
  if(MANAGED){
    if(!window.ElevhubProjectSession){notify('Vent, til projektgemning er klar.',true);return false;}
    return window.ElevhubProjectSession.save();
  }
  if(!finishPendingPoints())return false;
  const source=exportSVG(state,'all',{project:true,workshop:VINYL?'vinyl':'laser',level});
  if(new Blob([source]).size>4_000_000)throw Error('Projektfilen fylder mere end 4 MB. Brug færre eller mindre billeder.');
  saveFile(source,'.projekt.svg');persist();toast('Projektfilen er hentet. Brug Åbn projekt, når du vil arbejde videre.');
  return true;
}
function applyOpenedDrawing(result){
  if(VINYL)result=vinylState(result);
  setTool('select');
  // Keep the current drawing in undo history, including when opening a project
  // automatically routed here from the other workshop.
  edit(()=>{state=result;selected.clear();zoom=1;});setTool('select');
  if(MANAGED){undo=[];redo=[];clipboard=[];}
  if(result.workshop)setLevel(result.level);
  announceProjectChanged();
  notify(result.workshop?'Projekt åbnet. Lag, billeder og redigerbare dele er bevaret.':VINYL?'SVG åbnet som skærelinjer. Tjek størrelse og placering på måtten.':result.items.some(o=>o.op==='undecided')?'SVG åbnet. Vælg Skær eller Gravér for hver del.':'SVG åbnet med de gemte valg.');
}
function openDesign(source){
  const result=importSVG(source,VINYL?{maxHeight:603,defaultOperation:'cut'}:{});
  if(result.workshop&&result.workshop!==(VINYL?'vinyl':'laser')){
    if(!finishPendingPoints())return;
    if(!persist())throw Error('Gem dit nuværende projekt først. Browserkladden kunne ikke gemmes før skift af værksted.');
    try{sessionStorage.setItem(PENDING_PROJECT,source);}catch{throw Error('Projektet kunne ikke flyttes til det rigtige værksted. Vælg værkstedet på forsiden, og åbn filen dér.');}
    location.assign(result.workshop==='vinyl'?'/folie.html':'/laser.html');return;
  }
  applyOpenedDrawing(result);
}
function restorePendingProject(){
  try{
    const source=sessionStorage.getItem(PENDING_PROJECT);if(!source)return;
    const result=importSVG(source,VINYL?{maxHeight:603,defaultOperation:'cut'}:{});
    if(result.workshop!==(VINYL?'vinyl':'laser'))return;
    sessionStorage.removeItem(PENDING_PROJECT);applyOpenedDrawing(result);
  }catch(e){try{sessionStorage.removeItem(PENDING_PROJECT);}catch{}notify(e.message,true);}
}
function download(){
  if(!finishPendingPoints())return;
  const issues=issuesForDrawing(state);if(issues.length){notify(issues.join(' '),true);showPreview();return;}
  saveFile(exportDrawing(state));toast(VINYL?'SVG-filen er hentet. Kopiér den til din USB-nøgle.':'Din SVG til laseren er hentet.');
}
async function inboxStatus(){
  if(VINYL)return;
  try{const r=await fetch('/api/laser/health',{signal:AbortSignal.timeout(4000)});const d=await r.json();inboxAvailable=r.ok&&d.surface==='student'&&d.inbox===true;}catch{inboxAvailable=false;}
  render();
}
function showSend(){
  if(VINYL||sending)return;
  if(!finishPendingPoints())return;
  const issues=issuesForDrawing(state);if(issues.length)throw Error(issues.join(' '));
  // The preview and request use the same snapshot, including on network retry.
  sendDraft={svg:exportSVG(state)};
  renderDrawingPreview('send-preview',sendDraft.svg,state.width,state.height);
  try{const contact=window.ElevhubProjectSession?.naming()||JSON.parse(localStorage.getItem(STORAGE+'-contact')||'{}');$('student-name').value=contact.student||contact.name||'';$('student-class').value=contact.classroom||'';}catch{}
  $('student-project').value=state.name;resetSendDialog();$('send-dialog').showModal();
}
async function submit(e){
  e.preventDefault();if(VINYL||sending||!sendDraft||!$('send-dialog').open)return;
  const payload={elevnavn:$('student-name').value.trim(),klasse:$('student-class').value.trim(),projekt:$('student-project').value.trim(),svg:sendDraft.svg};
  if(!payload.elevnavn||!payload.klasse||!payload.projekt){$('send-status').textContent='Udfyld navn, klasse og projekt.';return;}
  const signature=JSON.stringify(payload);
  try{if(!pendingSubmission)pendingSubmission=JSON.parse(localStorage.getItem(STORAGE+'-submission')||'null');}catch{}
  if(!pendingSubmission||pendingSubmission.signature!==signature)pendingSubmission={signature,requestId:uid()};
  try{localStorage.setItem(STORAGE+'-submission',JSON.stringify(pendingSubmission));localStorage.setItem(STORAGE+'-contact',JSON.stringify({name:payload.elevnavn,classroom:payload.klasse}));}catch{}
  sending=true;$('confirm-send').disabled=true;$('send-status').classList.remove('error');$('send-status').textContent='Sender din tegning …';
  try{
    const response=await fetch('/api/laser/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,requestId:pendingSubmission.requestId}),signal:AbortSignal.timeout(15000)});
    const result=await response.json();if(!response.ok)throw Error(result.error||'Tegningen kunne ikke afleveres.');
    persistReceipt({id:result.id,project:payload.projekt,name:payload.elevnavn,classroom:payload.klasse,submittedAt:new Date().toISOString()});
    sendDraft=null;
    showSendSuccess(`Modtaget hos læreren. Afleveringsnummer ${result.id.slice(0,8).toUpperCase()}.`);
    toast('Tegningen er sendt til læreren.');
  }catch(e){$('send-status').classList.add('error');$('send-status').textContent=(e.name==='TimeoutError'||e instanceof TypeError?'Forbindelsen til læreren blev afbrudt. Prøv igen, eller gem SVG til USB.':e.message);$('confirm-send').disabled=false;}
  finally{sending=false;}
}
function installProjectAdapter(){
  window.ElevhubProjectAdapter={
    type:VINYL?'folie':'laser',
    extension:'.projekt.svg',
    name:()=>$('project-name').value.trim()||state.name,
    signature:()=>JSON.stringify({state,level,pointPath}),
    hasContent:()=>!!(state.items.length||state.learning||pointPath.length),
    setName:name=>{state.name=String(name).slice(0,60);$('project-name').value=state.name;persist();},
    prepareSave:()=>{if(!finishPendingPoints())throw Error('Afslut eller annullér punkttegningen, før projektet gemmes.');},
    reset:async()=>{
      if(sending)throw Error('Vent, til afleveringen er færdig.');
      traceRevision++;cancelTrace();clearTimeout(traceTimer);clearTimeout(offsetTimer);
      state=newState();selected.clear();undo=[];redo=[];clipboard=[];pointPath=[];pointRedo=[];pointHover=null;
      activeNode=null;activeCurve=null;addingNode=false;drag=null;imageDraft=null;imageTarget=null;
      offsetSources=[];offsetDraft=null;boxDraft=null;sendDraft=null;pendingSubmission=null;pendingProjectId=null;
      latestReceipt=null;zoom=1;level=0;
      for(const suffix of ['', '-level', '-contact', '-submission', '-receipt'])localStorage.removeItem(STORAGE+suffix);
      sessionStorage.removeItem(PENDING_PROJECT);
      for(const id of ['student-name','student-class','student-project'])$(id).value='';
      for(const modal of document.querySelectorAll('dialog[open]'))if(modal.id!=='elevhub-project-dialog')modal.close();
      setTool('select');setLevel(0);render();savedJSON=snapshot();
    },
    export:async()=>{
      if(!finishPendingPoints())throw Error('Afslut eller annullér punkttegningen, før projektet gemmes.');
      const source=exportSVG(state,'all',{project:true,workshop:VINYL?'vinyl':'laser',level});
      return new Blob([source],{type:'image/svg+xml'});
    },
    import:async blob=>{if(!blob||typeof blob.text!=='function')throw Error('Projektet kunne ikke læses.');openDesign(await blob.text());return state;}
  };
}
function bind(){
  $('send-dialog').addEventListener('close',()=>{sendDraft=null;});
  $('offset-button').onclick=()=>attempt(showOffset);
  $('box-button').onclick=()=>{$('box-status').textContent='';$('box-status').classList.remove('error');$('box-insert').disabled=true;boxDraft=null;$('box-dialog').showModal();attempt(generateBox);};
  $('box-generate').onclick=()=>attempt(generateBox);
  $('box-insert').onclick=()=>attempt(insertBox);
  $('offset-distance').oninput=()=>{clearTimeout(offsetTimer);offsetDraft=null;$('insert-offset').disabled=true;$('offset-status').textContent='Opdaterer skærelinjen …';offsetTimer=setTimeout(updateOffset,150);};
  $('offset-corners').onchange=$('offset-holes').onchange=updateOffset;
  $('insert-offset').onclick=()=>attempt(insertOffset);
  $('offset-dialog').addEventListener('close',()=>{clearTimeout(offsetTimer);offsetSources=[];offsetDraft=null;});
  $('workshop-home').onclick=async e=>{
    if(MANAGED){e.preventDefault();if(await window.ElevhubProjectSession?.finish())location.assign($('workshop-home').href);return;}
    if(!finishPendingPoints()){e.preventDefault();return;}persist();
  };
  window.addEventListener('elevhub-project-named',e=>{if(e.detail){$('student-name').value=e.detail.student||'';$('student-class').value=e.detail.classroom||'';}});
  if(VINYL){
    $('vinyl-mat').onchange=()=>{const mat=$('vinyl-mat').value;edit(()=>{state.mat=mat;state.width=MATS[mat].width;state.height=MATS[mat].height;zoom=1;});};
    $('vinyl-mirror').onclick=()=>attempt(()=>{if(!finishPendingPoints())return;const objects=state.items,b=groupBounds(objects);if(b)edit(()=>objects.forEach(o=>{o.d=scale(o.d,-1,1,b.cx,b.cy);transformText(o,-1,0,0,1);delete o.gear;}));toast('Tegningen er spejlvendt. Fortryd vender den tilbage.');});
  }
  $('add-image').onclick=()=>$('image-file').click();
  $('image-file').onchange=async()=>{const file=$('image-file').files[0];$('image-file').value='';if(!file)return;try{await openImageDialog(await readBitmap(file),null,file.name.replace(/\.[^.]+$/,'').slice(0,60));}catch(e){notify(e.message,true);}};
  $('edit-image-trace').onclick=()=>{const o=one();if(o?.image)openImageDialog(o.image,o.id,o.name).catch(e=>notify(e.message,true));};
  $('trace-threshold').oninput=()=>{clearTimeout(traceTimer);traceRevision++;cancelTrace();$('trace-threshold-value').textContent=$('trace-threshold').value;$('trace-status').textContent='Finder konturer …';$('insert-trace').disabled=true;traceTimer=setTimeout(updateTrace,180);};
  $('trace-smoothing').onchange=()=>{clearTimeout(traceTimer);updateTrace();};
  $('insert-trace').onclick=()=>attempt(insertBitmap);
  $('image-to-path').onclick=()=>attempt(convertImage);
  $('image-dialog').addEventListener('close',()=>{traceRevision++;clearTimeout(traceTimer);cancelTrace();imageDraft=null;imageTarget=null;});
  document.querySelectorAll('.level-bar [data-level]').forEach(b=>b.addEventListener('click',()=>setLevel(Number(b.dataset.level))));
  document.querySelectorAll('[data-tool]').forEach(b=>b.addEventListener('click',()=>setTool(b.dataset.tool)));
  document.querySelectorAll('[data-add]').forEach(b=>b.addEventListener('click',()=>attempt(()=>addItem(makeItem(({rect:'Firkant',circle:'Cirkel',heart:'Hjerte',star:'Stjerne'})[b.dataset.add],fit(shape(b.dataset.add),state.width*.7,state.height*.7))))));
  $('add-text').addEventListener('click',()=>attempt(()=>{addItem(makeItem('Mit navn',fit(textPath('Mit navn',8),state.width*.8,state.height*.8),'engrave-fill',{text:{value:'Mit navn',size:8}}));$('text-value').focus();$('text-value').select();}));
  if(!VINYL)$('add-gear').addEventListener('click',()=>attempt(()=>addItem(makeItem('Tandhjul · 20 tænder',gear(20,4),'cut',{gear:{teeth:20,hole:4}}))));
  document.querySelectorAll('button[data-op]').forEach(b=>b.addEventListener('click',()=>attempt(()=>setOp(b.dataset.op))));
  $('engrave-mode').addEventListener('change',()=>attempt(()=>setOp($('engrave-mode').value)));
  $('undo').onclick=()=>history(true);$('redo').onclick=()=>history(false);$('duplicate').onclick=duplicate;$('delete').onclick=removeSelected;
  $('point-finish').onclick=()=>attempt(()=>finishPointPath(false));$('point-close').onclick=()=>attempt(()=>finishPointPath(true));$('point-back').onclick=()=>history(true);$('point-cancel').onclick=cancelPointPath;
  $('node-add').onclick=()=>{addingNode=!addingNode;renderNodeTools();};
  $('node-smooth').onclick=()=>attempt(()=>alterNode('smooth'));$('node-corner').onclick=()=>attempt(()=>alterNode('corner'));$('node-delete').onclick=()=>attempt(()=>alterNode('delete'));
  $('curve-straight').onclick=()=>attempt(()=>{const o=one();if(o&&activeCurve?.id===o.id){const d=straightenCurve(o.d,activeCurve);edit(()=>{o.d=d;clearGenerator(o);});}});
  $('contour-close').onclick=()=>attempt(()=>setClosed(true));$('contour-open').onclick=()=>attempt(()=>setClosed(false));$('nodes-done').onclick=()=>setTool('select');
  $('flip-x').onclick=()=>transformSelection('x');$('flip-y').onclick=()=>transformSelection('y');$('rotate-exact').onclick=()=>attempt(()=>transformSelection('rotate',num('rotation-angle',-360,360)));
  $('group-objects').onclick=()=>groupObjects();$('ungroup-objects').onclick=()=>groupObjects(true);$('bring-front').onclick=()=>arrangeObjects(true);$('send-back').onclick=()=>arrangeObjects(false);
  $('copy-objects').onclick=copyObjects;$('paste-objects').onclick=()=>attempt(pasteObjects);
  document.querySelectorAll('[data-align]').forEach(b=>b.onclick=()=>alignSelection(b.dataset.align));document.querySelectorAll('[data-distribute]').forEach(b=>b.onclick=()=>distributeSelection(b.dataset.distribute));
  $('zoom-in').onclick=()=>{zoom=Math.min(3,zoom*1.25);render();};$('zoom-out').onclick=()=>{zoom=Math.max(.5,zoom/1.25);render();};$('zoom-reset').onclick=()=>{zoom=1;render();};
  $('project-name').onchange=()=>edit(()=>state.name=$('project-name').value.trim()||'Min tegning');
  $('object-name').onchange=()=>{const o=one();if(o)edit(()=>o.name=$('object-name').value.trim()||'Figur');};
  for(const axis of ['width','height'])$('object-'+axis).onchange=()=>attempt(()=>resizeObject(axis));
  for(const axis of ['x','y'])$('object-'+axis).onchange=()=>attempt(()=>{const o=one();if(!o)return;const v=num('object-'+axis,-600,600),b=bounds(o.d);edit(()=>o.d=move(o.d,axis==='x'?v-b.x:0,axis==='y'?v-b.y:0));});
  for(const [id,deg] of [['rotate-left',-15],['rotate-right',15]])$(id).onclick=()=>transformSelection('rotate',deg);
  for(const id of ['text-value','text-size'])$(id).onchange=()=>attempt(updateText);
  for(const id of ['gear-teeth','gear-hole'])$(id).onchange=()=>attempt(updateGear);
  for(const axis of ['width','height'])$('board-'+axis).onchange=()=>attempt(()=>{const value=num('board-'+axis,30,600);edit(()=>state[axis]=value);});
  for(const kind of ['unite','subtract','intersect','exclude'])$(kind).onclick=()=>attempt(()=>{const objects=chosen(),d=combine(objects,kind);edit(()=>{const id=uid();state.items=state.items.filter(o=>!selected.has(o.id));state.items.push({id,name:({unite:'Samlet figur',subtract:'Figur med hul',intersect:'Overlap',exclude:'Figur uden overlap'})[kind],d,op:VINYL?'cut':objects.every(o=>o.op===objects[0].op)?objects[0].op:'undecided',fillRule:'nonzero'});selected=new Set([id]);activeNode=null;activeCurve=null;});});
  $('preview-button').onclick=()=>showPreview();$('export-button').onclick=download;$('preview-export').onclick=download;
  $('save-project').onclick=()=>attempt(saveProject);
  $('send-button').onclick=()=>attempt(showSend);$('send-form').onsubmit=submit;
  window.addEventListener('online',inboxStatus);
  $('retry-connection').onclick=async()=>{const b=$('retry-connection');b.disabled=true;await inboxStatus();b.disabled=false;};
  document.querySelectorAll('[data-preview]').forEach(b=>b.onclick=()=>showPreview(b.dataset.preview));
  document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
  $('help-button').onclick=()=>$('help-dialog').showModal();$('new-button').onclick=async()=>{
    if(MANAGED){if(await window.ElevhubProjectSession?.finish())window.ElevhubProjectSession.begin();}
    else $('new-dialog').showModal();
  };
  $('confirm-new').onclick=()=>{setTool('select');edit(()=>{state=newState();selected.clear();zoom=1;});$('new-dialog').close();announceProjectChanged();};
  $('save-and-new').onclick=async()=>{try{if(await saveProject())$('confirm-new').click();}catch(e){notify(e.message,true);}};
  $('confirm-starter').onclick=()=>attempt(()=>applyStarter(pendingProjectId,'replace'));
  $('save-and-starter').onclick=async()=>{try{if(await saveProject())applyStarter(pendingProjectId,'replace');}catch(e){notify(e.message,true);}};
  $('center-on-page').onclick=()=>{const b=groupBounds();if(b)edit(()=>chosen().forEach(o=>o.d=move(o.d,state.width/2-b.cx,state.height/2-b.cy)));};
  $('import-button').onclick=()=>$('svg-file').click();
  $('svg-file').onchange=async()=>{
    const file=$('svg-file').files[0];$('svg-file').value='';if(!file)return;
    if(file.size>4_000_000){notify('Projektfilen må højst fylde 4 MB.',true);return;}
    try{
      openDesign(await file.text());
    }catch(e){notify(e.message,true);}
  };
  $('svg-source-button').onclick=()=>{const o=one();if(!o)return;$('svg-source').value=new XMLSerializer().serializeToString(el('path',{d:o.d,'data-operation':o.op,'fill-rule':o.fillRule}));$('source-dialog').showModal();};
  drawing.addEventListener('pointerdown',e=>attempt(()=>pointerDown(e)));drawing.addEventListener('pointermove',e=>attempt(()=>pointerMove(e)));drawing.addEventListener('pointerup',e=>attempt(()=>pointerUp(e)));drawing.addEventListener('pointercancel',e=>pointerUp(e,true));
  drawing.addEventListener('dblclick',e=>attempt(()=>{if(tool==='nodes'&&one()&&!e.target.closest('.node-dot,.curve-handle')){e.preventDefault();addNodeAt(one(),cursorPoint(e));}}));
  drawing.addEventListener('pointerleave',()=>{if(tool==='polyline'){pointHover=null;renderPointPath();}});
  window.addEventListener('resize',()=>{renderSelection();renderPointPath();});
  document.addEventListener('keydown',e=>{
    // Alt+Left is the pupil toolbar's browser-back shortcut. It must never
    // nudge a selected shape/node or consume native browser navigation.
    if(e.altKey)return;
    const mod=e.ctrlKey||e.metaKey;
    if(mod&&['s','o'].includes(e.key.toLowerCase())){
      e.preventDefault();if(document.querySelector('dialog[open]'))return;
      if(document.activeElement instanceof HTMLElement)document.activeElement.blur();
      if(e.key.toLowerCase()==='s')attempt(saveProject);else if(MANAGED)window.ElevhubProjectSession?.open();else $('svg-file').click();return;
    }
    if(e.target.closest('input,textarea,select,dialog'))return;
    if(tool==='polyline'){
      if(e.key==='Enter'&&!e.target.closest('button')){e.preventDefault();attempt(()=>finishPointPath(false));return;}
      if(e.key==='Escape'){e.preventDefault();cancelPointPath();return;}
      if(e.key==='Backspace'||e.key==='Delete'){e.preventDefault();history(true);return;}
    }
    if(mod&&e.key.toLowerCase()==='z'){e.preventDefault();history(!e.shiftKey);return;}
    if(mod&&e.key.toLowerCase()==='y'){e.preventDefault();history(false);return;}
    if(level>=1&&mod&&e.key.toLowerCase()==='g'){e.preventDefault();groupObjects(e.shiftKey);return;}
    if(mod&&['c','v','x'].includes(e.key.toLowerCase())){
      e.preventDefault();if(e.key.toLowerCase()==='v')attempt(pasteObjects);else{copyObjects();if(e.key.toLowerCase()==='x')removeSelected();}return;
    }
    if(mod&&e.key.toLowerCase()==='a'){e.preventDefault();selected=new Set(state.items.map(o=>o.id));activeNode=null;activeCurve=null;renderProperties();renderSelection();renderNodeTools();return;}
    if(mod&&e.key.toLowerCase()==='d'){e.preventDefault();duplicate();return;}
    if(e.key==='Escape'){selected.clear();setTool('select');renderProperties();return;}
    if(e.key==='Delete'||e.key==='Backspace'){e.preventDefault();if(tool==='nodes'&&activeNode)attempt(()=>alterNode('delete'));else removeSelected();return;}
    if(e.key.startsWith('Arrow')&&tool==='nodes'&&activeNode&&one()?.id===activeNode.id){e.preventDefault();const n=e.shiftKey?10:1,o=one(),p=nodes(o.d).find(p=>p.ci===activeNode.ci&&p.si===activeNode.si);if(p)edit(()=>{o.d=moveNode(o.d,p.ci,p.si,p.x+(e.key==='ArrowLeft'?-n:e.key==='ArrowRight'?n:0),p.y+(e.key==='ArrowUp'?-n:e.key==='ArrowDown'?n:0));clearGenerator(o);});return;}
    if(e.key.startsWith('Arrow')&&selected.size){e.preventDefault();const n=e.shiftKey?10:1;edit(()=>chosen().forEach(o=>o.d=move(o.d,e.key==='ArrowLeft'?-n:e.key==='ArrowRight'?n:0,e.key==='ArrowUp'?-n:e.key==='ArrowDown'?n:0)));}
  });
  window.addEventListener('beforeunload',e=>{if(!MANAGED&&(snapshot()!==savedJSON||pointPath.length)){e.preventDefault();e.returnValue='';}});
}
function restore(){
  try{
    const raw=localStorage.getItem(STORAGE);if(!raw)return false;
    if(raw.length>4_000_000)throw Error('For stor tegning');
    const s=JSON.parse(raw);
    if(s.version!==1||!Array.isArray(s.items)||s.items.length>500||!Number.isFinite(s.width)||!Number.isFinite(s.height)||s.width<30||s.height<30||s.width>600||s.height>(VINYL?603:600))throw Error('Ugyldig tegning');
    if(s.items.some(o=>!validPath(o.d)||(!OPS.includes(o.op)&&o.op!=='skip')||typeof o.name!=='string'||typeof o.id!=='string'||o.image&&(!validBitmap(o.image)||nodes(o.d).length!==4)))throw Error('Ugyldig figur');
    for(const o of s.items){if(o.image)o.image.show='trace';if(o.op==='skip')o.op='undecided';}
    state=VINYL?vinylState(s):s;
    const learning=cleanLearning(state.learning);if(learning&&getProject(learning.id,VINYL))state.learning=learning;else delete state.learning;
    savedJSON=raw;return true;
  }catch{notify('Den gemte tegning kunne ikke åbnes. Du kan åbne en tidligere gemt projektfil eller SVG.',true);return false;}
}
async function start(){
  try{
    if(VINYL)configureVinylUI();
    await loadFont();
    inspiration=(VINYL?installVinylWorkshop:installLaserWorkshop)(VINYL,{
      startProject:(id,mode)=>attempt(()=>requestStarter(id,mode)),
      startGuide:id=>attempt(()=>startLaserGuide(id)),
      guideStep:index=>{const guide=getLaserGuide(state.learning?.id);if(!guide||!state.learning?.guide)return;state.learning.stepIndex=Math.max(0,Math.min(guide.steps.length-1,index));state.learning.completed=false;persist();inspiration.update(state,level);},
      finishGuide:()=>{if(state.learning?.guide){state.learning.completed=true;state.learning.helpHidden=true;persist();inspiration.update(state,level);}},
      selectLevel:setLevel,
      projectAction:action=>{if(action==='box'){setLevel(3);$('box-button').click();}else if(action==='image'){setLevel(Math.max(level,2));$('image-file').click();}},
      toggleHelp:hidden=>{if(state.learning){state.learning.helpHidden=hidden;persist();inspiration?.update(state,level);}},
      reflect:text=>{if(state.learning){state.learning.reflection=String(text).slice(0,1000);persist();}}
    });
    if(!restore()){
      if(VINYL){const value='DIT NAVN',size=16;const o=makeItem(value,center(textPath(value,size),state.width/2,state.height/2),'cut',{text:{value,size}});state.items.push(o);selected=new Set([o.id]);}
      persist();
    }
    restoreReceipt();
    try{level=Math.max(0,Math.min(3,Number(localStorage.getItem(STORAGE+'-level'))||0));}catch{}
    bind();installProjectAdapter();setLevel(level);render();persist();inboxStatus();restorePendingProject();
    installLaserLibrary({notify,vinyl:VINYL,insert:(source,title)=>{
      if(!finishPendingPoints())throw Error('Afslut din punktfigur først.');
      const result=importSVG(source,VINYL?{maxHeight:603,defaultOperation:'cut'}:{}),factor=Math.min(1,state.width*.8/result.width,state.height*.8/result.height);
      result.items=result.items.map(item=>({...item,name:title.slice(0,60),d:move(scale(item.d,factor,factor,0,0),(state.width-result.width*factor)/2,(state.height-result.height*factor)/2)}));
      edit(()=>{selected=new Set(appendLibraryItems(state,result,uid,closed,{vinyl:VINYL}));});
      setTool('select');toast(VINYL?'Motivet er tilføjet som skærelinjer. Tjek mål og placering på måtten.':'Figuren er tilføjet som gravering.');
    }});
  }catch(e){notify(e.message,true);$('export-button').disabled=$('save-project').disabled=true;}
}
start();
