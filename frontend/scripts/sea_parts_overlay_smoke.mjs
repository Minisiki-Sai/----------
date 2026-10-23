import { chromium } from 'playwright-core';
import assert from 'node:assert/strict';
const output='C:/Users/gcg59/Documents/Codex/2026-09-14/referenced-chatgpt-conversation-this-is-an/work';
const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/155.0.4283.62/msedge.exe',headless:true});
const page=await browser.newPage({viewport:{width:340,height:170},deviceScaleFactor:2});
const errors=[];
page.on('pageerror',e=>errors.push(e.message));
page.on('request',r=>{if(r.url().includes('/api/'))console.log(r.url());});
await page.addInitScript(()=>{
 localStorage.setItem('dragon-nest-v420-buff-overlay-enabled','1');
 localStorage.setItem('dragon-nest-v420-buff-overlay-settings',JSON.stringify({overlaySurfaceVersion:2,showBossHealthOverlay:true}));
});
const target='0x00000069@4237023';
let destroyed=false, ended=false;
const payload=()=>({state:'running',status:'截图验证',elapsedSeconds:40,selfSourceId:'p',
  players:[],buffs:[],debuffs:[],bosses:[],endedBossIds:ended?[target]:[],
  selfAttackTarget:{targetId:target,timestampMs:40000},
  bossHealth:[
   {instanceId:'main',targetId:target,name:'梦魇·海龙萨芬特拉',currentHp:6284800000,maxHp:9820000000,percent:64,confidence:'packet_hp',updatedSeconds:40},
   {instanceId:'head',targetId:target,partId:4150061,name:'海龙 · 头部',currentHp:destroyed?0:45000000,maxHp:90000000,percent:destroyed?0:50,confidence:'packet_hp',updatedSeconds:40},
   {instanceId:'tail',targetId:target,partId:4150062,name:'海龙 · 尾部',currentHp:350000000,maxHp:700000000,percent:50,confidence:'packet_hp',updatedSeconds:40},
  ]});
let sequence=0;
async function publish(){
 await page.evaluate(({payload,sequence})=>{
  const channel=new BroadcastChannel('dragon-nest-overlay-event-v1');
  channel.postMessage({schemaVersion:1,topic:'combat.snapshot',streamId:'sea-parts-smoke',sequence,publishedAt:Date.now(),payload});
  setTimeout(()=>channel.close(),100);
 },{payload:payload(),sequence:++sequence});
}
try{
 await page.goto('http://127.0.0.1:1431/?view=boss-health-overlay');
 await page.waitForFunction(()=>document.documentElement.classList.contains('buff-overlay-mode'));
 await publish();
 await page.locator('.boss-health-card').nth(2).waitFor({state:'visible',timeout:30000});
 await page.locator('.boss-health-list').screenshot({path:output+'/sea-parts-overlay.png'});
 destroyed=true;
 await publish();
 await page.waitForFunction(()=>document.querySelector('.boss-health-list')?.textContent.includes('已破坏'));
 await page.locator('.boss-health-list').screenshot({path:output+'/sea-parts-overlay-broken.png'});
 assert.equal(await page.locator('.boss-health-card').count(),3);
 ended=true;
 await publish();
 await page.waitForFunction(()=>document.querySelectorAll('.boss-health-card').length===0);
 assert.deepEqual(errors,[]);
 console.log('actual overlay: three rows, destroyed state, parent death cleanup passed');
}catch(e){console.log(errors,await page.locator('body').innerText());throw e;}
finally{await browser.close();}
