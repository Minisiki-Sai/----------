import { chromium } from 'playwright-core';
import assert from 'node:assert/strict';
const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
try {
  const page=await browser.newPage({viewport:{width:1600,height:1000}});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:5198/tests/fixtures/history-damage.html');
  await page.getByText('125.5%',{exact:true}).waitFor();
  assert.equal(await page.locator('.damage-skill-panel tbody tr td').nth(7).textContent(),'—');
  await page.locator('.compact-filter select').selectOption('raw');
  await page.getByText('未归因技能',{exact:true}).waitFor();
  await page.locator('.damage-roster button').nth(1).click();
  await page.getByText('单独显示灼烧之手',{exact:true}).waitFor();
  await page.locator('.damage-curve-controls select').selectOption('2');
  await page.locator('.damage-boss-panel button').click();
  assert.equal(await page.locator('.damage-boss-panel button').getAttribute('aria-pressed'),'true');
  assert.ok(await page.locator('.native-visual-toolbar').count());
  assert.ok(await page.locator('.damage-curve-panel svg').count());
  await page.screenshot({path:'../outputs/verification/history_damage_shared_panel.png',fullPage:true});
  await page.evaluate(()=>window.setOldArchive());
  await page.getByText('旧记录只上传了当前成员的技能明细，完整队伍交互及曲线需重新评分上传。',{exact:true}).waitFor();
  assert.equal(await page.locator('.damage-roster tbody tr').count(),2);
  assert.deepEqual(errors,[]);
  console.log('History damage controls, curve, realization, old archive: passed');
} finally {await browser.close();}
