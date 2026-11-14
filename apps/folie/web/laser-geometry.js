import paper from 'paper/dist/paper-core.js';
import { parse as parseFont } from 'opentype.js';

export const NS = 'http://www.w3.org/2000/svg';
export const INKSCAPE_NS = 'http://www.inkscape.org/namespaces/inkscape';
export const LAYER_NAMES = {cut:'Skæring','engrave-line':'Gravering – streger','engrave-fill':'Gravering – flader',undecided:'Mangler valg'};
export const OPS = ['undecided', 'cut', 'engrave-line', 'engrave-fill'];
export const LABELS = {undecided:'Mangler valg',cut:'Skær', 'engrave-line':'Gravér streg', 'engrave-fill':'Gravér flade'};
export const COLORS = {undecided:'#8a8174',cut:'#c62b2f','engrave-line':'#256b93','engrave-fill':'#256b93'};
const scope = new paper.PaperScope();
scope.setup(document.createElement('canvas'));
scope.settings.insertItems = false;
let font;
export async function loadFont() {
  const response = await fetch('/assets/metrophobic-latin.ttf');
  if (!response.ok) throw new Error('Skrifttypen kunne ikke indlæses. Prøv at genindlæse siden.');
  font = parseFont(await response.arrayBuffer());
}
export const uid = () => {
  if(crypto.randomUUID)return crypto.randomUUID();
  // Local school sites can use HTTP on a LAN, where randomUUID is unavailable.
  const b=crypto.getRandomValues(new Uint8Array(16));b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;
  const h=[...b].map(v=>v.toString(16).padStart(2,'0')).join('');
  return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
};
export const round = n => Math.round(n * 10000) / 10000;
export function el(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [k,v] of Object.entries(attrs)) node.setAttribute(k,String(v));
  if (text !== undefined) node.textContent = text;
  return node;
}
export function path(d) { return new scope.CompoundPath({pathData:d,insert:false}); }
export function bounds(d) {
  const p=path(d), b=p.bounds;
  const result={x:b.x,y:b.y,width:b.width,height:b.height,cx:b.center.x,cy:b.center.y};
  p.remove(); return result;
}
export function closed(d) { const p=path(d); const ok=p.children.length>0&&p.children.every(c=>c.closed);p.remove();return ok; }
export function transform(d, matrix) {const p=path(d);p.transform(new scope.Matrix(...matrix));const out=p.pathData;p.remove();return out;}
export const move=(d,x,y)=>transform(d,[1,0,0,1,x,y]);
export const scale=(d,x,y,cx,cy)=>transform(d,[x,0,0,y,cx*(1-x),cy*(1-y)]);
export function rotate(d,deg,cx,cy) {const p=path(d);p.rotate(deg,new scope.Point(cx,cy));const out=p.pathData;p.remove();return out;}
export function center(d,x,y) {const b=bounds(d);return move(d,x-b.cx,y-b.cy);}
export function validPath(d) {
  if(typeof d!=='string'||d.length>150000||!/^[\s\d.,+eEaAcChHlLmMqQsStTvVzZ-]+$/.test(d)) return false;
  try {const b=bounds(d);return Object.values(b).every(Number.isFinite)&&Math.max(Math.abs(b.x),Math.abs(b.y),b.width,b.height)<100000;}catch{return false;}
}
export function validBitmap(image){
  return image&&typeof image.src==='string'&&image.src.length<=800000&&/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(image.src)&&Number.isInteger(image.width)&&Number.isInteger(image.height)&&image.width>0&&image.height>0&&image.width<=1024&&image.height<=1024&&typeof image.trace==='string'&&(!image.trace||validPath(image.trace))&&['image','trace'].includes(image.show)&&Number.isFinite(image.threshold)&&image.threshold>=1&&image.threshold<=254&&[.5,1,2,4].includes(image.smoothing);
}
export function imageMatrix(o){
  const points=nodes(o.d);if(points.length!==4)throw Error('Billedets placering kunne ikke læses.');
  const [a,b,,c]=points,w=o.image.width,h=o.image.height;
  return [(b.x-a.x)/w,(b.y-a.y)/w,(c.x-a.x)/h,(c.y-a.y)/h,a.x,a.y];
}
export function imageOutline(o){return o.image?o.image.trace?transform(o.image.trace,imageMatrix(o)):null:o.d;}
export function bitmapElement(o,{archive=false}={}){
  const matrix=imageMatrix(o).map(n=>Math.round(n*1e8)/1e8).join(' '),bitmap=o.image;
  const g=el('g',archive?{'data-image':'1','data-id':o.id,'data-operation':o.op,'data-name':o.name,'data-display':'trace','data-threshold':bitmap.threshold,'data-smoothing':bitmap.smoothing,...(o.groupId?{'data-group':o.groupId}:{})}:{});
  if(archive)g.append(el('image',{href:bitmap.src,width:bitmap.width,height:bitmap.height,transform:`matrix(${matrix})`,preserveAspectRatio:'none',display:'none'}));
  if(bitmap.trace)g.append(el('path',{d:bitmap.trace,transform:`matrix(${matrix})`,'fill-rule':'evenodd',fill:o.op==='engrave-fill'?COLORS[o.op]:'none',stroke:o.op==='engrave-fill'?'none':COLORS[o.op],'stroke-width':archive?.3:1.6,'vector-effect':'non-scaling-stroke'}));
  else if(!archive){const b=bounds(o.d);g.append(el('path',{d:o.d,fill:'#f4f4f1',stroke:COLORS.undecided,'stroke-width':1,'stroke-dasharray':'4 3','vector-effect':'non-scaling-stroke'}),el('text',{x:b.cx,y:b.cy,'text-anchor':'middle','font-size':3,fill:'#555'},'Trace billedet'));
  }
  return g;
}
export function shape(kind) {
  let p;
  if(kind==='rect')p=new scope.Path.Rectangle({rectangle:[0,0,45,30],radius:4,insert:false});
  if(kind==='circle')p=new scope.Path.Circle({center:[0,0],radius:16,insert:false});
  if(kind==='star')p=new scope.Path.Star({center:[0,0],points:5,radius1:8,radius2:18,insert:false});
  if(kind==='heart')p=new scope.Path({pathData:'M0 10 C-24 -9 -11 -23 0 -12 C11 -23 24 -9 0 10 Z',insert:false});
  if(!p)throw new Error('Ukendt figur.');
  const d=p.pathData;p.remove();return d;
}
export function textPath(value,size) {
  if(!font)throw new Error('Skrifttypen er ved at blive indlæst.');
  value=String(value).normalize('NFC').trim();
  if(!value||value.length>80)throw new Error('Skriv mellem 1 og 80 tegn.');
  const missing=[...value].filter(c=>!font.hasChar(c));
  if(missing.length)throw new Error('Skrifttypen har ikke disse tegn: '+[...new Set(missing)].join(' '));
  // Font contours are closed even when the parser emits no explicit Z.
  // Disable its size-dependent SVG optimization to preserve small letter details.
  const p=path(font.getPath(value,0,0,size).toPathData({decimalPlaces:4,flipY:false,optimize:false}));
  p.children.forEach(c=>c.closed=true);
  const d=p.pathData;p.remove();return d;
}
export function freehand(points) {
  const p=new scope.Path({segments:points,insert:false});
  p.simplify(.2);const d=p.pathData;p.remove();return d;
}
// Standard 20-degree involute flanks, fixed module 1.5 mm. Root joins are
// simplified radial joins, not a cutter-generated trochoid. No kerf allowance.
export function gear(teeth=20,hole=4) {
  teeth=Math.round(teeth);
  if(teeth<17||teeth>48||hole<0||hole>12)throw new Error('Vælg 17–48 tænder og et hul på 0–12 mm.');
  const m=1.5,r=teeth*m/2,rb=r*Math.cos(Math.PI/9),ra=r+m,rf=r-1.25*m;
  const inv=t=>Math.tan(t)-t,ip=inv(Math.PI/9),step=2*Math.PI/teeth;
  const half=rad=>Math.PI/(2*teeth)+ip-inv(Math.acos(Math.min(1,rb/rad)));
  const point=(rad,a)=>`${round(rad*Math.cos(a))},${round(rad*Math.sin(a))}`;
  const start=Math.max(rb,rf);let d='';
  for(let i=0;i<teeth;i++){
    const a=i*step;
    d+=(i?'L':'M')+point(rf,a-half(start));
    for(let j=0;j<=8;j++){const rad=start+(ra-start)*j/8;d+='L'+point(rad,a-half(rad));}
    d+=`A${ra},${ra} 0 0 1 `+point(ra,a+half(ra));
    for(let j=8;j>=0;j--){const rad=start+(ra-start)*j/8;d+='L'+point(rad,a+half(rad));}
    d+='L'+point(rf,a+half(start));
    d+=`A${rf},${rf} 0 0 1 `+point(rf,(i+1)*step-half(start));
  }
  d+='Z';
  if(hole>0){const h=hole/2;d+=`M${h},0 A${h},${h} 0 1 0 -${h},0 A${h},${h} 0 1 0 ${h},0 Z`;}
  return d;
}
export function combine(items,kind) {
  items=items.map(o=>({...o,d:imageOutline(o)}));
  if(items.some(o=>!o.d))throw new Error('Trace billedet først, før du sætter det sammen med en figur.');
  if(items.length<2||items.some(o=>!closed(o.d)))throw new Error('Vælg mindst to lukkede figurer.');
  if(!['unite','subtract','intersect','exclude'].includes(kind))throw new Error('Ukendt figurværktøj.');
  let result=path(items[0].d);
  result.fillRule=items[0].fillRule||'nonzero';
  for(const o of items.slice(1)){
    const other=path(o.d);other.fillRule=o.fillRule||'nonzero';const next=result[kind](other,{insert:false});
    result.remove();other.remove();result=next;
  }
  const d=result.pathData;result.remove();
  if(!d)throw new Error('Der er ingen figur tilbage. Flyt figurerne og prøv igen.');
  return d;
}
export function nodes(d){const p=path(d);const out=[];p.children.forEach((c,ci)=>c.segments.forEach((s,si)=>out.push({ci,si,x:s.point.x,y:s.point.y,hin:{x:s.point.x+s.handleIn.x,y:s.point.y+s.handleIn.y},hout:{x:s.point.x+s.handleOut.x,y:s.point.y+s.handleOut.y},closed:c.closed,count:c.segments.length})));p.remove();return out;}
export function moveNode(d,ci,si,x,y){const p=path(d);p.children[ci].segments[si].point=new scope.Point(x,y);const out=p.pathData;p.remove();return out;}
export function pointPathData(points,close=false){
  if(!points.length)return '';
  const coord=p=>`${round(p.x)},${round(p.y)}`;
  let d='M'+coord(points[0]);
  const edge=(a,b)=>{
    const out=a.hout||{x:0,y:0},inside=b.hin||{x:0,y:0};
    return Math.hypot(out.x,out.y,inside.x,inside.y)>.00001?`C${coord({x:a.x+out.x,y:a.y+out.y})} ${coord({x:b.x+inside.x,y:b.y+inside.y})} ${coord(b)}`:'L'+coord(b);
  };
  for(let i=1;i<points.length;i++)d+=edge(points[i-1],points[i]);
  if(close){const a=points.at(-1),b=points[0];if(a.hout||b.hin)d+=edge(a,b);d+='Z';}
  return d;
}
function modifyPath(d,fn){const p=path(d);try{fn(p);return p.pathData;}finally{p.remove();}}
export function setNodeHandle(d,ci,si,which,x,y,linked){
  return modifyPath(d,p=>{const s=p.children[ci].segments[si],v=new scope.Point(x,y).subtract(s.point);s[which]=v;if(linked)s[which==='handleIn'?'handleOut':'handleIn']=v.negate();});
}
export function changeNode(d,ci,si,kind){
  return modifyPath(d,p=>{
    const contour=p.children[ci],s=contour.segments[si];
    if(kind==='delete'){
      if(contour.segments.length<=(contour.closed?3:2))throw Error('Behold mindst '+(contour.closed?'tre punkter i en lukket figur.':'to punkter i en åben streg.'));
      s.remove();return;
    }
    if(kind==='corner'){s.handleIn=[0,0];s.handleOut=[0,0];return;}
    const prev=s.previous,next=s.next;
    let direction=(next?next.point:s.point).subtract(prev?prev.point:s.point);
    if(direction.length<.0001)direction=new scope.Point(1,0);
    const incoming=prev?s.point.getDistance(prev.point)/3:0,outgoing=next?s.point.getDistance(next.point)/3:0;
    s.handleIn=direction.normalize(incoming).negate();s.handleOut=direction.normalize(outgoing);
  });
}
export function nearestCurve(d,x,y){
  const p=path(d),point=new scope.Point(x,y);let best=null;
  try{p.children.forEach((c,ci)=>{
    const loc=c.getNearestLocation(point);if(!loc||best&&loc.distance>=best.distance)return;
    let time=loc.time;const curve=loc.curve;
    if(curve.isStraight()){
      const delta=curve.point2.subtract(curve.point1),length2=delta.dot(delta);
      time=length2?Math.max(0,Math.min(1,point.subtract(curve.point1).dot(delta)/length2)):0;
    }
    best={ci,index:loc.index,time,distance:loc.distance,x:loc.point.x,y:loc.point.y,straight:curve.isStraight()};
  });return best;}finally{p.remove();}
}
function linearHandles(curve){
  if(curve.isStraight()){const v=curve.point2.subtract(curve.point1).divide(3);curve.handle1=v;curve.handle2=v.negate();}
}
export function bendCurve(d,hit,x,y){
  return modifyPath(d,p=>{
    const c=p.children[hit.ci].curves[hit.index];linearHandles(c);
    const t=Math.max(.02,Math.min(.98,hit.time)),onCurve=c.getPointAtTime(t);
    // Moving both control points by delta / (B1+B2) moves the grabbed point
    // to the pointer while both endpoints stay fixed.
    const delta=new scope.Point(x,y).subtract(onCurve).divide(3*t*(1-t));
    c.handle1=c.handle1.add(delta);c.handle2=c.handle2.add(delta);
  });
}
export function insertCurveNode(d,hit){
  const p=path(d);
  try{
    const contour=p.children[hit.ci];if(contour.segments.length>=800)throw Error('Denne kontur har allerede 800 punkter.');
    const c=contour.curves[hit.index];linearHandles(c);
    const next=c.divideAtTime(Math.max(.001,Math.min(.999,hit.time)));
    if(!next)throw Error('Klik længere inde på stregen for at tilføje et punkt.');
    return {d:p.pathData,ci:hit.ci,si:next.segment1.index};
  }finally{p.remove();}
}
export function straightenCurve(d,hit){return modifyPath(d,p=>{const c=p.children[hit.ci].curves[hit.index];c.handle1=[0,0];c.handle2=[0,0];});}
export function setContourClosed(d,ci,value){return modifyPath(d,p=>{const c=p.children[ci];if(value&&c.segments.length<3)throw Error('Tilføj mindst tre punkter, før du lukker konturen.');c.closed=value;});}
export function drawingIssues(state) {
  const issues=[];const active=state.items;
  if(!active.length)issues.push('Tilføj mindst én figur, der skal skæres eller graveres.');
  const undecided=active.filter(o=>!['cut','engrave-line','engrave-fill'].includes(o.op));
  if(undecided.length)issues.push(`${undecided.length} ${undecided.length===1?'del mangler':'dele mangler'} et valg: Skær eller Gravér. Slet de dele, du ikke vil have med.`);
  for(const o of active){
    const b=bounds(o.d);
    if(b.x<-.02||b.y<-.02||b.x+b.width>state.width+.02||b.y+b.height>state.height+.02)issues.push(`“${o.name}” ligger uden for papiret.`);
    if(o.op==='engrave-fill'&&!closed(o.d))issues.push(`“${o.name}” er åben. Vælg “Følg stregerne” eller luk figuren.`);
    if(o.image&&!o.image.trace)issues.push(`Trace “${o.name}”, før figuren kan gemmes eller afleveres.`);
  }
  if(active.some(o=>o.image)&&new Blob([exportSVG(state)]).size>2_000_000)issues.push('Tegningen fylder mere end 2 MB. Brug færre eller mindre billeder, før du gemmer eller afleverer.');
  return issues;
}
export function exportSVG(state,filter='all',{project=false,workshop='laser',level=0}={}) {
  const svg=el('svg',{xmlns:NS,version:'1.1',width:state.width+'mm',height:state.height+'mm',viewBox:`0 0 ${state.width} ${state.height}`,'data-laservaerksted':'1'});
  if(project){
    svg.setAttributeNS('http://www.w3.org/2000/xmlns/','xmlns:inkscape',INKSCAPE_NS);
    svg.setAttribute('data-designvaerksted-project','1');svg.setAttribute('data-workshop',workshop);svg.setAttribute('data-level',level);
    if(workshop==='vinyl'&&['standard','long'].includes(state.mat))svg.setAttribute('data-mat',state.mat);
    const learning=cleanLearning(state.learning);
    if(learning)svg.setAttribute('data-learning',JSON.stringify(learning));
  }
  svg.append(el('title',{},state.name));
  svg.append(el('desc',{},project?'Designværksted: projekt med lag, redigeringsdata og originalbilleder. Åbn projektet i værkstedet for at arbejde videre. Brug SVG til maskinen, når designet er færdigt.':'Laserværksted: handlinger er gemt pr. figur. Gravér før skæring. Materialeindstillinger vælges i laserprogrammet.'));
  const operations=project?[...(workshop==='laser'?['engrave-fill','engrave-line']:[]),'cut','undecided']:['engrave-fill','engrave-line','cut'];
  for(const op of operations){
    if(filter==='cut'&&op!=='cut'||filter==='engrave'&&op==='cut')continue;
    const group=el('g',{'data-operation':op,id:op,'aria-label':LABELS[op],fill:op==='engrave-fill'?COLORS[op]:'none',stroke:op==='engrave-fill'?'none':COLORS[op],'stroke-width':op==='engrave-line'?.3:.1});
    if(project){group.setAttributeNS(INKSCAPE_NS,'inkscape:groupmode','layer');group.setAttributeNS(INKSCAPE_NS,'inkscape:label',LAYER_NAMES[op]);}
    for(const o of state.items.filter(o=>o.op===op)){
      if(o.image){const motif=bitmapElement(o,{archive:true});if(project)motif.setAttribute('data-order',state.items.indexOf(o));group.append(motif);continue;}
      const attrs={d:o.d,'fill-rule':o.fillRule||'nonzero','data-operation':op,'data-name':o.name};
      if(project){attrs['data-id']=o.id;attrs['data-order']=state.items.indexOf(o);}
      if(o.text)attrs['data-text']=JSON.stringify(o.text);
      if(o.gear)attrs['data-gear']=JSON.stringify(o.gear);
      if(o.groupId)attrs['data-group']=o.groupId;
      group.append(el('path',attrs));
    }
    if(group.children.length||project)svg.append(group);
  }
  return new XMLSerializer().serializeToString(svg);
}

const SHAPES=new Set(['path','rect','circle','ellipse','line','polyline','polygon']);
export function cleanLearning(data){
  if(!data||data.version!==1||typeof data.id!=='string'||!/^[a-z0-9-]{1,80}$/.test(data.id))return undefined;
  const learning={id:data.id,version:1};
  if(data.guide===true){
    learning.guide=true;
    learning.stepIndex=Number.isInteger(data.stepIndex)?Math.max(0,Math.min(100,data.stepIndex)):0;
    learning.completed=data.completed===true;
  }
  if(typeof data.helpHidden==='boolean')learning.helpHidden=data.helpHidden;
  if(typeof data.reflection==='string')learning.reflection=data.reflection.slice(0,1000);
  if(typeof data.frameId==='string'&&/^[a-zA-Z0-9-]{1,80}$/.test(data.frameId))learning.frameId=data.frameId;
  if(Array.isArray(data.contentIds))learning.contentIds=data.contentIds.filter(id=>typeof id==='string'&&/^[a-zA-Z0-9-]{1,80}$/.test(id)).slice(0,500);
  return learning;
}
function cleanText(data){
  if(!data||typeof data.value!=='string'||data.value.length>80||!Number.isFinite(data.size)||data.size<2||data.size>60)return undefined;
  const text={value:data.value,size:data.size};
  if(data.matrix!==undefined){
    if(!Array.isArray(data.matrix)||data.matrix.length!==4||!data.matrix.every(n=>Number.isFinite(n)&&Math.abs(n)<=100000))return undefined;
    const [a,b,c,d]=data.matrix;if(Math.abs(a*d-b*c)<1e-12)return undefined;
    text.matrix=[...data.matrix];
  }
  return text;
}
const GEOMETRY=new Set(['d','x','y','width','height','rx','ry','r','cx','cy','x1','y1','x2','y2','points','transform']);
function physicalMm(value){const match=String(value||'').match(/^\s*(\d*\.?\d+)\s*(mm|cm|in|pt|px)?\s*$/);return match?Number(match[1])*({mm:1,cm:10,in:25.4,pt:25.4/72,px:25.4/96}[match[2]||'px']):null;}
export function importSVG(source,{maxWidth=600,maxHeight=600,defaultOperation='undecided'}={}) {
  if(source.length>4_000_000)throw new Error('Projektfilen må højst fylde 4 MB.');
  if(/<!ENTITY|<\?xml-stylesheet/i.test(source))throw new Error('Filen bruger eksternt indhold. Gem en almindelig SVG.');
  const doc=new DOMParser().parseFromString(source,'image/svg+xml'),root=doc.documentElement;
  if(root.localName!=='svg'||doc.querySelector('parsererror'))throw new Error('Filen kunne ikke læses som SVG.');
  const own=root.getAttribute('data-laservaerksted')==='1',project=own&&root.hasAttribute('data-designvaerksted-project');
  if(project&&root.getAttribute('data-designvaerksted-project')!=='1')throw Error('Denne projektversion understøttes ikke. Åbn filen i en nyere version af Designværkstedet.');
  const workshop=project?root.getAttribute('data-workshop'):null;
  if(project&&!['laser','vinyl'].includes(workshop))throw Error('Projektfilen mangler et gyldigt værksted.');
  if(!project&&source.length>2_000_000)throw Error('SVG-filen må højst fylde 2 MB.');
  if(project)maxHeight=workshop==='vinyl'?603:600;
  const bitmaps=new Map();
  if(root.getAttribute('data-laservaerksted')==='1')for(const motif of [...root.querySelectorAll('g[data-image="1"]')]){
    const children=[...motif.children],image=children.find(n=>n.localName==='image'),trace=children.find(n=>n.localName==='path');
    if(!image||children.length>(trace?2:1)||children.some(n=>!['image','path'].includes(n.localName)))throw Error('Billedet i SVG-filen kunne ikke læses.');
    const matrix=(image.getAttribute('transform')||'').match(/^matrix\(([^)]+)\)$/)?.[1].trim().split(/[\s,]+/).map(Number);
    const bitmap={src:image.getAttribute('href'),width:Number(image.getAttribute('width')),height:Number(image.getAttribute('height')),trace:trace?.getAttribute('d')||'',show:motif.getAttribute('data-display')||'image',threshold:Number(motif.getAttribute('data-threshold')||150),smoothing:Number(motif.getAttribute('data-smoothing')||1)};
    if(!validBitmap(bitmap)||!matrix||matrix.length!==6||!matrix.every(n=>Number.isFinite(n)&&Math.abs(n)<100000)||Math.abs(matrix[0]*matrix[3]-matrix[1]*matrix[2])<1e-12)throw Error('SVG-billedet har ugyldige data eller mål.');
    bitmap.show='trace';
    const key=uid(),frame=transform(`M0 0H${bitmap.width}V${bitmap.height}H0Z`,matrix),replacement=el('path',{d:frame,'data-bitmap-key':key,'data-name':motif.getAttribute('data-name')||'Billede','data-operation':motif.getAttribute('data-operation')||'undecided','data-group':motif.getAttribute('data-group')||''});
    for(const key of ['data-id','data-order'])if(motif.hasAttribute(key))replacement.setAttribute(key,motif.getAttribute(key));
    if(motif.hasAttribute('transform'))replacement.setAttribute('transform',motif.getAttribute('transform'));
    bitmaps.set(key,bitmap);motif.replaceWith(replacement);
  }
  if(doc.querySelectorAll('*').length>3000)throw new Error('Tegningen har for mange elementer. Vælg en enklere SVG.');
  const allowed=new Set([...SHAPES,'svg','g','metadata','title','desc','defs']);
  for(const n of doc.querySelectorAll('*')){
    if(n.closest('metadata'))continue;
    if(!allowed.has(n.localName)){
      if(n.localName==='text'||n.localName==='tspan')throw new Error('Denne SVG indeholder redigerbar tekst. Omdan teksten til kurver i tegneprogrammet først. Tekst lavet her gemmes automatisk som kurver.');
      throw new Error(`SVG med “${n.localName}” understøttes ikke endnu. Brug en SVG med almindelige vektorfigurer.`);
    }
    for(const a of n.attributes){
      if(/^on/i.test(a.name)||/href$/i.test(a.name)||/url\s*\(|javascript:|@import/i.test(a.value))throw new Error('SVG med scripts, henvisninger eller effekter kan ikke åbnes.');
      if(['clip-path','mask','filter','class'].includes(a.name)&&a.value.trim()&&a.value!=='none')throw new Error('SVG med masker, klipning eller CSS-klasser understøttes ikke endnu.');
    }
    if(n.localName==='svg'&&n!==root)throw new Error('Indlejrede SVG-arbejdsområder understøttes ikke endnu.');
    if(n.localName==='defs'&&n.children.length)throw new Error('SVG med genbrugte symboler eller effekter understøttes ikke endnu.');
  }
  let vb=(root.getAttribute('viewBox')||'').trim().split(/[\s,]+/).map(Number);
  const pw=physicalMm(root.getAttribute('width')),ph=physicalMm(root.getAttribute('height'));
  if(vb.length!==4||!vb.every(Number.isFinite)||vb[2]<=0||vb[3]<=0){
    if(!pw||!ph)throw new Error('SVG-filen mangler en gyldig størrelse eller viewBox.');
    vb=[0,0,pw*96/25.4,ph*96/25.4];
  }
  const width=pw||vb[2]*25.4/96,height=ph||vb[3]*25.4/96;
  if(width<1||height<1||width>maxWidth||height>maxHeight)throw new Error(`SVG-arbejdsområdet må højst være ${maxWidth} × ${maxHeight} mm.`);
  // Respect the root viewport's default xMidYMid meet, including letterboxing.
  let sx=width/vb[2],sy=height/vb[3],ox=0,oy=0;
  const par=root.getAttribute('preserveAspectRatio')||'xMidYMid meet';
  if(!par.includes('none')){
    if(par.includes('slice'))throw new Error('SVG med beskåret viewBox understøttes ikke endnu.');
    sx=sy=Math.min(sx,sy);ox=(width-vb[2]*sx)*(par.includes('xMin')?0:par.includes('xMax')?1:.5);oy=(height-vb[3]*sy)*(par.includes('YMin')?0:par.includes('YMax')?1:.5);
  }
  const base=new scope.Matrix(sx,0,0,sy,ox-vb[0]*sx,oy-vb[1]*sy),items=[];
  const orders=new Map(),ids=new Set();
  function walk(node,ancestorTransforms=[],inherited={}){
    const style={...inherited};
    for(const key of ['fill','stroke','fill-rule','display','visibility','data-operation'])if(node.hasAttribute(key))style[key]=node.getAttribute(key);
    const inline=node.getAttribute('style');
    if(inline)for(const declaration of inline.split(';')){
      const [key,...parts]=declaration.split(':');const k=key.trim(),v=parts.join(':').trim();
      if(!k)continue;
      if(!['fill','stroke','fill-rule','stroke-width','stroke-linecap','stroke-linejoin','stroke-miterlimit','stroke-dasharray','stroke-dashoffset','opacity','fill-opacity','stroke-opacity','display','visibility','mix-blend-mode'].includes(k))throw new Error(`SVG-egenskaben “${k}” understøttes ikke endnu.`);
      if(k==='stroke-dasharray'&&v!=='none'&&v!=='')throw new Error('Stiplede SVG-streger skal først omdannes til kurver.');
      if(k==='mix-blend-mode'&&v!=='normal')throw new Error('SVG med blandingseffekter understøttes ikke endnu.');
      style[k]=v;
    }
    // Recover the hidden layer from older projects. Its former operation is
    // deliberately unrecognised below, so the pupil must choose or delete it.
    if((style.display==='none'||style.visibility==='hidden')&&!(project&&style['data-operation']==='skip'))return;
    const transforms=[...ancestorTransforms];if(node.hasAttribute('transform'))transforms.push(node.getAttribute('transform'));
    if(SHAPES.has(node.localName)){
      if(items.length>=500)throw new Error('Der må højst være 500 figurer i en SVG.');
      const clean=el(node.localName);
      for(const a of node.attributes)if(GEOMETRY.has(a.name)&&a.name!=='transform'){
        if(a.value.length>150000)throw new Error('En af figurerne er for kompleks.');
        clean.setAttribute(a.name,a.value);
      }
      let wrapped=clean;
      for(const tr of transforms.toReversed()){if(!/^[\w\s.,()+eE-]+$/.test(tr))throw new Error('Ugyldig SVG-transformation.');const g=el('g',{transform:tr});g.append(wrapped);wrapped=g;}
      const svg=el('svg',{xmlns:NS});svg.append(wrapped);
      const imported=scope.project.importSVG(svg,{insert:false,expandShapes:true,applyMatrix:true});
      let result='';
      const collect=item=>{
        if(item.className==='Path'||item.className==='CompoundPath'){
          const clone=item.clone({insert:false}),mat=item.globalMatrix.clone();clone.remove();clone.matrix.reset();clone.transform(mat);clone.transform(base);result+=clone.pathData+' ';clone.remove();
        }else if(item.children)item.children.forEach(collect);
      };
      collect(imported);imported.remove();
      if(!validPath(result))throw new Error('En figur har ugyldige koordinater.');
      const op=own?(OPS.includes(style['data-operation'])?style['data-operation']:'undecided'):defaultOperation==='cut'?'cut':'undecided';
      const item={id:uid(),name:node.getAttribute('data-name')||node.getAttribute('id')||`Figur ${items.length+1}`,op,d:result.trim(),fillRule:style['fill-rule']==='evenodd'?'evenodd':'nonzero'};
      if(project){
        const id=node.getAttribute('data-id');if(/^[a-zA-Z0-9-]{1,80}$/.test(id||'')&&!ids.has(id))item.id=id;ids.add(item.id);
        const order=Number(node.getAttribute('data-order'));orders.set(item,Number.isInteger(order)&&order>=0&&order<500?order:items.length);
      }
      if(bitmaps.has(node.getAttribute('data-bitmap-key')))item.image=bitmaps.get(node.getAttribute('data-bitmap-key'));
      if(own&&/^[a-zA-Z0-9-]{1,80}$/.test(node.getAttribute('data-group')||''))item.groupId=node.getAttribute('data-group');
      // Exported outlines remain the geometry of record. Editable metadata is optional.
      if(own)for(const key of ['text','gear'])try{const data=JSON.parse(node.getAttribute('data-'+key));if(key==='text'){const text=cleanText(data);if(text)item.text=text;}if(key==='gear'&&data&&data.teeth>=17&&data.teeth<=48&&data.hole>=0&&data.hole<=12)item.gear=data;}catch{}
      items.push(item);
    }else if(node.localName==='g'||node.localName==='svg')for(const child of node.children)walk(child,transforms,style);
  }
  walk(root);
  if(!items.length&&!project)throw new Error('SVG-filen indeholder ingen synlige vektorfigurer.');
  if(project)items.sort((a,b)=>orders.get(a)-orders.get(b));
  const result={version:1,name:root.querySelector(':scope > title')?.textContent?.slice(0,60)||'Min SVG',width:round(Math.max(width,30)),height:round(Math.max(height,30)),items};
  if(project){result.workshop=workshop;result.level=Math.max(0,Math.min(3,Math.floor(Number(root.getAttribute('data-level')))||0));if(['standard','long'].includes(root.getAttribute('data-mat')))result.mat=root.getAttribute('data-mat');}
  if(project)try{const learning=cleanLearning(JSON.parse(root.getAttribute('data-learning')));if(learning)result.learning=learning;}catch{}
  return result;
}
