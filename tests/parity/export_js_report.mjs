// 手机端 buildReport JSON 导出（双端一致性用）。相对路径基于本文件位置，可在项目内复现。
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { buildReport } from '../../mobile/src/portfolioAnalysis.js';

const here = dirname(fileURLToPath(import.meta.url));
const snap = JSON.parse(fs.readFileSync(join(here, '..', 'fixtures', 'fixture_snapshot.json'), 'utf8'));

function mobileHolding(f) {
  const rows = f.nav_rows;
  const last = rows[rows.length - 1];
  const prev = rows[rows.length - 2];
  return {
    fund_code: f.code, name: f.name, fund_type: f.fund_type, status: 'holding',
    shares_scaled: f.test_shares_scaled, cost_cents: f.test_cost_cents,
    nav_date: last.date, nav_scaled: last.nav,
    prev_nav_scaled: prev ? prev.nav : null, daily_pct_scaled: last.pct
  };
}
process.stdout.write(JSON.stringify(buildReport(snap.funds.map(mobileHolding))));
