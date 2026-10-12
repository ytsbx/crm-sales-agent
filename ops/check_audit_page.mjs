import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
const APP='http://127.0.0.1:5275', API='http://127.0.0.1:8069', PORT=9550, OUT=process.argv[2]
const sleep=(ms)=>new Promise(r=>setTimeout(r,ms))
const bin='/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
async function connect(ws){return new Promise((res,rej)=>{const s=new WebSocket(ws);const p=new Map();s.addEventListener('open',()=>res({send:(m,q={})=>new Promise((a,b)=>{const id=Math.floor(Math.random()*1e9);p.set(id,{a,b});s.send(JSON.stringify({id,method:m,params:q}))}),close:()=>s.close()}));s.addEventListener('error',()=>rej(new Error('x')));s.addEventListener('message',e=>{const m=JSON.parse(e.data);const t=p.get(m.id);if(!t)return;p.delete(m.id);m.error?t.b(new Error(JSON.stringify(m.error))):t.a(m.result)})})}
const login=await fetch(`${API}/api/v1/auth/login`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'admin',password:'admin123'})}).then(r=>r.json())
const me=await fetch(`${API}/api/v1/auth/me`,{headers:{Authorization:`Bearer ${login.data.access_token}`}}).then(r=>r.json())
const b=spawn(bin,['--headless=new','--disable-gpu','--no-first-run',`--remote-debugging-port=${PORT}`,`--user-data-dir=${join(tmpdir(),'chk-audit-'+Date.now())}`,'about:blank'],{stdio:'ignore'})
const raw=async(c,expr)=>{const r=await c.send('Runtime.evaluate',{expression:expr,returnByValue:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails.exception?.description));return r.result.value}
try{
for(let i=0;i<60;i++){try{if((await fetch(`http://127.0.0.1:${PORT}/json/version`)).ok)break}catch{}await sleep(250)}
const t=await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(APP+'/login')}`,{method:'PUT'}).then(r=>r.json())
const c=await connect(t.webSocketDebuggerUrl)
await c.send('Page.enable');await c.send('Runtime.enable')
await c.send('Emulation.setDeviceMetricsOverride',{width:1600,height:1000,deviceScaleFactor:1,mobile:false})
await c.send('Runtime.evaluate',{expression:`localStorage.setItem('crm-auth', ${JSON.stringify(JSON.stringify({state:{token:login.data.access_token,user:me.data},version:0}))})`})
await c.send('Page.navigate',{url:APP+'/settings?tab=audit&v='+Date.now()})
for(let i=0;i<80;i++){const p=await c.send('Runtime.evaluate',{expression:'document.body.innerText.trim().length>200',returnByValue:true});if(p.result.value)break;await sleep(300)}
await sleep(2000)
// 切到审计日志页签
console.log('切页签 →', await raw(c,`(() => {
  const tab = Array.from(document.querySelectorAll('.semi-tabs-tab')).find(n => (n.innerText||'').trim() === '审计日志');
  if (!tab) return 'not-found';
  tab.click(); return 'clicked';
})()`))
await sleep(2500)
const rows = await raw(c,`(() => {
  const out = Array.from(document.querySelectorAll('.semi-table-row')).map(r => {
    const cells = Array.from(r.querySelectorAll('.semi-table-cell')).map(c => (c.innerText||'').trim());
    return cells;
  });
  return JSON.stringify(out.slice(0, 12));
})()`)
console.log('审计表前几行：')
for (const r of JSON.parse(rows)) console.log('  ', JSON.stringify(r))
const machine = await raw(c,`(() => {
  const text = document.body.innerText;
  const bad = [];
  for (const t of ['object Object','price_refresh','quote_link_master','set_items','sku_identity_source','customer_price_rule','business_type','before_data','after_data']) {
    if (text.includes(t)) bad.push(t);
  }
  // 还有：任何 business_type 形态的小写英文（xx_yy）出现在"对象"列
  const typeCells = Array.from(document.querySelectorAll('.semi-table-cell')).map(c=>(c.innerText||'').trim());
  const snake = typeCells.filter(t => /^[a-z]+(_[a-z]+)*\s*#?\d*$/.test(t));
  return JSON.stringify({machineWords: bad, snakeCells: snake.slice(0,5)});
})()`)
console.log('机器话残留检查：', machine)
mkdirSync(OUT,{recursive:true})
const shot = await c.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true})
writeFileSync(join(OUT,'审计日志-说人话之后.png'), Buffer.from(shot.data,'base64'))
console.log('✓ 截图', join(OUT,'审计日志-说人话之后.png'))
// 展开一条"原始数据"
await raw(c,`(() => { const d=document.querySelector('.semi-table-row details summary'); if(d){d.click(); return 1} return 0 })()`)
await sleep(600)
const shot2 = await c.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true})
writeFileSync(join(OUT,'审计日志-展开原始数据.png'), Buffer.from(shot2.data,'base64'))
console.log('✓ 截图', join(OUT,'审计日志-展开原始数据.png'))
c.close()
} finally { b.kill() }
