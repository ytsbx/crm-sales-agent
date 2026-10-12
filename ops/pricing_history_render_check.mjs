import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
const APP='http://127.0.0.1:5275', API='http://127.0.0.1:8064', PORT=9540
const OUT=process.argv[2]
const sleep=(ms)=>new Promise(r=>setTimeout(r,ms))
const bin='/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
async function connect(ws){return new Promise((res,rej)=>{const s=new WebSocket(ws);const p=new Map();s.addEventListener('open',()=>res({send:(m,q={})=>new Promise((a,b)=>{const id=Math.floor(Math.random()*1e9);p.set(id,{a,b});s.send(JSON.stringify({id,method:m,params:q}))}),close:()=>s.close()}));s.addEventListener('error',()=>rej(new Error('x')));s.addEventListener('message',e=>{const m=JSON.parse(e.data);const t=p.get(m.id);if(!t)return;p.delete(m.id);m.error?t.b(new Error(JSON.stringify(m.error))):t.a(m.result)})})}
const login=await fetch(`${API}/api/v1/auth/login`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'admin',password:'admin123'})}).then(r=>r.json())
const me=await fetch(`${API}/api/v1/auth/me`,{headers:{Authorization:`Bearer ${login.data.access_token}`}}).then(r=>r.json())
const b=spawn(bin,['--headless=new','--disable-gpu','--no-first-run',`--remote-debugging-port=${PORT}`,`--user-data-dir=${join(tmpdir(),'chk-log-'+Date.now())}`,'about:blank'],{stdio:'ignore'})
const raw=async(c,expr)=>{const r=await c.send('Runtime.evaluate',{expression:expr,returnByValue:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails.exception?.description));return r.result.value}
try{
for(let i=0;i<60;i++){try{if((await fetch(`http://127.0.0.1:${PORT}/json/version`)).ok)break}catch{}await sleep(250)}
const t=await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(APP+'/login')}`,{method:'PUT'}).then(r=>r.json())
const c=await connect(t.webSocketDebuggerUrl)
await c.send('Page.enable');await c.send('Runtime.enable')
await c.send('Emulation.setDeviceMetricsOverride',{width:1600,height:1000,deviceScaleFactor:1,mobile:false})
await c.send('Runtime.evaluate',{expression:`localStorage.setItem('crm-auth', ${JSON.stringify(JSON.stringify({state:{token:login.data.access_token,user:me.data},version:0}))})`})
await c.send('Page.navigate',{url:APP+'/prices?chk='+Date.now()})
for(let i=0;i<80;i++){const p=await c.send('Runtime.evaluate',{expression:'document.body.innerText.trim().length>200',returnByValue:true});if(p.result.value)break;await sleep(300)}
await sleep(2000)
// 变更记录在「核价历史」页签里（默认停在「客户查价」）
console.log('切页签 →', await raw(c,`(() => {
  const tab = Array.from(document.querySelectorAll('.semi-tabs-tab')).find(n => (n.innerText||'').trim() === '核价历史');
  if (!tab) return 'not-found';
  tab.click();
  return 'clicked';
})()`))
await sleep(2500)
mkdirSync(OUT,{recursive:true})
console.log('页面标题/URL：', await raw(c,`JSON.stringify({url:location.pathname+location.search, h1:(document.querySelector('h1,h2,.semi-typography')||{}).innerText})`))
const table = await raw(c,`(() => {
  const rows = Array.from(document.querySelectorAll('.semi-table-row')).map(r => (r.innerText||'').split(String.fromCharCode(10)).map(s=>s.trim()).filter(Boolean));
  return JSON.stringify(rows.slice(0,10));
})()`)
console.log('表格内容：')
for (const r of JSON.parse(table)) console.log('  ', JSON.stringify(r))
const bad = await raw(c,`(() => {
  const text = document.body.innerText;
  return JSON.stringify({
    hasObjectObject: text.includes('[object Object]'),
    rawFieldNames: ['skipped','refreshed','linked:','missing:','price_refresh','quote_link_master','set_items']
      .filter(k => text.includes(k)),
  });
})()`)
console.log('机器话残留检查：', bad)
const shot = await c.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true})
writeFileSync(join(OUT,'价格中心-变更内容-修好之后.png'), Buffer.from(shot.data,'base64'))
console.log('✓ 截图', join(OUT,'价格中心-变更内容-修好之后.png'))
c.close()
} finally { b.kill() }
