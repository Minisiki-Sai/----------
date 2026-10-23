import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { chromium } from 'playwright-core';

const server = await createServer({ server: { host: '127.0.0.1', port: 5179, hmr: false } });
await server.listen();
const port = server.httpServer.address().port;
const browser = await chromium.launch({ channel: 'msedge', headless: true });
try {
  const page = await browser.newPage();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on('pageerror', error => { errors.push(error.message); console.error(error.message); });
  page.on('console', message => { if (message.type() === 'error') console.error(message.text()); });
  await page.route('**/summary-test', async route => route.fulfill({ contentType: 'text/html; charset=utf-8', body: await server.transformIndexHtml('/summary-test', `
    <meta charset="utf-8"><div id="app"></div><script type="module">
    import { createApp, h } from 'vue';
    import Explorer from '/src/components/LiveStatusExplorer.vue';
    const props = { extent: 50, series: [], events: [],
      effects: [{ target_id:'a', target_name:'Boss A', key:'减光抗%', label:'减光抗', skill_name:'精神爆破', job:'雷神', value:20, status_kind:'减抗', refresh_count:1, intervals:[[0,10]] }],
      views: [{ key:'a', name:'Boss A', target_ids:['a'], series:[], output_windows:[[0,10]] },
              { key:'b', name:'Boss B', target_ids:['b'], series:[], output_windows:[[20,50]] }] };
    createApp({ render: () => h('div', [h('section',{id:'live'},h(Explorer,props)), h('section',{id:'history'},h(Explorer,{...props,historyOnly:true}))]) }).mount('#app');
    </script>`) }));
  await page.goto(`http://127.0.0.1:${port}/summary-test`);
  for (const id of ['live', 'history']) {
    const panel = page.locator(`#${id}`);
    const skillRow = panel.locator('tbody tr').filter({ hasText: '精神爆破' });
    await skillRow.waitFor().catch(async error => { console.error(await page.locator('body').innerText()); throw error; });
    assert.match(await skillRow.innerText(), /全部 Boss/);
    assert.match(await skillRow.innerText(), /25\.0%/);
    await panel.getByLabel('目标 Boss').selectOption('a');
    assert.match(await skillRow.innerText(), /100\.0%/);
    await panel.getByLabel('目标 Boss').selectOption('__boss_rows__');
    assert.match(await skillRow.innerText(), /Boss A/);
    await panel.getByLabel('目标 Boss').selectOption('');
    await panel.getByLabel('来源技能').selectOption('精神爆破');
    assert.equal(await panel.locator('tbody tr').count(), 1);
    assert.match(await skillRow.innerText(), /25\.0%/);
  }
  assert.deepEqual(errors, []);
  console.log('Boss summary UI smoke passed for both live and history, individual targets and skill filtering.');
} finally { await browser.close(); await server.close(); }
