import http from 'node:http'
import {spawn} from 'node:child_process'
import {existsSync} from 'node:fs'
import {networkInterfaces} from 'node:os'
const browser=['/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge','/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].find(existsSync)
if (!browser) throw new Error('No test browser')
const localIp=Object.values(networkInterfaces()).flat().find(x=>x && x.family==='IPv4' && !x.internal).address
let finish
const reported=new Promise(resolve=>{finish=resolve})
const server=http.createServer((req,res)=>{
  if(req.url==='/result') {
    let data=''
    req.on('data',chunk=>{data+=chunk})
    req.on('end',()=>{res.end('ok');finish(data)})
    return
  }
  res.setHeader('Content-Type','text/html')
  res.end('<html><body><script>let info={secure:window.isSecureContext,randomUUID:typeof crypto.randomUUID};try{crypto.randomUUID()}catch(e){info.error=e.message}document.body.innerText=JSON.stringify(info);fetch("/result",{method:"POST",body:JSON.stringify(info)})</script></body></html>')
})
await new Promise(resolve=>server.listen(5291,'0.0.0.0',resolve))
try {
  const child=spawn(browser,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-extensions','--disable-sync','--disable-background-networking','--disable-dev-shm-usage','--no-sandbox','--no-proxy-server','--user-data-dir=/tmp/crm_audit_lan_browser_2',`http://${localIp}:5291`],{stdio:'ignore'})
  let timer
  const result=await Promise.race([reported,new Promise(resolve=>{timer=setTimeout(()=>resolve('timeout'),20000)})])
  clearTimeout(timer)
  console.log('LAN_BROWSER',result)
  child.kill('SIGTERM')
} finally {server.close()}
