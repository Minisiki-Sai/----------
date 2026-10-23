// Read-only SQL regression: exercise production bin selection with synthetic bounds.
import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import assert from "node:assert/strict";
const cli = process.argv[2];
assert.ok(cli, "Pass the installed CloudBase CLI entry path");
const source = await readFile(new URL("../cloudbase/postgresql/040_v476_ranking_refinements.sql", import.meta.url), "utf8");
const ranges = source.slice(source.indexOf("), scale as ("), source.indexOf("), counts as ("));
const sql = "with bounds(metric,lower,upper,sample_count) as (values " +
  "('zero',0::double precision,0::double precision,1)," +
  "('constant',88,88,1),('negative',-123.4,37.9,2)," +
  "('boundary',4.99,104.99,2),('million',1245678,9834521,2)" +
  ranges + ") select metric,lower,width,bin_count from ranges order by metric";
const result = spawnSync(process.execPath, [cli,"db","execute","-e",
  "dragonnest-d0gy32zvzd63b6c52","--sql",sql,"--json"], {encoding:"utf8",timeout:60000});
assert.equal(result.status,0,result.stderr || result.stdout);
const payload = JSON.parse(result.stdout);
const rows = payload.data.Rows.map(row=>JSON.parse(row));
const limits = {zero:[0,0],constant:[88,88],negative:[-123.4,37.9],boundary:[4.99,104.99],million:[1245678,9834521]};
assert.equal(rows.length,5);
for (const [metric,rawLower,rawWidth,rawCount] of rows) {
  const lower=Number(rawLower),width=Number(rawWidth),count=Number(rawCount);
  const [min,max] = limits[metric];
  assert.equal(count,20);
  assert.ok(width>=1 && lower<=min && lower+width*20>=max);
  assert.ok(Math.abs(lower/width-Math.round(lower/width))<1e-8);
}
console.log(JSON.stringify({histogramSql:"passed",cases:rows.length,includes:"zero, constant, negative, boundary, million",bins:20}));
