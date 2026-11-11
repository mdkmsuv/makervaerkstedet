/* Receive a file only from Elevhub's exact local project shelf, never a URL. */
(()=>{'use strict';if(window.parent===window||new URL(location.href).searchParams.get('elevhub_import')!=='1')return;
 // Older running backends can serve current HTML without the new config route.
 // Preserve only their historical exact project-shelf parents. A modern empty
 // list is authoritative and must never fall back to those defaults.
 const configured=window.ElevhubProjectOrigins;
 const origins=new Set(Array.isArray(configured)?configured:configured===undefined?['http://127.0.0.1:8791','http://localhost:8791']:[]);let request=null,source=null,origin=null,accepted=false;
 const reply=(type,extra={})=>source?.postMessage({type,request,...extra},origin);
 window.addEventListener('message',async e=>{if(e.source!==window.parent||!origins.has(e.origin)||typeof e.data?.request!=='string')return;
 if(e.data.type==='elevhub-file-hello'){if(accepted)return;request=e.data.request;source=e.source;origin=e.origin;reply('elevhub-file-ready');return;}
 if(e.data.type!=='elevhub-file-import'||accepted||e.data.request!==request||e.source!==source||e.origin!==origin)return;
 accepted=true;try{const d=e.data;if(!(d.bytes instanceof ArrayBuffer)||d.bytes.byteLength>50*1024*1024||typeof d.filename!=='string')throw Error('Projektfilen kunne ikke læses.');
 const file=new File([d.bytes],d.filename);const until=Date.now()+15000;while(!window.ElevhubProjectAdapter&&!window.ElevhubImportFile){if(Date.now()>until)throw Error('Værkstedet blev ikke klar. Prøv igen.');await new Promise(r=>setTimeout(r,100));}
 if(window.ElevhubImportFile)await window.ElevhubImportFile(file);else{if(!/\.svg$/i.test(d.filename))throw Error('Vælg en SVG-fil til dette værksted.');await window.ElevhubProjectAdapter.import(file,d.name);}
 reply('elevhub-file-opened');}catch(error){reply('elevhub-file-error',{message:error.message});}});
})();
