/* ============================================================
   图像本地化脚本：content/images.json → assets/img/ + content/local-images.json
   背景：wsrv.nl 与 upload.wikimedia.org 在部分网络下不可达，
        直连会造成图片长时间加载失败；改为随站点本地打包。
   用法：node tools/fetch-images.js
   特性：串行 + 退避重试 + 断点续跑（已存在且非空的文件跳过）+ 增量日志
   ============================================================ */
const https = require('https');
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const OUTDIR = path.join(root, 'assets', 'img');
const LOCAL = path.join(root, 'content', 'local-images.json');
const LOG = path.join(root, 'assets', 'fetch-images.log');
const TMPLOG = 'C:/Users/ChandlerC/AppData/Local/Temp/vgm-fetch.log';

const UA = 'VanGoghOnlineMuseum/1.0 (educational static site; local build)';
/* Commons 只按“常见缩略图尺寸”生成缩略图，非白名单宽度一律返回 HTTP 400，
   故只从白名单中挑取（thumb.php 的宽度参数为 w）。
   参考：https://www.mediawiki.org/wiki/Common_thumbnail_sizes */
const THUMB_W = [500, 960];
const DETAIL_W = [1280, 1920, 960];

fs.mkdirSync(OUTDIR, { recursive: true });

function log(s) {
  const line = s + '\n';
  process.stdout.write(line);
  try { fs.appendFileSync(TMPLOG, line); } catch (e) {}
}

function get(url, depth) {
  depth = depth || 0;
  return new Promise((resolve) => {
    const req = https.get(url, { headers: { 'User-Agent': UA, 'Accept-Encoding': 'identity' } }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        if (depth >= 3) return resolve({ err: 'redirect-loop' });
        const next = res.headers.location.startsWith('http')
          ? res.headers.location
          : 'https://commons.wikimedia.org' + res.headers.location;
        return resolve(get(next, depth + 1));
      }
      if (res.statusCode !== 200) { res.resume(); return resolve({ err: 'http' + res.statusCode }); }
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ buf: Buffer.concat(chunks) }));
    });
    req.on('error', (e) => resolve({ err: e.code || e.message }));
    req.setTimeout(60000, () => { req.destroy(); resolve({ err: 'timeout' }); });
  });
}

async function fetchWithRetry(url, tries) {
  let wait = 900;
  for (let i = 0; i < (tries || 5); i++) {
    const r = await get(url);
    if (r.buf && r.buf.length > 1000) return r.buf;
    log('      [' + (r.err || 'too-small') + '] retry#' + (i + 1));
    await new Promise((s) => setTimeout(s, wait));
    wait = Math.round(wait * 1.7);
  }
  return null;
}

/* 解析 JPEG 真实尺寸（SOF 段） */
function jpegSize(buf) {
  if (buf[0] !== 0xff || buf[1] !== 0xd8) return null;
  let i = 2;
  while (i < buf.length - 9) {
    if (buf[i] !== 0xff) { i++; continue; }
    const m = buf[i + 1];
    if (m >= 0xc0 && m <= 0xcf && m !== 0xc4 && m !== 0xc8 && m !== 0xcc) {
      return { h: buf.readUInt16BE(i + 5), w: buf.readUInt16BE(i + 7) };
    }
    if (m === 0xd8 || m === 0x01 || (m >= 0xd0 && m <= 0xd7)) { i += 2; continue; }
    const len = buf.readUInt16BE(i + 2);
    if (len <= 0) break;
    i += 2 + len;
  }
  return null;
}

const commonsUrl = (file, w) =>
  'https://commons.wikimedia.org/w/thumb.php?f=' + encodeURIComponent(file.replace(/ /g, '_')) + '&w=' + w;

/* 依次尝试多个宽度，返回首个成功字节 */
async function fetchWidths(file, widths) {
  for (const w of widths) {
    const b = await fetchWithRetry(commonsUrl(file, w), 2);
    if (b) return b;
  }
  return null;
}

(async () => {
  try { fs.writeFileSync(TMPLOG, ''); } catch (e) {}
  const images = JSON.parse(fs.readFileSync(path.join(root, 'content', 'images.json'), 'utf8'));
  let local = {};
  try { local = JSON.parse(fs.readFileSync(LOCAL, 'utf8')); } catch (e) {}

  const ids = Object.keys(images);
  log('开始本地化：共 ' + ids.length + ' 件作品，目标 ' + OUTDIR);

  for (let n = 0; n < ids.length; n++) {
    const id = ids[n];
    const im = images[id];
    const tName = id + '-t.jpg';
    const dName = id + '-d.jpg';
    const tPath = path.join(OUTDIR, tName);
    const dPath = path.join(OUTDIR, dName);
    const rel = (f) => 'assets/img/' + f;

    const haveT = fs.existsSync(tPath) && fs.statSync(tPath).size > 1000;
    const haveD = fs.existsSync(dPath) && fs.statSync(dPath).size > 1000;
    if (haveT && haveD && local[id] && local[id].detail) { log('[' + (n + 1) + '/' + ids.length + '] ' + id + '  已存在，跳过'); continue; }

    let ok = true;
    if (!haveT) {
      const b = im.source === 'met' ? await fetchWithRetry(im.small, 4) : await fetchWidths(im.file, THUMB_W);
      if (!b) { ok = false; log('[' + (n + 1) + '/' + ids.length + '] ' + id + '  ✗ thumb 失败'); }
      else fs.writeFileSync(tPath, b);
    }
    if (ok && !haveD) {
      const b = im.source === 'met' ? await fetchWithRetry(im.small, 4) : await fetchWidths(im.file, DETAIL_W);
      if (!b) { ok = false; log('[' + (n + 1) + '/' + ids.length + '] ' + id + '  ✗ detail 失败'); }
      else fs.writeFileSync(dPath, b);
    }
    if (!ok) { await new Promise((s) => setTimeout(s, 1200)); continue; }

    const dim = jpegSize(fs.readFileSync(dPath)) || { w: im.w || 4, h: im.h || 3 };
    local[id] = {
      thumb: rel(tName),
      detail: rel(dName),
      zoom: rel(dName),
      w: dim.w,
      h: dim.h,
      bytes: fs.statSync(tPath).size + fs.statSync(dPath).size,
    };
    fs.writeFileSync(LOCAL, JSON.stringify(local, null, 1));
    log('[' + (n + 1) + '/' + ids.length + '] ' + id + '  ✓ ' + dim.w + 'x' + dim.h +
      '  ' + Math.round(local[id].bytes / 1024) + 'KB');
    await new Promise((s) => setTimeout(s, 250));
  }

  const done = Object.keys(local).length;
  const total = Object.values(local).reduce((a, b) => a + (b.bytes || 0), 0);
  log('本地化完成：' + done + ' / ' + ids.length + ' 件，共 ' + (total / 1048576).toFixed(1) + ' MB');
  try { fs.writeFileSync(LOG, '本地化完成：' + done + ' / ' + ids.length + ' 件，共 ' + (total / 1048576).toFixed(1) + ' MB\n'); } catch (e) {}
})();