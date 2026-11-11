/* Font paths become bounded planar contours in physical millimetres. */
export class TextGeometryError extends Error {
  constructor(code) { super(code); this.code=code; }
}
export function textContours(font, params) {
  const fail=code=>{throw new TextGeometryError(code);};
  if(!params || typeof params.text!=='string')fail('ugyldig_tekst');
  const text=params.text.normalize('NFC');
  if(!text.trim())fail('tom_tekst');
  if([...text].length>40)fail('for_lang_tekst');
  if(/[\u0000-\u001f\u007f]/.test(text))fail('ugyldig_tekst');
  const {height,depth}=params;
  if(![height,depth].every(x=>Number.isFinite(x)&&x>=0.01&&x<=350))fail('ugyldige_maal');
  if(!font || [...text].some(c=>font.charToGlyph(c).index===0))fail('ugyldig_tekst');
  const path=font.getPath(text,0,0,1,{kerning:true}),box=path.getBoundingBox();
  if(![box.x1,box.x2,box.y1,box.y2].every(Number.isFinite)||box.y2-box.y1<=0)fail('tom_tekst');
  const scale=height/(box.y2-box.y1),cx=(box.x1+box.x2)/2,cy=(box.y1+box.y2)/2;
  const point=(x,y)=>{if(!Number.isFinite(x)||!Number.isFinite(y))fail('ugyldig_tekst');return [(x-cx)*scale,-(y-cy)*scale];};
  const middle=(a,b)=>[(a[0]+b[0])/2,(a[1]+b[1])/2];
  function distance(p,a,b){const dx=b[0]-a[0],dy=b[1]-a[1],length=dx*dx+dy*dy,t=length?Math.max(0,Math.min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/length)):0;return Math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy);}
  let contour=[],points=0;const contours=[];
  function add(p){if(++points>25000)fail('for_detaljeret_tekst');if(!contour.length||Math.hypot(p[0]-contour.at(-1)[0],p[1]-contour.at(-1)[1])>1e-9)contour.push(p);}
  function flush(){if(contour.length>1&&Math.hypot(contour[0][0]-contour.at(-1)[0],contour[0][1]-contour.at(-1)[1])<1e-9)contour.pop();if(contour.length>=3)contours.push(contour);contour=[];}
  function quadratic(a,b,c,level=0){if(distance(b,a,c)<=0.015||level>=14){add(c);return}const ab=middle(a,b),bc=middle(b,c),m=middle(ab,bc);quadratic(a,ab,m,level+1);quadratic(m,bc,c,level+1);}
  function cubic(a,b,c,d,level=0){if(Math.max(distance(b,a,d),distance(c,a,d))<=0.015||level>=14){add(d);return}const ab=middle(a,b),bc=middle(b,c),cd=middle(c,d),abc=middle(ab,bc),bcd=middle(bc,cd),m=middle(abc,bcd);cubic(a,ab,abc,m,level+1);cubic(m,bcd,cd,d,level+1);}
  let current=null;
  for(const command of path.commands){
    if(command.type==='M'){flush();current=point(command.x,command.y);add(current);continue}
    if(command.type==='Z'){flush();current=null;continue}
    if(!current)fail('ugyldig_tekst');
    const end=point(command.x,command.y);
    if(command.type==='L')add(end);
    else if(command.type==='Q')quadratic(current,point(command.x1,command.y1),end);
    else if(command.type==='C')cubic(current,point(command.x1,command.y1),point(command.x2,command.y2),end);
    else fail('ugyldig_tekst');
    current=end;
  }
  // The actual pinned font/parser may omit Z; each next M and EOF closes a contour.
  flush();if(!contours.length)fail('tom_tekst');
  const all=contours.flat(),min=[Infinity,Infinity],max=[-Infinity,-Infinity];
  for(const p of all)for(let axis=0;axis<2;axis++){min[axis]=Math.min(min[axis],p[axis]);max[axis]=Math.max(max[axis],p[axis]);}
  if(max[1]<=min[1])fail('tom_tekst');
  const fit=height/(max[1]-min[1]),center=[(min[0]+max[0])/2,(min[1]+max[1])/2];
  return contours.map(polygon=>polygon.map(p=>[(p[0]-center[0])*fit,(p[1]-center[1])*fit]));
}
