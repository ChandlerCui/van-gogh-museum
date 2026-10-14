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

/* Commons 直出缩略图：thumb.php 直接返回图像字节，不经第三方中转服务
   （wsrv.nl 在本机网络不可达，且实测 Wikimedia 比 GitHub Pages 更快） */
const enc = encodeURIComponent;
function thumbUrl(file, width) {
  return 'https://commons.wikimedia.org/w/thumb.php?f=' + enc(file.replace(/ /g, '_')) + '&width=' + width;
}
/* Commons 只按"常见缩略图尺寸"生成缩略图，其余宽度一律返回 HTTP 400
   （https://www.mediawiki.org/wiki/Common_thumbnail_sizes）。
   故把目标宽度吸附到白名单里最接近的一档，并按原图宽度封顶（绝不放大）。 */
const COMMON = [120, 250, 500, 960, 1280, 1920];
function pickWidth(px, origW) {
  let pick = COMMON[0];
  let best = Infinity;
  for (const s of COMMON) {
    const d = Math.abs(s - px);
    if (d < best) { best = d; pick = s; }
  }
  let cap = COMMON[0];
  for (const s of COMMON) if (s <= origW) cap = s;
  return Math.min(pick, cap);
}
/* longEdge：希望的长边像素；竖幅按原图比例反推宽度 */
function detailUrl(im, longEdge) {
  const w = im.w || 4;
  const h = im.h || 3;
  const target = Math.min(longEdge, Math.max(w, h));
  return thumbUrl(im.file, pickWidth(Math.round(target * Math.min(1, w / h)), w));
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
  let img;
  if (local) {
    /* 缩略图随站点分发（体积小、网格页秒开）；
       详情图留空则由 Commons 直供，仓库因此只需保留缩略图。
       三档远端尺寸按展示位反推：hero 首页首屏 ≈960、detail 作品页 ≈1280、zoom 灯箱 ≈1920 */
    const detail = local.detail || detailUrl(im, 1280);
    img = {
      thumb: local.thumb,
      detail,
      hero: local.detail || detailUrl(im, 960),
      zoom: local.zoom || detailUrl(im, 1920),
      credit,
      w: local.w,
      h: local.h,
    };
  } else if (im.source === 'met') {
    img = {
      thumb: im.small,
      detail: im.small,
      hero: im.small,
      zoom: im.original,
      credit: CREDIT_MET,
      w: im.w || 800,
      h: im.h || 1000,
    };
  } else {
    img = {
      thumb: thumbUrl(im.file, 480),
      detail: detailUrl(im, 1280),
      hero: detailUrl(im, 960),
      zoom: detailUrl(im, 1920),
      credit: CREDIT_COMMONS,
      w: im.w || 4,
      h: im.h || 3,
    };
  }

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
const thumbLocal = works.filter((w) => w.img.thumb.indexOf('assets/img/') === 0).length;
const detailLocal = works.filter((w) => w.img.detail.indexOf('assets/img/') === 0).length;
console.log('缩略图本地：' + thumbLocal + ' / ' + works.length + ' 件');
console.log('详情图本地：' + detailLocal + ' / ' + works.length + ' 件（其余由 Wikimedia Commons 直供）');
/* 校验：所有远端宽度都必须落在 Commons 常见缩略图尺寸白名单内 */
const whitelist = [120, 250, 500, 960, 1280, 1920];
let outOfList = [];
for (const w of works) {
  for (const k of ['hero', 'detail', 'zoom']) {
    const u = w.img[k];
    if (u.indexOf('http') !== 0) continue;
    const m = /[?&]width=(\d+)/.exec(u);
    if (!m || whitelist.indexOf(Number(m[1])) < 0) outOfList.push(w.id + ' ' + k + ' → ' + u);
  }
}
console.log('白名单校验：' + (outOfList.length ? '越界 ' + outOfList.length + ' 条\n  ' + outOfList.join('\n  ') : '全部通过'));
const sample = works.find((w) => w.img.thumb.indexOf('assets/img/') === 0);
if (sample) console.log('抽样 ' + sample.id + '：thumb=' + sample.img.thumb + ' | hero=' + sample.img.hero + ' | detail=' + sample.img.detail + ' | zoom=' + sample.img.zoom);
