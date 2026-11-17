import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
import { readFileSync } from 'node:fs';

const root = fileURLToPath(new URL('.', import.meta.url));
const outDir = fileURLToPath(new URL('../www', import.meta.url));
const versionFile = fileURLToPath(new URL('../android/version.properties', import.meta.url));

// 版本唯一来源：android/version.properties（与 Gradle 共用）
// 构建渠道：打包脚本设置 XTLC_BUILD_CHANNEL=development-debug，正式构建不设置
function readVersionProperties () {
  const map = {};
  const raw = readFileSync(versionFile, 'utf8');
  for (const line of raw.split(/\r?\n/)) {
    const text = line.trim();
    if (!text || text.startsWith('#')) continue;
    const eq = text.indexOf('=');
    if (eq < 0) continue;
    map[text.slice(0, eq).trim()] = text.slice(eq + 1).trim();
  }
  return map;
}

const props = readVersionProperties();
const appVersion = props.versionName || '0.0.0-unknown';
const appChannel = (process.env.XTLC_BUILD_CHANNEL || '').trim();

export default defineConfig({
  root,
  base: './',
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
    __APP_CHANNEL__: JSON.stringify(appChannel)
  },
  build: {
    outDir,
    emptyOutDir: true,
    target: 'es2020'
  }
});
