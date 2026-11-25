import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
const APP='http://127.0.0.1:5281', API='http://127.0.0.1:8081', PORT=9565, OUT=process.argv[2]
const sleep=(ms)=>new Promise(r=>setTimeout(r,ms))
const bin='/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
async function connect(ws){return new Promise((res,rej)=>{const s=new WebSocket(ws);const p=new Map();s.addEventListener('open',()=>res({send:(m,q={})=>new Promise((a,b)=>{const id=Math.floor(Math.random()*1e9);p.set(id,{a,b});s.send(JSON.stringify({id,method:m,params:q}))}),close:()=>s.close()}));s.addEventListener('error',()=>rej(new Error('ws')));s.addEventListener('message',e=>{const m=JSON.parse(e.data);const t=p.get(m.id);if(!t)return;p.delete(m.id);m.error?t.b(new Error(JSON.stringify(m.error))):t.a(m.result)})})}
const call=async(m,p,tok,body)=>{const r=await fetch(API+'/api/v1'+p,{method:m,headers:{'Content-Type':'application/json',...(tok?{Authorization:'Bearer '+tok}:{})},body:body?JSON.stringify(body):undefined});return r.json()}
const admin=(await call('POST','/auth/login',null,{username:'admin',password:'admin123'})).data.access_token
async function setSample(on){
  const rs=await call('GET','/roles?page=1&page_size=20',admin)
  const sp=(rs.data.items ?? rs.data).find(r=>r.code==='salesperson')
  const codes=on ? [...new Set([...sp.permission_codes,'sample:view','sample:manage'])].sort()
                : sp.permission_codes.filter(c=>!c.startsWith('sample:'))
  await call('PUT',`/roles/${sp.id}/permissions`,admin,{permission_codes:codes})
  return codes.length
}
console.log('前置：恢复 sample →', await setSample(true), '条')
const login=(await call('POST','/auth/login',null,{username:'zhangsan',password:'123456'})).data
const me=await call('GET','/auth/me',login.access_token)
const b=spawn(bin,['--headless=new','--disable-gpu','--no-first-run',`--remote-debugging-port=${PORT}`,`--user-data-dir=${join(tmpdir(),'rs-'+Date.now())}`,'about:blank'],{stdio:'ignore'})
const raw=async(c,e)=>{const r=await c.send('Runtime.evaluate',{expression:e,returnByValue:true});if(r.exceptionDetails)throw new Error(r.exceptionDetails.exception?.description||'e');return r.result.value}
const st=async(c)=>JSON.parse(await raw(c,`(() => {
  const u=JSON.parse(localStorage.getItem('crm-auth')).state.user
  const item=[...document.querySelectorAll('.nav-link')].find(n=>n.innerText.includes('样品管理'))
  return JSON.stringify({perms:u.permissions.length, sampleInPerms:u.permissions.some(p=>p.startsWith('sample:')),
    menuShown:!!item, denied:item?item.classList.contains('nav-link-denied'):null})
})()`))
async function load(c){ await c.send('Page.navigate',{url:APP+'/workbench?v='+Date.now()})
  for(let i=0;i<60;i++){const p=await c.send('Runtime.evaluate',{expression:'document.body.innerText.includes("工作台")',returnByValue:true});if(p.result.value)break;await sleep(300)}
  await sleep(2500) }
try{
for(let i=0;i<60;i++){try{if((await fetch(`http://127.0.0.1:${PORT}/json/version`)).ok)break}catch{}await sleep(250)}
const t=await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(APP+'/login')}`,{method:'PUT'}).then(r=>r.json())
const c=await connect(t.webSocketDebuggerUrl)
await c.send('Page.enable');await c.send('Runtime.enable')
await c.send('Emulation.setDeviceMetricsOverride',{width:1500,height:1000,deviceScaleFactor:1,mobile:false})
await c.send('Runtime.evaluate',{expression:`localStorage.setItem('crm-auth', ${JSON.stringify(JSON.stringify({state:{token:login.access_token,user:me.data},version:0}))})`})
await load(c)
console.log('① 首屏（服务端此刻给 '+me.data.permissions.length+' 条）→', JSON.stringify(await st(c)))
mkdirSync(OUT,{recursive:true})
let shot=await c.send('Page.captureScreenshot',{format:'png'}); writeFileSync(join(OUT,'权限同步-1-有权限时.png'), Buffer.from(shot.data,'base64'))

console.log('· 管理员摘掉 sample:* →', await setSample(false), '条（页面**不刷新**）')
console.log('② 摘掉后**不刷新**：本地仍是旧权限 →', JSON.stringify(await st(c)), '（预期仍是 23 / 入口可用）')

console.log('· 现在**刷新页面**（不重新登录）…')
await load(c)
const s3=await st(c)
console.log('③ 刷新后：', JSON.stringify(s3))
console.log('   · 权限已同步到 21 条 →', s3.perms===21 ? '✅' : '❌')
console.log('   · 「样品管理」入口**还在** →', s3.menuShown ? '✅' : '❌')
console.log('   · 它被标成"没权限" →', s3.denied ? '✅' : '❌')
shot=await c.send('Page.captureScreenshot',{format:'png'}); writeFileSync(join(OUT,'权限同步-2-刷新后入口仍在但有标记.png'), Buffer.from(shot.data,'base64'))

await raw(c,`(() => { const a=[...document.querySelectorAll('.nav-link')].find(n=>n.innerText.includes('样品管理')); if(a) a.click(); return !!a })()`)
await sleep(2200)
const denied=await raw(c,`(() => { const el=document.querySelector('.permission-denied'); return el ? el.innerText.replace(/\\n+/g,' | ') : null })()`)
console.log('④ 点进「样品管理」→', denied ? '✅ ' + denied.slice(0,80) : '❌ 没有说明页')
shot=await c.send('Page.captureScreenshot',{format:'png'}); writeFileSync(join(OUT,'权限同步-3-点进去的说明.png'), Buffer.from(shot.data,'base64'))

console.log('· 管理员加回权限，再刷新一次…')
await setSample(true)
await load(c)
const s5=await st(c)
console.log('⑤ 加回后刷新：权限', s5.perms, '｜入口在', s5.menuShown, '｜被标没权限', s5.denied,
  '→', (s5.perms===23 && !s5.denied) ? '✅ 恢复' : '❌')
console.log('✓ 截图 3 张')
c.close()
} finally { b.kill(); await setSample(true) }
