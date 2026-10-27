// Isolated file-origin browser test; never touches the pupil's browser or CAD.
const {spawn} = require('node:child_process');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {pathToFileURL} = require('node:url');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../..');
const python = process.env.TEST_PYTHON || path.join(root, '.venv/Scripts/python.exe');
const source = path.join(root, 'linux-elevhub/ansible/roles/kiosk/files/workshop_launcher.py');
const server = spawn(python, ['-u', '-c', `import importlib.util,threading,sys
s=importlib.util.spec_from_file_location('launch',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
server=m.make_server('fixture','t'*43,lambda:True,port=0)
threading.Thread(target=server.serve_forever,daemon=True).start()
server.accepted=0
def acknowledge():
 while True:
  request=server.requests.get()
  server.accepted+=1
  with server.state_lock:
   server.results[request['id']]={'id':request['id'],'state':'completed'}
   server.pending_id=None
   server.pending_action=None
threading.Thread(target=acknowledge,daemon=True).start()
print(server.server_port,flush=True)
sys.stdin.readline()
print(server.accepted,flush=True)
server.shutdown();server.server_close()
`, source], {stdio:['pipe','pipe','inherit']});
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'elevhub-native-3d-'));
let browser;
(async()=>{
  console.log('Starting isolated local launcher');
  const port = await new Promise((resolve,reject)=>{
    server.stdout.once('data', data=>resolve(Number(data.toString().trim())));
    server.once('error', reject);
  });
  console.log('Launcher ready on ephemeral port', port);
  const js = fs.readFileSync(path.join(root,'linux-elevhub/server/elevhub/web/assets/native-3d.js'),'utf8')
    .replaceAll('127.0.0.1:8790', '127.0.0.1:'+port);
  fs.writeFileSync(path.join(temp,'native.js'),js);
  const template = fs.readFileSync(path.join(root,'linux-elevhub/ansible/roles/kiosk/templates/start.html.j2'),'utf8');
  const handling = template.slice(template.indexOf('async function kioskHandling(path)'), template.indexOf("document.querySelectorAll('[data-project-link]')"))
    .replaceAll('127.0.0.1:8790', '127.0.0.1:'+port);
  fs.writeFileSync(path.join(temp,'index.html'),`<!doctype html><meta charset="utf-8">
    <p id="fejl"></p><button id="open" onclick="aabn(event,{id:'3dprint'})">Åbn CaDoodle</button>
    <script>const UI_TOKEN='${'t'.repeat(43)}',COMPUTER='fixture',OFFLINE_APPS=[{id:'3dprint',offline_unavailable:true}];
    let starter=false,offlineMode=true;window.CreativeWorkshops={'3dprint':{steps:[]}};
    async function aabn(){};async function meld(){return {apps:[{id:'3dprint',aaben:false}]}};function tegn(){}
    ${handling}</script>
    <script src="native.js"></script>`);
  browser=await chromium.launch({headless:true, ...(process.env.TEST_CHROMIUM
    ? {executablePath:process.env.TEST_CHROMIUM} : {channel:'chrome'})});
  console.log('Headless Chrome ready');
  const page=await browser.newPage(),errors=[];
  page.setDefaultTimeout(15000);
  page.on('pageerror',error=>errors.push(error.message));
  page.on('console',message=>{if(message.type()==='error')console.log('Browser:',message.text())});
  await page.goto(pathToFileURL(path.join(temp,'index.html')).href);
  console.log('Fixture page loaded');
  console.log('Script state',await page.evaluate(()=>({native:OFFLINE_APPS[0],open:String(aabn).slice(0,80)})));
  await page.locator('#open').click();
  await page.waitForFunction(()=>document.querySelector('#fejl').textContent.includes('Åbner'));
  console.log('Offline launch accepted');
  await page.evaluate(()=>{offlineMode=false});
  await page.locator('#open').click();
  await page.waitForFunction(()=>document.querySelector('#fejl').textContent.includes('lukket'));
  console.log('Teacher closure respected');
  assert.deepEqual(errors,[]);
  const queued=new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>reject(new Error('Fixture did not stop')),5000);
    server.stdout.once('data',data=>{clearTimeout(timeout);resolve(Number(data.toString().trim()))});
  });
  server.stdin.write('\n');assert.equal(await queued,1);
  console.log('Chrome file page: local CORS, offline launch and teacher closure passed');
})().catch(error=>{console.error(error);process.exitCode=1}).finally(async()=>{
  if(browser)await browser.close();server.kill();fs.rmSync(temp,{recursive:true,force:true});
});
