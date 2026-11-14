import {bounds,center,textPath,transform} from './laser-geometry.js';

// Only the linear part is stored: translation comes from the current outline.
// This keeps moving, aligning and copying text compatible with old projects.
export function transformText(o,a,b,c,d){
  if(!o.text)return;
  const [e,f,g,h]=o.text.matrix||[1,0,0,1];
  o.text.matrix=[a*e+c*f,b*e+d*f,a*g+c*h,b*g+d*h];
}
export function rotateText(o,degrees){
  const angle=degrees*Math.PI/180,cos=Math.cos(angle),sin=Math.sin(angle);
  transformText(o,cos,sin,-sin,cos);
}
export function scaleText(o,sx,sy){
  if(!o.text)return;
  if(Math.abs(sx-sy)<.000001&&sx>0){
    const previous=o.text.size,next=Math.max(2,Math.min(60,previous*sx));
    o.text.size=next;
    const residual=previous*sx/next;
    if(Math.abs(residual-1)>1e-10)transformText(o,residual,0,0,residual);
  }else transformText(o,sx,0,0,sy);
}
export function changedText(o,value,size){
  const b=bounds(o.d),matrix=o.text.matrix||[1,0,0,1];
  return center(transform(textPath(value,size),[...matrix,0,0]),b.cx,b.cy);
}
