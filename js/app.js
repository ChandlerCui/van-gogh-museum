/* ============================================================
   梵高线上博物馆 · 前端应用
   纯静态、hash 路由：#/ 首页 · #/period/N 展厅 · #/work/ID 作品页 · #/search/Q 搜索
   数据来自 js/data.js（由 tools/build.js 从 content/ 构建）
   ============================================================ */
(function () {
  'use strict';

  var VGM = window.VGM;
  var view = document.getElementById('view');
  var periodNav = document.getElementById('periodNav');
  var footerPeriods = document.getElementById('footerPeriods');
  var searchForm = document.getElementById('searchForm');
  var searchInput = document.getElementById('searchInput');
  var lightbox = document.getElementById('lightbox');
  var lightboxImg = document.getElementById('lightboxImg');
  var lightboxCaption = document.getElementById('lightboxCaption');

  var byId = {};
  VGM.works.forEach(function (w) { byId[w.id] = w; });
  var byPeriod = {};
  VGM.periods.forEach(function (p) { byPeriod[p.id] = p; });

  /* ———— 工具 ———— */
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function pad(n) {
    return (n < 10 ? '0' : '') + n;
  }
  function worksOf(pid) {
    return VGM.works.filter(function (w) { return w.period === pid; });
  }
  function para(text) {
    return String(text).split('\n').map(function (t) {
      return t.trim() ? '<p>' + esc(t) + '</p>' : '';
    }).join('');
  }

  /* ———— 导航与页脚 ———— */
  function buildNav() {
    periodNav.innerHTML = VGM.periods.map(function (p) {
      return '<a href="#/period/' + p.id + '" data-period="' + p.id + '" title="' + esc(p.title) + '">' +
        '<span class="nav-num">' + p.num + '</span>' +
        '<span class="nav-name">' + esc(p.short || p.title) + '</span>' +
        '<span class="nav-mini">' + esc(p.mini || p.short || p.title) + '</span></a>';
    }).join('');
    footerPeriods.innerHTML = VGM.periods.map(function (p) {
      return '<li><a href="#/period/' + p.id + '">' + p.num + '　' + esc(p.title) + '　<span style="opacity:.6">' + esc(p.years) + '</span></a></li>';
    }).join('');
  }
  function setActiveNav(pid) {
    Array.prototype.forEach.call(periodNav.querySelectorAll('a'), function (a) {
      a.classList.toggle('active', a.getAttribute('data-period') === String(pid));
    });
  }

  /* ———— 首页 ———— */
  function renderHome() {
    var heroWork = byId['p4-starry-night'] || VGM.works[0];
    var total = VGM.works.length;

    var total2 = VGM.periods.length;
    var timeline = VGM.periods.map(function (p, i) {
      return '<a class="timeline-item" style="--accent:' + p.accent + '" href="#/period/' + p.id + '">' +
        '<div class="timeline-index">' + pad(i + 1) + ' / ' + pad(total2) + '</div>' +
        '<div class="timeline-num">' + p.num + '</div>' +
        '<div class="timeline-title">' + esc(p.short || p.title) + '</div>' +
        '<div class="timeline-years">' + esc(p.years) + '</div>' +
        '<div class="timeline-count">' + (p.count ? p.count + ' 件作品' : '序章 · 暂无藏品') + '</div>' +
        '</a>';
    }).join('');

    var chapters = VGM.periods.map(function (p, i) {
      var cover = p.cover && byId[p.cover];
      var excerpt = p.preface.replace(/\n/g, '').slice(0, 84) + '……';
      return '<a class="chapter-card reveal" style="--i:' + (i > 6 ? 6 : i) + '" href="#/period/' + p.id + '">' +
        '<figure class="chapter-fig">' + (cover ? '<img src="' + cover.img.thumb + '" alt="' + esc(cover.zh) + '" loading="lazy" decoding="async">' : '') +
        '<span class="chapter-badge">' + p.num + '</span></figure>' +
        '<div class="chapter-body">' +
        '<div class="chapter-title">' + esc(p.title) + '</div>' +
        '<div class="chapter-meta">' + esc(p.years) + ' · ' + esc(p.places) + '</div>' +
        '<div class="chapter-excerpt">' + esc(excerpt) + '</div>' +
        '<div class="chapter-more">进入展厅 →</div>' +
        '</div></a>';
    }).join('');

    view.innerHTML =
      '<section class="hero">' +
        '<div class="hero-grid">' +
          '<div>' +
            '<div class="kicker">ONLINE MUSEUM · 线上博物馆</div>' +
            '<h1>梵高的<br>十年与七个展厅</h1>' +
            '<p class="lead">从二十七岁在矿区拿起铅笔，到三十七岁倒在奥维尔的麦田边——文森特·梵高的创作生涯只有十年，却辗转七处居所，换了七种颜色。本馆按时间顺序将其分为七个时期，每章一篇序，每件作品皆有创作背景与赏析。</p>' +
            '<blockquote class="hero-quote">我想用作品打动人。我想让他们说：这个人感受得很深，这个人爱得很深。<small>—— 文森特·梵高，1880年7月，致提奥的信</small></blockquote>' +
            '<div class="hero-actions">' +
              '<a class="btn primary" href="#/period/1">从第一展厅开始</a>' +
              (heroWork ? '<a class="btn" href="#/work/' + heroWork.id + '">直达《星月夜》</a>' : '') +
            '</div>' +
          '</div>' +
          '<figure class="hero-fig reveal">' +
            '<div class="frame"><img src="' + heroWork.img.detail + '" alt="' + esc(heroWork.zh) + '" decoding="async" fetchpriority="high"></div>' +
            '<figcaption>' + esc(heroWork.zh) + ' · ' + esc(heroWork.date) + ' · ' + esc(heroWork.location) + '</figcaption>' +
          '</figure>' +
        '</div>' +
      '</section>' +

      '<section class="section route-band">' +
        '<div class="section-head"><h2 class="section-title">参观动线</h2><span class="section-sub">A ROUTE THROUGH SEVEN CHAPTERS · 1880 — 1890 · 共 ' + total + ' 件作品</span></div>' +
        '<nav class="timeline">' + timeline + '</nav>' +
      '</section>' +

      '<section class="section" style="padding-top:10px">' +
        '<div class="section-head"><h2 class="section-title">七个展厅</h2><span class="section-sub">点击进入 · 每章一篇序</span></div>' +
        '<div class="chapters-grid">' + chapters + '</div>' +
      '</section>';

    document.title = '梵高线上博物馆 · Van Gogh Online Museum';
    setActiveNav(null);
  }

  /* ———— 展厅页 ———— */
  function renderPeriod(pid) {
    var p = byPeriod[pid];
    if (!p) { location.hash = '#/'; return; }

    var list = worksOf(pid);
    var next = VGM.periods.filter(function (x) { return x.id > p.id; })[0];
    var cards = list.map(function (w, i) {
      return '<a class="work-card reveal" style="--i:' + (i > 8 ? 8 : i) + '" href="#/work/' + w.id + '">' +
        '<figure class="work-card-fig" style="aspect-ratio:' + (w.img.w / w.img.h).toFixed(4) + '">' +
          '<img src="' + w.img.thumb + '" alt="' + esc(w.zh) + '" loading="lazy" decoding="async">' +
        '</figure>' +
        '<div class="work-card-body">' +
          (w.featured ? '<span class="tag-featured">馆藏代表作</span>' : '') +
          '<h3>' + esc(w.zh) + '</h3>' +
          '<p class="work-card-en">' + esc(w.en) + '</p>' +
          '<p class="work-card-meta">' + esc(w.date) + ' · ' + esc(w.location) + '</p>' +
        '</div></a>';
    }).join('');

    view.innerHTML =
      '<section class="period-hero">' +
        '<div class="kicker">CHAPTER ' + p.id + ' / ' + VGM.periods.length + ' · ' + esc(p.en) + '</div>' +
        '<div class="num">' + p.num + '</div>' +
        '<h1 style="color:' + p.accent + '">' + esc(p.title) + '</h1>' +
        '<div class="en">' + esc(p.en) + '</div>' +
        '<div class="meta-line"><span><b>' + esc(p.years) + '</b></span><span>' + esc(p.places) + '</span><span>' + p.count + ' 件作品</span></div>' +
      '</section>' +

      '<section class="preface">' +
        '<h2>序 · PREFACE</h2>' +
        para(p.preface) +
        '<p class="preface-sign">—— 本时期导览</p>' +
      '</section>' +

      '<section class="section" style="padding-top:26px">' +
        '<div class="section-head"><h2 class="section-title" style="color:' + p.accent + '">作品陈列</h2><span class="section-sub">' + (list.length ? '按创作时间排序 · 共 ' + list.length + ' 件' : '本展厅暂无藏品') + '</span></div>' +
        (list.length
          ? '<div class="works-grid">' + cards + '</div>'
          : '<div class="hall-empty">' +
              '<p>这一时期的作品以素描、水彩与小幅油画为主，本馆藏品暂未覆盖这段行程。</p>' +
              '<p>序章已经把这段路讲完：他在安特卫普第一次撞见鲁本斯的红、第一次把日本浮世绘贴满墙壁，也第一次决定把自己画面里的泥土味调亮。</p>' +
              (next ? '<a class="btn primary" href="#/period/' + next.id + '">前往 ' + esc(next.title) + ' →</a>' : '') +
            '</div>') +
      '</section>';

    document.title = p.num + ' ' + p.title + '（' + p.years + '）· 梵高线上博物馆';
    setActiveNav(pid);
  }

  /* ———— 作品页 ———— */
  function renderWork(id) {
    var w = byId[id];
    if (!w) { location.hash = '#/'; return; }
    var p = byPeriod[w.period];
    var list = worksOf(w.period);
    var i = list.indexOf(w);
    var prev = list[i - 1];
    var next = list[i + 1];

    var interp =
      '<div class="interp">' +
      (w.interp.background
        ? '<h2>创作背景</h2>' + para(w.interp.background)
        : '') +
      (w.interp.analysis
        ? '<h2>作品赏析</h2>' + para(w.interp.analysis)
        : '') +
      (w.interp.note
        ? '<h2>简注</h2>' + para(w.interp.note)
        : '') +
      '</div>';

    view.innerHTML =
      '<article class="work-page">' +
        '<div class="work-layout">' +
          '<figure class="work-fig reveal">' +
            '<div class="frame" data-zoom="' + w.img.zoom + '" data-zh="' + esc(w.zh) + '" data-cap="' + esc(w.en + ' · ' + w.date) + '">' +
              '<img src="' + w.img.detail + '" alt="' + esc(w.zh) + '" decoding="async" fetchpriority="high">' +
              '<span class="zoom-hint">点击放大观看</span>' +
            '</div>' +
            '<figcaption>' + esc(w.img.credit) + '</figcaption>' +
          '</figure>' +
          '<div class="work-info">' +
            '<div class="crumb"><a href="#/period/' + p.id + '">' + p.num + ' ' + esc(p.title) + '</a> ／ 作品</div>' +
            '<h1>' + esc(w.zh) + '</h1>' +
            '<div class="en">' + esc(w.en) + '</div>' +
            '<div class="meta-table">' +
              '<div class="row"><span class="k">年代</span><span class="v">' + esc(w.date) + '</span></div>' +
              '<div class="row"><span class="k">媒介</span><span class="v">' + esc(w.medium) + '</span></div>' +
              (w.dims ? '<div class="row"><span class="k">尺寸</span><span class="v">' + esc(w.dims) + '</span></div>' : '') +
              '<div class="row"><span class="k">现藏</span><span class="v">' + esc(w.location) + '</span></div>' +
            '</div>' +
            interp +
            '<nav class="work-nav">' +
              (prev
                ? '<a href="#/work/' + prev.id + '"><span class="dir">← 上一件</span>' + esc(prev.zh) + '</a>'
                : '<a href="#/period/' + p.id + '"><span class="dir">← 回到展厅</span>' + esc(p.title) + '作品陈列</a>') +
              (next
                ? '<a class="next" href="#/work/' + next.id + '"><span class="dir">下一件 →</span>' + esc(next.zh) + '</a>'
                : '<a class="next" href="#/period/' + p.id + '"><span class="dir">回到展厅 →</span>' + esc(p.title) + '作品陈列</a>') +
            '</nav>' +
          '</div>' +
        '</div>' +
      '</article>';

    document.title = w.zh + '（' + w.date + '）· 梵高线上博物馆';
    setActiveNav(w.period);
  }

  /* ———— 搜索页 ———— */
  function renderSearch(q) {
    var kw = (q || '').trim().toLowerCase();
    var hits = kw
      ? VGM.works.filter(function (w) {
          return (w.zh + ' ' + w.en + ' ' + w.date + ' ' + w.location + ' ' + byPeriod[w.period].title).toLowerCase().indexOf(kw) > -1;
        })
      : [];

    var cards = hits.map(function (w) {
      var p = byPeriod[w.period];
      return '<a class="work-card reveal" href="#/work/' + w.id + '">' +
        '<figure class="work-card-fig" style="aspect-ratio:' + (w.img.w / w.img.h).toFixed(4) + '">' +
          '<img src="' + w.img.thumb + '" alt="' + esc(w.zh) + '" loading="lazy" decoding="async">' +
        '</figure>' +
        '<div class="work-card-body">' +
          '<p class="work-card-meta" style="margin:0 0 6px">' + p.num + ' ' + esc(p.title) + '</p>' +
          '<h3>' + esc(w.zh) + '</h3>' +
          '<p class="work-card-en">' + esc(w.en) + '</p>' +
          '<p class="work-card-meta">' + esc(w.date) + ' · ' + esc(w.location) + '</p>' +
        '</div></a>';
    }).join('');

    view.innerHTML =
      '<section class="search-page">' +
        '<div class="search-head">' +
          '<div class="kicker">SEARCH · 馆内检索</div>' +
          '<h1>“' + esc(q) + '” 的检索结果</h1>' +
        '</div>' +
        (hits.length
          ? '<div class="works-grid">' + cards + '</div>'
          : '<p class="search-empty">未找到相关作品。可尝试作品名（如“向日葵”）、馆藏地（如“奥赛”）或时期（如“阿尔勒”）。</p>') +
      '</section>';

    document.title = '搜索：' + q + ' · 梵高线上博物馆';
    setActiveNav(null);
  }

  /* ———— 灯箱 ———— */
  function openLightbox(frame) {
    lightboxImg.src = frame.getAttribute('data-zoom');
    lightboxImg.alt = frame.getAttribute('data-zh');
    lightboxCaption.textContent = frame.getAttribute('data-zh') + '　' + frame.getAttribute('data-cap');
    lightbox.hidden = false;
    document.body.style.overflow = 'hidden';
  }
  function closeLightbox() {
    lightbox.hidden = true;
    lightboxImg.src = '';
    document.body.style.overflow = '';
  }
  lightbox.addEventListener('click', function (e) {
    if (e.target === lightbox || e.target.closest('.lightbox-close')) closeLightbox();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeLightbox();
  });

  /* ———— 进场动画 ———— */
  var observer = ('IntersectionObserver' in window)
    ? new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) { en.target.classList.add('in'); observer.unobserve(en.target); }
        });
      }, { threshold: 0.06 })
    : null;

  function armReveals() {
    if (!observer) {
      Array.prototype.forEach.call(view.querySelectorAll('.reveal'), function (el) { el.classList.add('in'); });
      return;
    }
    Array.prototype.forEach.call(view.querySelectorAll('.reveal:not(.in)'), function (el) { observer.observe(el); });
  }

  /* ———— 事件与路由 ———— */
  view.addEventListener('click', function (e) {
    var frame = e.target.closest('.work-fig .frame');
    if (frame) openLightbox(frame);
    var cardFig = e.target.closest('.work-card-fig');
    if (cardFig) {
      var card = e.target.closest('.work-card');
      if (card) location.hash = '#/work/' + card.getAttribute('href').replace('#/work/', '');
    }
  });

  searchForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var q = searchInput.value.trim();
    location.hash = q ? '#/search/' + encodeURIComponent(q) : '#/';
  });

  function route() {
    var h = location.hash || '#/';
    var m;
    if (h === '#/' || h === '#') renderHome();
    else if ((m = h.match(/^#\/period\/(\d+)$/))) renderPeriod(parseInt(m[1], 10));
    else if ((m = h.match(/^#\/work\/([A-Za-z0-9_-]+)$/))) renderWork(m[1]);
    else if ((m = h.match(/^#\/search\/(.+)$/))) renderSearch(decodeURIComponent(m[1]));
    else renderHome();
    window.scrollTo(0, 0);
    view.classList.remove('view-enter');
    void view.offsetWidth;
    view.classList.add('view-enter');
    armReveals();
  }

  buildNav();
  window.addEventListener('hashchange', route);
  route();

  /* ———— 顶栏滚动态 ———— */
  var siteHeader = document.querySelector('.site-header');
  function onScroll() {
    if (siteHeader) siteHeader.classList.toggle('scrolled', window.scrollY > 8);
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
})();
