// Silent isolated Chromium test of the unchanged production player. No ACT API.
import { chromium } from 'playwright-core';
import ts from 'typescript';
import { readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const player=ts.transpileModule(await readFile(path.join(root,'frontend/src/utils/qwenVoicePlayer.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}}).outputText;
const server=createServer(async(req,res)=>{
  try {
    if(req.url==='/') {res.setHeader('Content-Type','text/html');res.end('<title>Silent voice diagnostics</title>');return;}
    if(req.url==='/player.js') {res.setHeader('Content-Type','text/javascript');res.end(player);return;}
    if(!/^\/voices\/(manifest\.json|[A-Za-z_]+\/[a-f0-9]+\.wav)$/.test(req.url)) {res.writeHead(404).end();return;}
    res.end(await readFile(path.join(root,'frontend/public/voices/qwen3',req.url.slice(8))));
  }catch {res.writeHead(404).end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
let browser;
try {
  browser=await chromium.launch({executablePath:'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',headless:true,args:['--autoplay-policy=no-user-gesture-required','--mute-audio']});
  const page=await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  const result=await page.evaluate(async()=>{
    const {QwenVoicePlayer,QWEN_VOICES}=await import('/player.js');
    const starts=[];
    const original=AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start=function(when){starts.push({called:performance.now(),scheduled_ms:(when-this.context.currentTime)*1000,duration_ms:this.buffer.duration*1000});return original.call(this,when);};
    const samples=[];
    for(const {key} of QWEN_VOICES) {
      const p=new QwenVoicePlayer('/voices/');
      for(const mode of ['cold','warm','batched']) {
        if(mode==='warm') await p.warm(key);
        starts.length=0;
        const now=performance.now();
        await p.play(mode==='batched'?['六芒星已好','马上拉冰']:['跳'],key,0,false);
        samples.push({voice:key,mode,request_to_schedule_ms:performance.now()-now,starts:starts.map(s=>({...s,called:s.called-now}))});
      }
      p.dispose();
    }
    const timerTests=[];
    for(const blockMs of [0,1000,0,1000]) {
      const start=performance.now();let next=start+100;const gaps=[];
      await new Promise(resolve=>{
        const timer=setInterval(()=>{const now=performance.now();gaps.push(now-next);next=now+100;if(now-start>1800){clearInterval(timer);resolve();}},100);
        if(blockMs) setTimeout(()=>{const end=performance.now()+blockMs;while(performance.now()<end){}},150);
      });
      timerTests.push({artificial_main_thread_block_ms:blockMs,max_timer_lateness_ms:Math.max(...gaps)});
    }
    return {userAgent:navigator.userAgent,visibility:document.visibilityState,silent:true,samples,timerTests};
  });
  await writeFile(path.join(root,'outputs/verification/voice_latency_1019/browser.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
}finally {if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
