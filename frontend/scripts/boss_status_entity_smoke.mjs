// Real replay summary rows, detail ledger, curves and archived projection.
import assert from "node:assert/strict";
import {readFile, writeFile} from "node:fs/promises";
import {fileURLToPath} from "node:url";
import {inflateSync} from "node:zlib";
import {createServer} from "vite";
import {chromium} from "playwright-core";

const output = new URL("../../outputs/verification/boss_status_entity_20261020/", import.meta.url);
const old = JSON.parse(await readFile(new URL("1019噬魂竞速第十七把.json", output), "utf8"));
const fixed = JSON.parse(await readFile(new URL("fixed.json", output), "utf8"));
assert.equal(fixed.detail_rows, old.detail_rows);
const {effects, views} = fixed.ui;
const roundNumbers = value => JSON.parse(JSON.stringify(value, (_key, v) =>
  typeof v === "number" ? Math.round(v * 1e8) / 1e8 : v));
for (const entity of fixed.entities) {
  assert.deepEqual(roundNumbers(entity.table_rows), roundNumbers(entity.exact_rows), entity.name);
  const previous = old.entities.find(e => e.target_ids[0] === entity.target_ids[0]);
  assert.equal(entity.detail_rows, previous.detail_rows);
}
const first = views.find(v => v.target_ids[0].endsWith("@4237003"));
const third = views.find(v => v.target_ids[0].endsWith("@4237023"));
const archived = JSON.parse(inflateSync(Buffer.from(fixed.archive.data, "base64")));
for (const entity of fixed.entities) {
  const rows = archived.effects.filter(r => r.target_id === entity.target_ids[0]);
  assert.ok(rows.length > 0);
  for (const row of rows) {
    assert.ok(entity.table_rows.some(r => r.key === row.key && r.skill_id === row.skill_id
      && r.value === row.value && JSON.stringify(r.intervals) === JSON.stringify(row.intervals)));
  }
}
const props = fixed.ui;
const server = await createServer({
  server: {host: "127.0.0.1", port: 0, hmr: false},
  plugins: [{
    name: "boss-entity-test",
    configureServer(s) {
      s.middlewares.use("/__boss_entity", async(req, res, next) => {
        if (req.url.includes("html-proxy")) return next();
        res.setHeader("Content-Type", "text/html; charset=utf-8");
        res.end(await s.transformIndexHtml("/__boss_entity", `<html><body><div id="app"></div>
        <script type="module">
        import {createApp, h} from 'vue';
        import '/src/styles.css';
        import Explorer from '/src/components/LiveStatusExplorer.vue';
        const props = ${JSON.stringify(props)};
        createApp({render:()=>h('main',{style:'margin:20px'},[
          h('h2','第十七把 · 实体状态回放验证'),
          h('section',{id:'live'},h(Explorer,props)),
          h('section',{id:'history'},h(Explorer,{...${JSON.stringify(archived)},events:[],historyOnly:true}))
        ])}).mount('#app');
        </script></body></html>`));
      });
    },
  }],
});
await server.listen();
const browser = await chromium.launch({channel: "msedge", headless: true});
try {
  const page = await browser.newPage({viewport: {width: 1500, height: 1000}});
  const errors = [];
  page.on("pageerror", e => errors.push(String(e)));
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/__boss_entity`);
  for (const id of ["live", "history"]) {
    const panel = page.locator(`#${id}`);
    await panel.getByLabel("目标 Boss").selectOption(first.key);
    if (id === "live") assert.equal(await panel.locator("tbody tr").count(), 50);
    for (const skill of ["重锤", "破甲", "地狱火海", "C2H5OH"]) {
      assert.ok(await panel.locator("tbody tr").filter({hasText: skill}).count(), `${id}: ${skill}`);
    }
    await panel.getByLabel("来源技能").selectOption("重锤");
    assert.ok(await panel.locator("tbody tr").count() > 0);
    await panel.getByLabel("来源技能").selectOption("");
    await panel.getByLabel("目标 Boss").selectOption(third.key);
    if (id === "live") assert.equal(await panel.locator("tbody tr").count(), 66);
    await panel.getByLabel("目标 Boss").selectOption(first.key);
    if (id === "live") {
      await panel.getByRole("button", {name: "状态明细", exact: true}).click();
      assert.match(await panel.locator(".status-table-toolbar").innerText(), /459 条/);
      await panel.getByRole("button", {name: "状态总览", exact: true}).click();
    }
    await panel.getByRole("checkbox", {name: "通用增伤", exact: true}).check();
    await panel.getByRole("checkbox", {name: "减火抗", exact: true}).check();
    assert.equal(await panel.locator(".boss-status-curve path").count(), 2);
  }
  assert.deepEqual(errors, []);
  await page.locator("#live .status-table-toolbar").scrollIntoViewIfNeeded();
  await page.screenshot({path: fileURLToPath(new URL("实体状态表验证.png", output))});
  console.log("PASS: all 7 entity ledgers match exact views; live first/third phase = 50/66 rows; history filtering and skill selection passed.");
} finally {
  await browser.close();
  await server.close();
}

const escape = s => String(s).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
const rows = fixed.entities.map(e => {
  const prev = old.entities.find(p => p.target_ids[0] === e.target_ids[0]);
  return `<tr><td>${escape(e.name)}<small>${escape(e.target_ids[0])}</small></td>
    <td>${prev.table_rows.length}</td><td>${e.table_rows.length}</td>
    <td>${e.exact_rows.length}</td><td>${e.detail_rows}</td></tr>`;
}).join("");
await writeFile(new URL("Boss状态实体归属修复验证_20261020.html", output), `<!doctype html>
<html lang="zh"><meta charset="utf-8"><title>Boss状态实体归属修复验证</title>
<style>body{font:16px/1.7 system-ui;color:#21434a;background:#f1f7f7;margin:36px auto;max-width:1100px}main{background:white;padding:32px;border-radius:16px}h1{font-size:26px}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:12px;border-bottom:1px solid #dce8e8}th{background:#e8f3f3}small{display:block;color:#65818a}img{max-width:100%}a{color:#007d85}</style>
<main><h1>1019 噬魂竞速第十七把：Boss 状态实体归属修复</h1>
<p>问题不是原包没有状态，而是总览按同名 Boss 合并了不同实体，并挂在最后刷新的实体上。曲线、逐次明细按实体读取，所以与总览矛盾。</p>
<p>现在总览、覆盖率及有效状态区间均按运行时实体分别计算；巢穴 Boss 血量与阶段分组规则不变。不凭空补状态、不把其他阶段的状态复制进来。</p>
<table><thead><tr><th>实体</th><th>旧总览状态数</th><th>修复后</th><th>独立实体核算</th><th>逐次记录（未变）</th></tr></thead><tbody>${rows}</tbody></table>
<p>表中不含前端额外显示的两条叠冰汇总。海龙第一阶段页面因此由 3 条变为 50 条；第三阶段为 66 条，状态区间不再混入第一阶段。全局总览 ${old.summary_rows} → ${fixed.summary_rows}，逐次明细仍为 ${fixed.detail_rows} 条。</p>
<p>7 个实体的总览与各自独立核算逐字段一致（浮点数比较精度为 10⁻⁸）。浏览器使用真实回放数据验证了第一、第三阶段切换、重锤筛选和归档后的历史状态组件。</p>
<img src="实体状态表验证.png" alt="真实回放状态表验证">
<p>新生成的上传状态账本使用相同实体归属；既有云端账本不会自动重算，需要重载原包、重新评分上传。缓存指纹包含聚合器源码，重启新版核心后不会继续复用旧解码缓存。</p>
<p>本次修复了公共状态账本，因此依赖实体归属的辅助等下游计算也会获得正确数据；未改评分公式，未声称所有历史评分数值完全不变。未打包安装程序、未部署云端。</p></main></html>`, "utf8");
