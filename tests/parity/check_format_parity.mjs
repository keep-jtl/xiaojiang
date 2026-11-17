// 双端显示口径一致性：Windows ui/analysis.html 的 pct() 与手机 money.js formatPctScaled()
// 必须对同一 scaled 整数输出完全相同字符串（四舍五入、进位、负零、正负号）。
import fs from 'node:fs';
import { formatPctScaled } from '../../mobile/src/money.js';

const html = fs.readFileSync(
  'D:/Projects/XiaoLiCaiTong-Dev/ui/analysis.html', 'utf8');

// 从 analysis.html 抽取 pct 函数（与页面实际执行的同一段代码）
const m = /function pct \(sc\) \{[\s\S]*?\n\}/.exec(html);
if (!m) throw new Error('未在 analysis.html 中找到 pct()');
const pct = new Function('sc', m[0].replace(/^function pct \(sc\) \{/, '')
  .replace(/\n\}$/, ''));

const cases = [
  0, 1, 4, 5, 49, 50, 99, 100, 149, 150, 1230, 1234, 1235, 12345,
  712475, 712449, 712450, 275353, 275345, 999999, 1000000,
  -1, -5, -1235, -712475, -712450, -999999, -123456,
  1, 18200, -18200, 100000000,
];
// 说明：手机端历史风格是正数带 "+"，Windows 端用颜色表达方向、不带 "+"。
// 本项对照「数值与四舍五入结果」，即去掉正号后的字符串必须完全一致。
const norm = (s) => s.replace(/^\+/, '');
let bad = 0;
for (const v of cases) {
  const a = norm(pct(v));
  const b = norm(formatPctScaled(v));
  if (a !== b) { bad++; console.log('DIFF', v, 'windows=', a, 'mobile=', b); }
}
console.log('对照值', cases.length, '不一致', bad);
if (bad) process.exit(1);
console.log('PASS 双端百分比数值与四舍五入口径一致（正号风格差异：手机带 +、Windows 不带）');
