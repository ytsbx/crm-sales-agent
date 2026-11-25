import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
const APP='http://127.0.0.1:5277', API='http://127.0.0.1:8008', PORT=9552, OUT=process.argv[2]
const sleep=(ms)=>new Promise(r=>setTimeout(r,ms))
const bin='/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
async function connect(ws){return new Promise((res,rej)=>{const s=new WebSocket(ws);const p=new Map();s.addEventListener('open',()=>res({send:(m,q={})=>new Promise((a,b)=>{const id=Math.floor(Math.random()*1e9);p.set(id,{a,b});s.send(JSON.stringify({id,method:m,params:q}))}),close:()=>s.close()}));s.addEventListener('error',()=>rej(new Error('ws')));s.addEventListener('message',e=>{const m=JSON.parse(e.data);const t=p.get(m.id);if(!t)return;p.delete(m.id);m.error?t.b(new Error(JSON.stringify(m.error))):t.a(m.result)})})}
const login=await fetch(`${API}/api/v1/auth/login`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'admin',password:'admin123'})}).then(r=>r.json())
const me=await fetch(`${API}/api/v1/auth/me`,{headers:{Authorization:`Bearer ${login.data.access_token}`}}).then(r=>r.json())
const b=spawn(bin,['--headless=new','--disable-gpu','--no-first-run',`--remote-debugging-port=${PORT}`,`--user-data-dir=${join(tmpdir(),'chk-ids-'+Date.now())}`,'about:blank'],{stdio:'ignore'})
const raw=async(c,e)=>{const r=await c.send('Runtime.evaluate',{expression:e,returnByValue:true});if(r.exceptionDetails)throw new Error(r.exceptionDetails.exception?.description||'e');return r.result.value}
try{
for(let i=0;i<60;i++){try{if((await fetch(`http://127.0.0.1:${PORT}/json/version`)).ok)break}catch{}await sleep(250)}
const t=await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(APP+'/login')}`,{method:'PUT'}).then(r=>r.json())
const c=await connect(t.webSocketDebuggerUrl)
await c.send('Page.enable');await c.send('Runtime.enable')
await c.send('Emulation.setDeviceMetricsOverride',{width:1500,height:1000,deviceScaleFactor:1,mobile:false})
await c.send('Runtime.evaluate',{expression:`localStorage.setItem('crm-auth', ${JSON.stringify(JSON.stringify({state:{token:login.data.access_token,user:me.data},version:0}))})`})
for (const [name,path] of [['任务列表','/tasks'],['商机详情','/opportunities'],['报价中心','/quotes']]) {
  await c.send('Page.navigate',{url:APP+path+'?v='+Date.now()})
  for(let i=0;i<70;i++){const p=await c.send('Runtime.evaluate',{expression:'document.body.innerText.trim().length>300',returnByValue:true});if(p.result.value)break;await sleep(280)}
  await sleep(2200)
  const info=await raw(c,`(() => {
    const text = document.body.innerText;
    // 找"编号 N"这种裸编号（N 是纯数字）——那是我们要清掉的形态
    const bare = (text.match(/编号\\s*\\d+/g) || []);
    const withBizNo = (text.match(/(报价|订单|打样单|客户|商机|需求)编号\\s*\\d+/g) || []);
    const hashes = (text.match(/#\\s*\\d+/g) || []);
    return JSON.stringify({裸编号: bare.slice(0,6), 带限定编号: withBizNo.slice(0,6), 井号编号: hashes.slice(0,4)});
  })()`)
  console.log(`${name} ${path} →`, info)
  if (name === '任务列表') {
    mkdirSync(OUT,{recursive:true})
    const shot=await c.send('Page.captureScreenshot',{format:'png'})
    writeFileSync(join(OUT,'任务列表-编号写法.png'), Buffer.from(shot.data,'base64'))
    console.log('  ✓ 截图 任务列表-编号写法.png')
  }
}
c.close()
} finally { b.kill() }
