import {validPath} from './laser-geometry.js';

export async function loadImage(src){
  const image=new Image();image.src=src;
  try{await image.decode();}catch{throw Error('Billedet kunne ikke læses. Vælg en PNG, JPG eller WebP.');}
  return image;
}
export async function readBitmap(file){
  if(!['image/png','image/jpeg','image/webp'].includes(file.type))throw Error('Vælg et billede som PNG, JPG eller WebP. SVG åbnes med Åbn SVG.');
  if(file.size>10_000_000)throw Error('Billedet må højst fylde 10 MB.');
  const url=URL.createObjectURL(file);
  try{
    const image=await loadImage(url);
    if(image.naturalWidth*image.naturalHeight>30_000_000)throw Error('Billedet er for stort. Gem det i mindre opløsning først.');
    const canvas=document.createElement('canvas');let ratio=Math.min(1,1024/Math.max(image.naturalWidth,image.naturalHeight)),src;
    do{
      canvas.width=Math.max(1,Math.round(image.naturalWidth*ratio));canvas.height=Math.max(1,Math.round(image.naturalHeight*ratio));
      const ctx=canvas.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(image,0,0,canvas.width,canvas.height);
      src=canvas.toDataURL('image/png');ratio*=.8;
    }while(src.length>700000&&Math.max(canvas.width,canvas.height)>100);
    if(src.length>800000)throw Error('Billedet er for detaljeret. Gem en mindre udgave først.');
    return {src,width:canvas.width,height:canvas.height,trace:'',threshold:150,smoothing:1,show:'trace'};
  }finally{URL.revokeObjectURL(url);}
}

let worker,sequence=0,pending;
export function cancelTrace(reason=new DOMException('Tracing afbrudt','AbortError')){sequence++;if(worker)worker.terminate();worker=null;if(pending){pending.reject(reason);pending=null;}}
export async function traceBitmap(bitmap,threshold,smoothing){
  cancelTrace();const current=++sequence,image=await loadImage(bitmap.src);
  if(current!==sequence)throw new DOMException('Tracing afbrudt','AbortError');
  const canvas=document.createElement('canvas');canvas.width=bitmap.width;canvas.height=bitmap.height;
  const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(image,0,0);
  const pixels=ctx.getImageData(0,0,canvas.width,canvas.height).data.buffer;
  const svg=await new Promise((resolve,reject)=>{
    pending={reject};worker=new Worker('/vendor/laser-trace-worker.js');
    const timer=setTimeout(()=>cancelTrace(Error('Tracing tog for lang tid. Vælg et enklere eller mindre billede.')),20000);
    const finish=()=>{clearTimeout(timer);worker?.terminate();worker=null;pending=null;};
    pending.reject=e=>{clearTimeout(timer);reject(e);};
    worker.onmessage=({data})=>{if(data.id!==current)return;finish();data.error?reject(Error(data.error)):resolve(data.svg);};
    worker.onerror=()=>{finish();reject(Error('Tracing kunne ikke startes. Genindlæs siden og prøv igen.'));};
    worker.postMessage({id:current,pixels,width:canvas.width,height:canvas.height,threshold,smoothing},[pixels]);
  });
  const doc=new DOMParser().parseFromString(svg,'image/svg+xml');
  const d=[...doc.querySelectorAll('path')].map(p=>p.getAttribute('d')).join(' ');
  if(!d)return '';
  if(!validPath(d)||(d.match(/[MLCQ]/gi)||[]).length>6000)throw Error('Der er for mange små detaljer. Vælg mere udglatning eller et enklere billede.');
  return d;
}
