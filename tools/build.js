/* ============================================================
   构建脚本：content/manifest.json + content/images.json + content/interp.json
   → js/data.js（window.VGM = {...}）
   用法：node tools/build.js
   说明：内容与展示分离——本脚本产出的数据与图像 URL 即小程序版的
   数据源，迁移时无需改动内容层。
   ============================================================ */
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const read = (f) => JSON.parse(fs.readFileSync(path.join(root, f), 'utf8'));

const manifest = read('content/manifest.json');
let images = {};
let interp = {};
let localImages = {};
try { images = read('content/images.json'); } catch (e) { console.warn('警告：content/images.json 缺失'); }
try { interp = read('content/interp.json'); } catch (e) { console.warn('警告：content/interp.json 缺失'); }
try { localImages = read('content/local-images.json'); } catch (e) { /* 尚未本地化时静默回退远端 */ }

/* 回退用：Commons 文件 → wsrv.nl 中转的可缩放 URL（仅在本地图像缺失时使用） */
const enc = encodeURIComponent;
function wsrvUrl(file, width) {
  const inner = 'https://commons.wikimedia.org/wiki/Special:FilePath/' + file.replace(/ /g, '_') + '?width=' + width;
  return 'https://wsrv.nl/?url=' + enc(inner);
}

const CREDIT_COMMONS = '图像来源：Wikimedia Commons（公有领域）';
const CREDIT_MET = '图像来源：纽约大都会艺术博物馆（Open Access · CC0）';

const works = [];
const skipped = [];

for (const w of manifest.works) {
  const im = images[w.id];
  if (!im) {
    skipped.push(w.id + '（无已验证图像）');
    continue;
  }
  const it = interp[w.id] || {};
  const credit = im.source === 'met' ? CREDIT_MET : CREDIT_COMMONS;
  const local = localImages[w.id];
  const img = local
    ? { /* 本地打包：随站点分发，无需外网即可秒开 */
        thumb: local.thumb,
        detail: local.detail,
        zoom: local.zoom || local.detail,
        credit,
        w: local.w,
        h: local.h,
      }
    : im.source === 'met'
      ? {
          thumb: im.small,
          detail: im.small,
          zoom: im.original,
          credit: CREDIT_MET,
          w: im.w || 800,
          h: im.h || 1000,
        }
      : {
          thumb: wsrvUrl(im.file, 480),
          detail: wsrvUrl(im.file, 1400),
          zoom: wsrvUrl(im.file, 2400),
          credit: CREDIT_COMMONS,
          w: im.w || 4,
          h: im.h || 3,
        };

  works.push({
    id: w.id,
    period: w.period,
    zh: w.zh,
    en: w.en,
    date: w.date,
    medium: w.medium,
    dims: w.dims || null,
    location: w.location,
    featured: !!w.featured,
    must: !!w.must,
    img,
    interp: it,
  });
}

const periods = manifest.periods.map((p) => {
  const ws = works.filter((w) => w.period === p.id);
  const cover =
    (ws.find((w) => w.must && w.featured) || ws.find((w) => w.featured) || ws[0] || {}).id || null;
  return { id: p.id, num: p.num, title: p.title, short: p.short || p.title, mini: p.mini || p.short || p.title, en: p.en, years: p.years, places: p.places, accent: p.accent, preface: p.preface, count: ws.length, cover };
});

const out = { periods, works };
fs.mkdirSync(path.join(root, 'js'), { recursive: true });
fs.writeFileSync(path.join(root, 'js/data.js'), 'window.VGM = ' + JSON.stringify(out) + ';\n');

console.log('构建完成：收录作品 ' + works.length + ' 件 / ' + manifest.works.length + ' 幅（跳过 ' + skipped.length + ' 幅）');
if (skipped.length) console.log('跳过清单：\n  ' + skipped.join('\n  '));
const localCount = works.filter((w) => w.img.thumb.indexOf('assets/img/') === 0).length;
console.log('本地图像：' + localCount + ' / ' + works.length + ' 件' + (localCount === works.length ? '（全部本地化）' : '（其余回退远端）'));
const sample = works.find((w) => w.img.thumb.indexOf('assets/img/') === 0);
if (sample) console.log('抽样验证：' + sample.id + ' → ' + sample.img.thumb + ' (' + sample.img.w + 'x' + sample.img.h + ')');
