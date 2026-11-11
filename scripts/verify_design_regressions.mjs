// Start the real student/teacher HTTP surfaces with a temporary local queue.
import {spawn} from 'node:child_process';
import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';
import {createServer} from 'node:net';
import path from 'node:path';
const root=process.cwd();
await mkdir('.runtime',{recursive:true});
const folder=await mkdtemp(path.join(root,'.runtime','svg-regression-'));
async function freePort(){const s=createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const port=s.address().port;await new Promise(r=>s.close(r));return port;}
const student=await freePort();let teacher=await freePort();while(teacher===student)teacher=await freePort();
const python=process.env.WORKSHOP_PYTHON||path.join(root,'.venv','Scripts','python.exe');
const server=spawn(python,['-m','app.laser_server','--port',String(student),'--teacher-port',String(teacher),'--data',folder],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
let logs='';server.stdout.on('data',b=>logs+=b);server.stderr.on('data',b=>logs+=b);
const env={...process.env,WORKSHOP_BASE:`http://127.0.0.1:${student}`,WORKSHOP_TEACHER_BASE:`http://127.0.0.1:${teacher}`};
try{
  let ready=false;for(let i=0;i<60;i++){try{const r=await fetch(env.WORKSHOP_BASE+'/api/laser/health');if(r.ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}
  if(!ready)throw Error('Local test server did not start');
  const scripts=process.argv.slice(2);
  for(const name of scripts.length?scripts:['verify_workshop_projects.mjs','verify_laser_superuser.mjs','verify_laser_point_tool.mjs','verify_brother_workshop.mjs','verify_design_box.mjs']){
    const child=spawn(process.execPath,['scripts/'+name],{cwd:root,windowsHide:true,env,stdio:'inherit'});
    const code=await new Promise(r=>child.on('exit',r));if(code!==0)throw Error(name+' failed');
  }
  await mkdir('output/svg-progression',{recursive:true});
  for(const name of ['workshop-project-results.json','laser-superuser-results.json','laser-point-results.json','brother-workshop-results.json']){
    try{await writeFile('output/svg-progression/'+name,await readFile('.runtime/'+name));}catch{}
  }
  console.log('PASS existing project, geometry, point-tool and Brother regressions against isolated real HTTP server');
}finally{
  server.kill();await writeFile(path.join(folder,'http-test.log'),logs);
}
