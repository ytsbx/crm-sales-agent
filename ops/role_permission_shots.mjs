import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
const APP = process.env.APP || 'http://127.0.0.1:5276'
const API = process.env.API || 'http://127.0.0.1:8008'
const PORT = 9551, OUT = process.argv[2]
const sleep = (ms) => new Promise(r => setTimeout(r, ms))
const bin = '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
async function connect(ws){return new Promise((res,rej)=>{const s=new WebSocket(ws);const p=new Map();s.addEventListener('open',()=>res({send:(m,q={})=>new Promise((a,b)=>{const id=Math.floor(Math.random()*1e9);p.set(id,{a,b});s.send(JSON.stringify({id,method:m,params:q}))}),close:()=>s.close()}));s.addEventListener('error',()=>rej(new Error('ws')));s.addEventListener('message',e=>{const m=JSON.parse(e.data);const t=p.get(m.id);if(!t)return;p.delete(m.id);m.error?t.b(new Error(JSON.stringify(m.error))):t.a(m.result)})})}
const login = await fetch(`${API}/api/v1/auth/login`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'admin',password:'admin123'})}).then(r=>r.json())
if (!login.data) { console.log('登录失败:', JSON.stringify(login).slice(0,200)); process.exit(1) }
const me = await fetch(`${API}/api/v1/auth/me`,{headers:{Authorization:`Bearer ${login.data.access_token}`}}).then(r=>r.json())
const b = spawn(bin,['--headless=new','--disable-gpu','--no-first-run',`--remote-debugging-port=${PORT}`,`--user-data-dir=${join(tmpdir(),'chk-perm-'+Date.now())}`,'about:blank'],{stdio:'ignore'})
const raw = async (c,expr)=>{const r=await c.send('Runtime.evaluate',{expression:expr,returnByValue:true});if(r.exceptionDetails)throw new Error(r.exceptionDetails.exception?.description||'eval');return r.result.value}
try {
  for(let i=0;i<60;i++){try{if((await fetch(`http://127.0.0.1:${PORT}/json/version`)).ok)break}catch{}await sleep(250)}
  const t = await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(APP+'/login')}`,{method:'PUT'}).then(r=>r.json())
  const c = await connect(t.webSocketDebuggerUrl)
  await c.send('Page.enable'); await c.send('Runtime.enable')
  await c.send('Emulation.setDeviceMetricsOverride',{width:1500,height:1000,deviceScaleFactor:1,mobile:false})
  await c.send('Runtime.evaluate',{expression:`localStorage.setItem('crm-auth', ${JSON.stringify(JSON.stringify({state:{token:login.data.access_token,user:me.data},version:0}))})`})
  await c.send('Page.navigate',{url:APP+'/settings?v='+Date.now()})
  for(let i=0;i<80;i++){const p=await c.send('Runtime.evaluate',{expression:'document.body.innerText.includes("角色")',returnByValue:true});if(p.result.value)break;await sleep(300)}
  await sleep(2000)
  // 先切到"角色与数据范围"页签（默认在"用户"，直接点会开到"编辑用户"）
  console.log('切页签 →', await raw(c,`(() => {
    const tab = Array.from(document.querySelectorAll('.semi-tabs-tab')).find(n => (n.innerText||'').trim() === '角色与数据范围');
    if (!tab) return 'no-tab';
    tab.click(); return 'clicked';
  })()`))
  await sleep(1800)
  // 打开"编辑角色：业务员"
  console.log('打开编辑弹窗 →', await raw(c,`(() => {
    const rows = Array.from(document.querySelectorAll('.semi-table-row'));
    const row = rows.find(r => (r.innerText||'').includes('业务员'));
    if (!row) return 'no-row';
    const btn = Array.from(row.querySelectorAll('button, .semi-button')).find(b => /编辑/.test(b.innerText||''));
    if (!btn) return 'no-row-button';
    btn.click(); return 'clicked';
  })()`))
  await sleep(1800)
  const info = await raw(c,`(() => {
    const picker = document.querySelector('.permission-picker');
    if (!picker) return JSON.stringify({error:'选择器没渲染'});
    const sections = Array.from(picker.querySelectorAll('.permission-picker-section-title')).map(n=>n.innerText.trim());
    const groups = Array.from(picker.querySelectorAll('.permission-picker-group')).map(g => ({
      name: g.querySelector('.permission-picker-group-name')?.innerText.trim(),
      count: g.querySelector('.permission-picker-group-count')?.innerText.trim(),
      items: Array.from(g.querySelectorAll('.permission-picker-row')).length,
    }));
    const meta = picker.querySelector('.permission-picker-count')?.innerText.trim();
    return JSON.stringify({sections, groupCount: groups.length, groups, meta,
      bodyScrollable: picker.querySelector('.permission-picker-body').scrollHeight > picker.querySelector('.permission-picker-body').clientHeight});
  })()`)
  console.log(info)
  mkdirSync(OUT,{recursive:true})
  let shot = await c.send('Page.captureScreenshot',{format:'png'})
  writeFileSync(join(OUT,'角色权限-分组之后.png'), Buffer.from(shot.data,'base64'))
  console.log('✓ 截图 角色权限-分组之后.png')
  // 搜索态
  await raw(c,`(() => {
    const input = document.querySelector('.permission-picker-bar input');
    const desc = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
    desc.set.call(input, '报价');
    input.dispatchEvent(new Event('input',{bubbles:true}));
    return 1;
  })()`)
  await sleep(900)
  console.log('搜索「报价」 →', await raw(c,`(() => JSON.stringify({
    rows: Array.from(document.querySelectorAll('.permission-picker-row')).map(r=>r.innerText.trim().replace(/\\n/g,' · ')),
    empty: document.querySelector('.permission-picker-empty')?.innerText.trim() || null,
  }))()`))
  shot = await c.send('Page.captureScreenshot',{format:'png'})
  writeFileSync(join(OUT,'角色权限-搜索报价.png'), Buffer.from(shot.data,'base64'))
  console.log('✓ 截图 角色权限-搜索报价.png')
  c.close()
} finally { b.kill() }
