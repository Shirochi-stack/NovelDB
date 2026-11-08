/*
 * hard-sources.js - page-side logic for the qdn and rbooks sources.
 *
 * One file for every platform. It runs inside a web view (Android WebView, iOS WKWebView) and has no
 * native dependencies, so the iOS app loads the same bytes. The native side only navigates, keeps the
 * sign-in session, paces requests and turns the JSON this file returns into BookInfo / ChapterResult.
 * The contract (entry points, JSON shapes, resources) is android/docs/hard-sources.md.
 *
 * Load it two ways, both safe to repeat:
 *   - as a document-start user script on the qdn pages (it installs the font hook before the reader runs), and
 *   - with a plain evaluate on any page that needs the entry points (including a blank page for decoding).
 *
 * Entry points live under window.__npiaHard.qdn and window.__npiaHard.rbooks. Every function returns
 * plain JSON (objects, arrays, strings, numbers, booleans) or a promise of it.
 *
 * Ported from the desktop external_scraper.py (_qdn_*, _rbooks_*) and qdn_font_decoder.py.
 */
(function (root) {
  'use strict';
  const VERSION = 1;
  if (root.__npiaHard && root.__npiaHard.version >= VERSION) return;

  // Site names. Kept as escapes so tracked files carry codenames only (see AGENTS.md).
  const SITE = Object.freeze({
    qdnDomain: '\u0071\u0069\u0064\u0069\u0061\u006e.com',
    rbooksDomain: '\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com',
    rbooksCdn: '\u0072\u0069\u0064\u0069\u0063\u0064\u006e.net',
    rbooksBorderClass: '\u0072\u0069\u0064\u0069border',
    rbooksCenterClass: '\u0072\u0069\u0064\u0069center',
    mpiaDomain: '\u006d\u0075\u006e\u0070\u0069\u0061.com',
  });

  const hard = { version: VERSION, qdn: {}, rbooks: {}, mpia: {}, ntk: {}, util: {} };
  const util = hard.util;

  // ---------------------------------------------------------------------------------------------
  // Shared helpers
  // ---------------------------------------------------------------------------------------------

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const clean = (value) => String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
  const text = (el) => clean(el && el.textContent);
  const escapeHtml = (value) => String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const absolute = (value, base) => {
    if (!value) return '';
    try { return new URL(value, base || location.href).href; } catch (e) { return String(value); }
  };
  const metaContent = (...keys) => {
    for (const key of keys) {
      const node = document.querySelector(`meta[property="${key}"], meta[name="${key}"]`);
      const value = node && node.getAttribute('content');
      if (value && value.trim()) return value.trim();
    }
    return '';
  };
  const pageContext = () => {
    // Server-rendered reader state: {pageContext: {pageProps: {pageData}}}.
    try {
      const node = document.querySelector('#vite-plugin-ssr_pageContext');
      return JSON.parse((node && node.textContent) || 'null');
    } catch (e) { return null; }
  };
  const pageData = () => {
    const ctx = pageContext();
    return (ctx && ctx.pageContext && ctx.pageContext.pageProps && ctx.pageContext.pageProps.pageData) || null;
  };

  util.sleep = sleep;
  util.escapeHtml = escapeHtml;

  // Base64 without atob/btoa size limits and without building huge intermediate strings.
  const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  const B64_LOOKUP = (() => {
    const table = new Int16Array(256).fill(-1);
    for (let i = 0; i < B64.length; i += 1) table[B64.charCodeAt(i)] = i;
    table[45] = 62; // '-' and '_' are the url-safe forms
    table[95] = 63;
    return table;
  })();
  const base64ToBytes = (input) => {
    const source = String(input || '');
    let length = source.length;
    while (length > 0 && (source.charCodeAt(length - 1) === 61 || source.charCodeAt(length - 1) <= 32)) length -= 1;
    const out = new Uint8Array(Math.floor((length * 3) / 4));
    let buffer = 0, bits = 0, position = 0;
    for (let i = 0; i < length; i += 1) {
      const value = B64_LOOKUP[source.charCodeAt(i) & 255];
      if (value < 0) continue;
      buffer = (buffer << 6) | value;
      bits += 6;
      if (bits >= 8) {
        bits -= 8;
        out[position] = (buffer >> bits) & 255;
        position += 1;
      }
    }
    return position === out.length ? out : out.subarray(0, position);
  };
  const bytesToBase64 = (input) => {
    const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
    const parts = [];
    for (let i = 0; i < bytes.length; i += 0x6000) {
      let chunk = '';
      const end = Math.min(bytes.length, i + 0x6000);
      for (let j = i; j < end; j += 1) chunk += String.fromCharCode(bytes[j]);
      parts.push(chunk);
    }
    return btoa(parts.join(''));
  };
  util.base64ToBytes = base64ToBytes;
  util.bytesToBase64 = bytesToBase64;

  // SHA-256 as lower-case hex. crypto.subtle needs a secure context, so a plain implementation backs it up.
  const K256 = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]);
  const sha256Plain = (data) => {
    const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
    const total = bytes.length;
    const padded = new Uint8Array(((total + 9 + 63) >> 6) << 6);
    padded.set(bytes);
    padded[total] = 0x80;
    const view = new DataView(padded.buffer);
    view.setUint32(padded.length - 8, Math.floor(total / 0x20000000), false);
    view.setUint32(padded.length - 4, (total << 3) >>> 0, false);
    const h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
    const w = new Uint32Array(64);
    for (let offset = 0; offset < padded.length; offset += 64) {
      for (let i = 0; i < 16; i += 1) w[i] = view.getUint32(offset + i * 4, false);
      for (let i = 16; i < 64; i += 1) {
        const a = w[i - 15], b = w[i - 2];
        const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3);
        const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      let a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
      for (let i = 0; i < 64; i += 1) {
        const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
        const ch = (e & f) ^ (~e & g);
        const t1 = (hh + S1 + ch + K256[i] + w[i]) | 0;
        const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (S0 + maj) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      h[0] = (h[0] + a) | 0; h[1] = (h[1] + b) | 0; h[2] = (h[2] + c) | 0; h[3] = (h[3] + d) | 0;
      h[4] = (h[4] + e) | 0; h[5] = (h[5] + f) | 0; h[6] = (h[6] + g) | 0; h[7] = (h[7] + hh) | 0;
    }
    let hex = '';
    for (let i = 0; i < 8; i += 1) hex += (h[i] >>> 0).toString(16).padStart(8, '0');
    return hex;
  };
  const sha256Hex = async (data) => {
    const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
    try {
      if (root.crypto && root.crypto.subtle && root.isSecureContext !== false) {
        const digest = await root.crypto.subtle.digest('SHA-256', bytes);
        return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
      }
    } catch (e) { /* fall through */ }
    return sha256Plain(bytes);
  };
  util.sha256Hex = sha256Hex;
  util.sha256Plain = sha256Plain;

  // A page is a block page (firewall / bot check) when its title or text says so.
  const verificationPattern = /TencentCaptcha|WafCaptcha|__captcha/i;
  const verificationTextPattern = /captcha|verify|verification|验证|安全|人机|滑块/i;
  const bodyText = () => (document.body && (document.body.innerText || document.body.textContent)) || '';
  const wafBlocked = () => /WAF/.test(document.title || '') || /您的请求已中断/.test(bodyText());

  // ---------------------------------------------------------------------------------------------
  // qdn: page side (book page, chapter page, reader extraction)
  // ---------------------------------------------------------------------------------------------

  const qdn = hard.qdn;

  // Reader fonts. The reader builds one FontFace in script; record it (and a copy of its bytes) before
  // the reader runs, because it is visible in no stylesheet.
  qdn.installFontHook = function () {
    const Native = root.FontFace;
    if (!Native || Native.__npiaHooked) return false;
    const records = [];
    Object.defineProperty(root, '__npiaFontFaces', { value: records, configurable: true });
    const Hooked = function FontFace(family, source, descriptors) {
      const face = new Native(family, source, descriptors);
      let copy = source;
      try {
        if (source instanceof ArrayBuffer) copy = source.slice(0);
        else if (ArrayBuffer.isView(source)) {
          copy = source.buffer.slice(source.byteOffset, source.byteOffset + source.byteLength);
        }
      } catch (e) { /* keep the original */ }
      records.push({ family, source: copy, descriptors: descriptors || {}, face });
      if (records.length > 64) records.splice(0, records.length - 64);
      return face;
    };
    Hooked.prototype = Native.prototype;
    Object.defineProperty(Hooked, 'name', { value: 'FontFace' });
    Hooked.toString = () => 'function FontFace() { [native code] }';
    Hooked.__npiaHooked = true;
    Hooked.__npiaNative = Native;
    root.FontFace = Hooked;
    return true;
  };

  /** Book page readiness: 'ready', 'verification' (captcha / bot check), 'blocked' (firewall) or 'waiting'. */
  qdn.pageState = function () {
    const html = (document.documentElement && document.documentElement.innerHTML) || '';
    const catalogCount = document.querySelectorAll('#bookCatalogSection a.chapter-name, a.chapter-name').length;
    const hasTitle = !!(document.querySelector('#bookName') || document.querySelector('h1'));
    let state = 'waiting';
    if (verificationPattern.test(html)) state = 'verification';
    else if (wafBlocked()) state = 'blocked';
    // A page still being parsed lists only the chapters that have arrived, so it is not ready yet.
    else if (document.readyState !== 'loading' && catalogCount > 0 && (hasTitle || bodyText().trim().length > 100)) state = 'ready';
    return {
      state, url: location.href, title: document.title || '', readyState: document.readyState,
      text: clean(bodyText()).slice(0, 240), catalogCount, bookName: !!document.querySelector('#bookName'),
      viewport: { width: window.innerWidth, height: window.innerHeight },
    };
  };

  /** Metadata and catalog of the book page. */
  qdn.bookInfo = function () {
    const meta = metaContent;
    const title = text(document.querySelector('#bookName')) || meta('og:title')
      || text(document.querySelector('h1')) || (document.title || '').split('-')[0].trim();
    const author = (text(document.querySelector('span.author')) || text(document.querySelector('[class*="author"]')))
      .replace(/^作者[:：]\s*/, '').trim();
    const coverEl = document.querySelector('#bookImg img, img#bookImg, #bookImg')
      || [...document.images].find((img) => /bookcover\.yuewen\.com/.test(img.src));
    const cover = absolute((coverEl && (coverEl.currentSrc || coverEl.src)) || meta('og:image'));
    const description = text(document.querySelector('#book-intro-detail')) || meta('og:description', 'description');
    const seen = new Set();
    const tags = [...document.querySelectorAll('#all-label a, .book-tag a, .tag-wrap a, a[href*="/all/"]')]
      .map((el) => text(el)).filter((tag) => tag && !seen.has(tag) && seen.add(tag));
    const chapters = [...document.querySelectorAll('#bookCatalogSection a.chapter-name, a.chapter-name')]
      .map((a, index) => {
        const parent = a.closest('li, dd, div') || a.parentElement || a;
        const name = text(a) || `Chapter ${index + 1}`;
        // VIP is marked on the chapter row (older layout) or only on its volume's heading
        // ("订阅本卷 ... 共270章 VIP" against "... 共130章 免费").
        const volume = a.closest('.catalog-volume, .volume');
        const heading = volume ? text(volume.querySelector('.volume-header, h3')) : '';
        const volumeVip = /VIP/.test(heading) && !/免费/.test(heading);
        return {
          url: absolute(a.href || a.getAttribute('href') || ''),
          name,
          isVip: volumeVip || /VIP|付费|订阅/.test(text(parent)),
        };
      }).filter((chapter) => chapter.url);
    // A long book's page lists only the first chapters; "完本共1418章" says how many there are and the
    // heading links to the page that lists them all.
    const total = /共\s*(\d+)\s*章/.exec(text(document.querySelector('.catalog-header-desc')) || '');
    const catalogLink = document.querySelector('a.catalog-header-title[href], a[href$="/catalog/"]');
    return {
      title, author: author || '', coverUrl: cover, description, tags, chapters, bookUrl: location.href,
      totalChapters: total ? parseInt(total[1], 10) : 0,
      catalogUrl: catalogLink ? absolute(catalogLink.getAttribute('href')) : '',
    };
  };

  /** Full catalog page (`/book/<id>/catalog/`): 'ready', 'verification', 'blocked' or 'waiting', with the chapter count. */
  qdn.catalogState = function () {
    const html = (document.documentElement && document.documentElement.innerHTML) || '';
    const count = document.querySelectorAll('.volume li a[href*="/chapter/"]').length;
    let state = 'waiting';
    if (verificationPattern.test(html)) state = 'verification';
    else if (wafBlocked()) state = 'blocked';
    else if (count > 0 && document.readyState !== 'loading') state = 'ready';
    return { state, count, readyState: document.readyState, url: location.href, title: document.title || '' };
  };

  /**
   * Every chapter of the book from the full catalog page. A volume's heading says whether it is VIP
   * ("订阅本卷 ... 共270章VIP") or free ("... 共130章免费"); its chapters share that.
   * Result: {chapters: [{url, name, isVip}], volumes: [{name, count, isVip}]}
   */
  qdn.catalogInfo = function () {
    const chapters = [];
    const volumes = [];
    const seen = new Set();
    for (const volume of document.querySelectorAll('.volume-wrap .volume, .volume-wrap > .volume-chapters, .volume')) {
      if (volume.querySelector('.volume')) continue; // a wrapper, not a volume
      const header = volume.querySelector('h3, .volume-header, [class*="volume-name"]') || volume.firstElementChild;
      const name = clean(header && header.textContent);
      const isVip = /VIP/.test(name) && !/免费/.test(name);
      let count = 0;
      for (const link of volume.querySelectorAll('li a[href*="/chapter/"]')) {
        const url = absolute(link.getAttribute('href'));
        if (!url || seen.has(url)) continue;
        seen.add(url);
        count += 1;
        chapters.push({ url, name: text(link) || `Chapter ${chapters.length + 1}`, isVip });
      }
      volumes.push({ name, count, isVip });
    }
    return { chapters, volumes };
  };

  /** Chapter page readiness (the reader is parsed and has text, a decrypt in progress, or a challenge). */
  qdn.chapterState = function () {
    const main = document.querySelector('div.chapter-wrapper div.print main, main.content, #reader main, main');
    const mainText = ((main && (main.innerText || main.textContent)) || '').trim();
    const body = bodyText();
    const html = (document.documentElement && document.documentElement.innerHTML) || '';
    const challenge = verificationPattern.test(html);
    const textChallenge = /VIP|订阅|购买|captcha|verify|verification|验证|安全|人机|滑块/i.test(body);
    const parsed = document.readyState !== 'loading';
    const info = (pageData() || {}).chapterInfo || {};
    const encrypted = !!document.querySelector('main.r-font-encrypt')
      || (Number(info.isBuy) > 0 && (Number(info.cES) > 0 || Number(info.fEnS) > 0));
    // Author notes ("欢迎收藏") are shorter than any length threshold; once the page data names the
    // chapter and <main> has text, it is done.
    const expected = Number(info.actualWords || info.wordsCount || 0);
    const complete = !!info.chapterId && mainText.length > 0
      && (expected === 0 || mainText.length >= Math.min(100, expected * 0.5));
    const ready = (parsed && encrypted) || (parsed && complete)
      || (parsed && (document.querySelectorAll('span.content-text').length >= 3 || mainText.length > 100))
      || challenge || (parsed && textChallenge);
    return { ready: !!ready, blocked: wafBlocked(), verification: challenge, encrypted };
  };

  /** Progress of an encrypted (font-scrambled or encrypted) bought chapter. */
  qdn.encryptionState = function () {
    const info = (pageData() || {}).chapterInfo || {};
    const cipherMain = document.querySelector('main.r-font-encrypt');
    // Only font-scrambled chapters get main.r-font-encrypt. A bought chapter that is encrypted without
    // the font cipher renders into the ordinary reader <main>, so either counts once its paragraphs are drawn.
    const main = cipherMain || document.querySelector('main[id^="c-"], div.chapter-wrapper div.print main, main.content');
    const body = document.body ? document.body.innerText || '' : '';
    return {
      encrypted: !!cipherMain || (Number(info.isBuy) > 0 && (Number(info.cES) > 0 || Number(info.fEnS) > 0)),
      rendered: !!main && main.querySelectorAll('p').length > 0
        && (main.innerText || '').trim().length > 50 && document.fonts.status === 'loaded',
      failed: /章节加载失败/.test(body),
      blocked: wafBlocked(),
      words: Number(info.actualWords || info.wordsCount) || 0,
    };
  };

  /**
   * The reader's own sign-in check (GetUserInfo). {live: true|false|null}; null when the answer is not JSON
   * (the firewall page). Run it on a qdn page.
   */
  qdn.userInfo = async function () {
    try {
      const match = document.cookie.match(/(?:^|;\s*)_csrfToken=([^;]*)/);
      const csrf = match ? match[1] : '';
      const response = await fetch(`/ajax/UserInfo/GetUserInfo?_csrfToken=${encodeURIComponent(csrf)}`,
        { credentials: 'include', headers: { Accept: 'application/json' } });
      const data = await response.json();
      if (data && data.code === 0) {
        const login = (data.data || {}).isLogin;
        return { live: login === undefined ? true : !!login };
      }
      if (data && data.code === 1000) return { live: false };
    } catch (e) { /* not JSON */ }
    return { live: null };
  };

  /**
   * Reads a chapter the reader draws as ordinary text. Result:
   *   {lines: [paragraph...]}                       the text, or
   *   {error: 'locked', reason: 'login'|'purchase', chars, expected}
   *   {error: 'verification'} | {error: 'missing content'} | {error: 'empty content'}
   */
  qdn.readChapter = function () {
    // Older chapters scramble characters with CSS classes (content: "\XXXX") over a qd_* font.
    const deobfuscate = () => {
      const content = document.querySelector('div.chapter-wrapper div.print main, main.content, #reader main, main');
      if (!content) return;
      const spans = [...content.querySelectorAll('span.content-text')];
      if (!spans.length) return;
      const fontMatch = (spans[0].getAttribute('style') || '').match(/font-family:\s*["']?(qd_[^"',;\s]+)/);
      if (!fontMatch) return;
      const mapping = {};
      for (const styleEl of document.querySelectorAll('style')) {
        const css = styleEl.textContent || '';
        if (!css.includes(fontMatch[1])) continue;
        const rule = /\.([a-zA-Z0-9_-]+)\s*\{[^}]*content\s*:\s*["']\\([0-9a-fA-F]{1,6})["'][^}]*\}/g;
        let m;
        while ((m = rule.exec(css)) !== null) mapping[m[1]] = parseInt(m[2], 16);
      }
      if (!Object.keys(mapping).length) return;
      for (const span of spans) {
        for (const cls of span.classList) {
          if (Object.prototype.hasOwnProperty.call(mapping, cls)) {
            span.textContent = String.fromCodePoint(mapping[cls]);
            break;
          }
        }
      }
    };
    deobfuscate();
    const main = document.querySelector('div.chapter-wrapper div.print main, main.content, #reader main, main');
    const body = bodyText();
    const html = (document.documentElement && document.documentElement.innerHTML) || '';
    const needsVerification = verificationPattern.test(html) || verificationTextPattern.test(body);
    const lockedHint = /VIP|订阅|购买|登录/.test(body);
    if (!main) {
      if (needsVerification) return { error: 'verification' };
      return { error: lockedHint ? 'locked' : 'missing content' };
    }
    const clone = main.cloneNode(true);
    clone.querySelectorAll(
      'script, style, noscript, button, textarea, input, svg, canvas, span.review, span.review-count, '
      + '.ql-block-token, [class*="review"], [class*="comment"], [class*="vote"], [class*="ad"]',
    ).forEach((el) => el.remove());
    let paras = [...clone.querySelectorAll('p')].map((p) => text(p)).filter(Boolean);
    if (paras.length < 2) {
      paras = (clone.innerText || clone.textContent || '').split(/\n+/)
        .map((line) => line.replace(/\s+/g, ' ').trim()).filter(Boolean);
    }
    paras = paras.filter((line) => !/^(\d+\s*){1,4}$/.test(line));
    const contentText = paras.join('\n');
    // A guest or unpurchased VIP chapter renders a few real paragraphs and a subscribe prompt. The
    // page data says which one this is.
    const data = pageData();
    const info = (data && data.chapterInfo) || {};
    const expected = Number(info.actualWords || info.wordsCount || 0);
    const unbought = Number(info.vipStatus) === 1 && !Number(info.isBuy) && !Number(info.limitFree);
    if (unbought || (expected >= 500 && contentText.length < expected * 0.5)) {
      return { error: 'locked', reason: Number(data && data.isLogin) ? 'purchase' : 'login', chars: contentText.length, expected };
    }
    if (!contentText || contentText.length < 20) {
      if (needsVerification) return { error: 'verification' };
      return { error: lockedHint ? 'locked' : 'empty content' };
    }
    return { lines: paras };
  };

  // ---------------------------------------------------------------------------------------------
  // qdn: reader extraction
  //
  // Bought chapters are drawn with cipher fonts: each code point shows the outline of a different
  // character, some outlines are stored mirrored, and the characters sit in random tags ordered by CSS
  // `order`, `::before/::after{content:attr()}` and hidden decoys. So this collects what is *painted*,
  // in visual order, plus the cipher font files, and leaves the character recognition to
  // qdn.decoder. The reader also keeps poisoned decoy paragraphs away from the viewport (antonym and
  // pronoun swaps) and swaps the real text in once a paragraph comes close, so every paragraph is
  // scrolled into view and read only after its text settles.
  //
  // options: {known: [sha256 hex...]}  fonts the decoder already holds are not sent again.
  // Result: {faces, fontData, fontDigests, paragraphs, dropped, duplicates, unstableParagraphs, skipped,
  //          scriptedCount, url, title}   or {error: 'no reader'}
  // ---------------------------------------------------------------------------------------------

  qdn.extract = async function (options) {
    const known = new Set((options && options.known) || []);
    const main = document.querySelector('main.r-font-encrypt, main[id^="c-"]') || document.querySelector('main');
    if (!main) return { error: 'no reader' };
    const unquote = (value) => {
      if (!value || value === 'none' || value === 'normal') return '';
      const m = value.match(/^"(.*)"$/s) || value.match(/^'(.*)'$/s);
      if (!m) return '';
      return m[1].replace(/\\([0-9a-fA-F]{1,6})\s?/g,
        (_, hex) => String.fromCodePoint(parseInt(hex, 16))).replace(/\\(.)/g, '$1');
    };
    const transparent = (color) => /rgba\([^)]*,\s*0(?:\.0+)?\)$/.test(color) || color === 'transparent';
    const hiddenCache = new Map();
    // Zero opacity or display:none anywhere above hides a node. Visibility is inherited but a child may
    // set it back to visible, so only the node's own computed value counts.
    const concealed = (el) => {
      if (!el || el === main) return false;
      if (hiddenCache.has(el)) return hiddenCache.get(el);
      const st = getComputedStyle(el);
      const value = st.display === 'none' || parseFloat(st.opacity) === 0 || concealed(el.parentElement);
      hiddenCache.set(el, value);
      return value;
    };
    const hidden = (el) => concealed(el) || getComputedStyle(el).visibility === 'hidden';
    const mirrored = (el, extra = 'none') => {
      let combined = new DOMMatrix(extra === 'none' ? undefined : extra);
      for (let node = el; node; node = node.parentElement) {
        const transform = getComputedStyle(node).transform;
        if (transform !== 'none') combined = new DOMMatrix(transform).multiply(combined);
      }
      return combined.a < 0;
    };
    const faces = [];
    const readSheet = (sheet) => {
      let rules;
      try { rules = sheet.cssRules; } catch (e) { return; }
      for (const rule of rules) {
        if (rule.type === CSSRule.FONT_FACE_RULE) {
          const src = rule.style.getPropertyValue('src');
          faces.push({
            family: rule.style.getPropertyValue('font-family').replace(/["']/g, '').trim(),
            range: rule.style.getPropertyValue('unicode-range'),
            urls: [...src.matchAll(/url\("?([^")]+)"?\)/g)].map((m) => m[1]),
          });
        }
      }
    };
    [...document.styleSheets].forEach(readSheet);
    (document.adoptedStyleSheets || []).forEach(readSheet);
    const usedFamilies = new Set();
    let dropped = 0;
    let duplicates = 0;
    // Generated text is measured with real Range boxes. Computed advances alone cannot locate wrapped,
    // positioned, flex-ordered or transformed pseudo-elements.
    let measurementId = 0;
    const painted = (rect, el) => {
      let left = rect.left, right = rect.right, top = rect.top, bottom = rect.bottom;
      for (let node = el; node; node = node.parentElement) {
        const st = getComputedStyle(node);
        const box = node.getBoundingClientRect();
        if (['hidden', 'clip', 'scroll', 'auto'].includes(st.overflowX)) {
          left = Math.max(left, box.left); right = Math.min(right, box.right);
        }
        if (['hidden', 'clip', 'scroll', 'auto'].includes(st.overflowY)) {
          top = Math.max(top, box.top); bottom = Math.min(bottom, box.bottom);
        }
        // Legacy clipping is still used by inline decoy containers.
        const clip = st.clip.match(/^rect\((.*)\)$/);
        if (clip && ['absolute', 'fixed'].includes(st.position)) {
          const values = clip[1].split(/[,\s]+/).filter(Boolean);
          if (values.length === 4 && values.every((v) => v !== 'auto')) {
            const [t, r, b, l] = values.map(parseFloat);
            if (r <= l || b <= t) return false;
          }
        }
        if (right - left <= 0.01 || bottom - top <= 0.01) return false;
      }
      return true;
    };
    const capture = (p) => {
      hiddenCache.clear();
      const atoms = [];
      const addPseudo = (el, which) => {
        const st = getComputedStyle(el, which);
        const content = unquote(st.content);
        if (!content || st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0
            || parseFloat(st.fontSize) < 1 || transparent(st.color)) return;
        // Snapshot before suppressing the original pseudo: computed styles are live objects. Everything
        // is restored synchronously before the reader's mutation observers or the next scroll can run.
        const properties = [...st].map((name) => [name, st.getPropertyValue(name)]);
        const fonts = st.fontFamily;
        const flip = mirrored(el, st.transform);
        const attribute = 'data-qdn-capture';
        const previous = el.getAttribute(attribute);
        const id = String(++measurementId);
        const sheet = document.createElement('style');
        sheet.textContent = `[${attribute}="${id}"]${which}{content:none!important;}`;
        const probe = document.createElement('qdn-generated-text');
        for (const [name, value] of properties) {
          if (!['content', 'background-image', 'animation', 'transition'].includes(name)) {
            probe.style.setProperty(name, value, 'important');
          }
        }
        probe.textContent = content;
        try {
          el.setAttribute(attribute, id);
          document.head.appendChild(sheet);
          if (which === '::before') el.prepend(probe); else el.appendChild(probe);
          const range = document.createRange();
          let offset = 0;
          for (const ch of content) {
            range.setStart(probe.firstChild, offset);
            range.setEnd(probe.firstChild, offset + ch.length);
            offset += ch.length;
            const rect = range.getBoundingClientRect();
            if (rect.width <= 0.01 || rect.height <= 0.01 || !painted(rect, probe)) {
              dropped += 1; continue;
            }
            fonts.split(',').forEach((f) => usedFamilies.add(f.replace(/["']/g, '').trim()));
            atoms.push({ x: rect.left + rect.width / 2, y: rect.top, h: rect.height, w: rect.width,
              text: ch, fonts, src: which.slice(2), mirrored: flip });
          }
        } finally {
          probe.remove(); sheet.remove();
          if (previous === null) el.removeAttribute(attribute); else el.setAttribute(attribute, previous);
        }
      };
      const walk = (node) => {
        if (node.nodeType === 3) {
          const el = node.parentElement;
          const st = getComputedStyle(el);
          if (hidden(el) || transparent(st.color) || parseFloat(st.fontSize) < 1) {
            dropped += node.textContent.length;
            return;
          }
          st.fontFamily.split(',').forEach((f) => usedFamilies.add(f.replace(/["']/g, '').trim()));
          const flip = mirrored(el);
          const range = document.createRange();
          let offset = 0;
          for (const ch of node.textContent) {
            range.setStart(node, offset);
            range.setEnd(node, offset + ch.length);
            offset += ch.length;
            const r = range.getBoundingClientRect();
            // Decoys have no box at all; zoomed-out glyphs are only a few px.
            if (r.width <= 0.01 || r.height <= 0.01 || !painted(r, el)) { dropped += 1; continue; }
            atoms.push({ x: r.left + r.width / 2, y: r.top, h: r.height, w: r.width, text: ch,
              fonts: st.fontFamily, src: 'text', mirrored: flip });
          }
          return;
        }
        if (node.nodeType !== 1) return;
        const el = node;
        if (el.matches('.review, .review *, script, style')) return;
        if (concealed(el)) {
          dropped += (el.textContent || '').length;
          return;
        }
        addPseudo(el, '::before');
        for (const child of el.childNodes) walk(child);
        addPseudo(el, '::after');
      };
      walk(p);
      // Group into visual lines, then read each line left to right.
      atoms.sort((a, b) => a.y - b.y);
      const lines = [];
      for (const atom of atoms) {
        // Fallback punctuation and cipher faces can have different ascents. Comparing just their tops
        // split one painted line into two, moving names behind its punctuation. Match overlapping boxes.
        const line = lines.find((candidate) => candidate.atoms.some((other) => {
          const overlap = Math.min(atom.y + atom.h, other.y + other.h) - Math.max(atom.y, other.y);
          return overlap > Math.min(atom.h, other.h) * 0.5;
        }));
        if (line) line.atoms.push(atom); else lines.push({ y: atom.y, atoms: [atom] });
      }
      lines.sort((a, b) => a.y - b.y);
      const ordered = [];
      for (const line of lines) {
        line.atoms.sort((a, b) => a.x - b.x);
        const kept = [];
        for (const atom of line.atoms) {
          if (kept.some((other) => other.text === atom.text && other.fonts === atom.fonts
              && other.mirrored === atom.mirrored && other.src !== atom.src
              && Math.abs(other.x - atom.x) <= Math.max(0.05, Math.min(other.w, atom.w) * 0.05)
              && Math.abs(other.y - atom.y) <= Math.max(0.05, atom.h * 0.1))) {
            duplicates += 1;
            continue;
          }
          kept.push(atom);
          ordered.push({ parts: [{ text: atom.text, fonts: atom.fonts }], mirrored: atom.mirrored,
            x: atom.x, y: atom.y, h: atom.h, w: atom.w, src: atom.src });
        }
      }
      return ordered;
    };
    // The reader keeps a decoy copy of every paragraph away from the viewport and swaps in the real
    // text (in about 25 ms) once the paragraph comes within ~200 px of it. Step through the chapter a
    // screen at a time and read each paragraph once it is fully on screen and the swaps settled.
    await Promise.race([document.fonts.ready, sleep(2000)]);
    const all = [...main.querySelectorAll('p')];
    const paragraphs = new Array(all.length).fill(null);
    const skipped = new Set();
    const view = window.innerHeight;
    const inView = (p) => {
      const r = p.getBoundingClientRect();
      return r.height > 0.01 && r.top >= 0 && r.bottom <= view;
    };
    all.forEach((p, i) => {
      // A paragraph may be visibility:hidden while children set it back to visible, so only
      // display/opacity rule a paragraph out.
      if (concealed(p) || p.getBoundingClientRect().height <= 0.01) skipped.add(i);
    });
    const onScreenText = () => all.filter((p) => {
      const box = p.getBoundingClientRect();
      return box.bottom > 0 && box.top < view;
    }).map((p) => [p.innerHTML,
      ...[p, ...p.querySelectorAll('*')].map((el) => {
        const box = el.getBoundingClientRect();
        return [box.x, box.y, box.width, box.height,
          ...[null, '::before', '::after'].map((which) => {
            const st = getComputedStyle(el, which);
            return [st.content, st.fontFamily, st.fontSize, st.transform, st.display, st.visibility,
              st.opacity, st.color, st.clip, st.overflowX, st.overflowY].join('|');
          })].join('|');
      })].join('|')).join('\u0000') + document.fonts.status;
    const unstable = new Set();
    const settle = async () => {
      // Initially stable text may still be a decoy. Allow a full second for scroll/attribute swaps,
      // then require a quiet rendered signature.
      const started = Date.now();
      let last = onScreenText();
      let lastChange = started;
      while (Date.now() - started < 2500) {
        await sleep(25);
        const now = onScreenText();
        if (now !== last) lastChange = Date.now();
        last = now;
        if (Date.now() - lastChange >= 150 && Date.now() - started >= 1000
            && document.fonts.status === 'loaded') return true;
      }
      return false;
    };
    const remaining = () => all.some((p, i) => paragraphs[i] === null && !skipped.has(i));
    const readVisible = async () => {
      const stable = await settle();
      all.forEach((p, i) => {
        if (paragraphs[i] === null && !skipped.has(i) && inView(p)) {
          paragraphs[i] = capture(p);
          if (!stable) unstable.add(i);
        }
      });
    };
    // Fast path: zoom out until the whole chapter fits on one screen. The reader picks which paragraphs
    // to reveal from layout positions when the page scrolls, so a 1px scroll after zooming reveals every
    // paragraph at once. (A resized or emulated viewport alone does not trigger it.)
    window.scrollTo(0, 0);
    const fit = (view - 16) / Math.max(1, main.getBoundingClientRect().bottom);
    if (fit < 1 && fit >= 0.08) {
      const rootEl = document.documentElement;
      const previousZoom = rootEl.style.zoom;
      rootEl.style.zoom = String(fit);
      try {
        await sleep(25);
        window.scrollTo(0, 1);
        window.scrollTo(0, 0);
        await readVisible();
      } finally {
        rootEl.style.zoom = previousZoom;
        window.scrollTo(0, 0);
      }
    }
    // Otherwise (or for anything the zoom missed) step a screen at a time.
    const tallest = Math.max(0, ...all.map((p) => p.getBoundingClientRect().height));
    const step = Math.max(120, view - tallest - 16);
    const top = main.getBoundingClientRect().top + window.scrollY - 8;
    // Stop at the end of the text: reaching the page end opens the next chapter.
    const bottom = Math.max(top, main.getBoundingClientRect().bottom + window.scrollY - view + 8);
    for (let y = top; remaining(); y = Math.min(bottom, y + step)) {
      window.scrollTo(0, y);
      await readVisible();
      if (y >= bottom) break;
    }
    // Only a paragraph taller than the screen is left; read it from its top.
    for (let i = 0; i < all.length; i += 1) {
      if (paragraphs[i] !== null || skipped.has(i)) continue;
      all[i].scrollIntoView({ block: 'start' });
      const stable = await settle();
      paragraphs[i] = capture(all[i]);
      if (!stable) unstable.add(i);
    }
    window.scrollTo(0, 0);

    // Faces built in script come after the stylesheet ones, so the browser tries them first. Keep
    // definition order; the decoder walks it backwards.
    const fontData = {};
    const fontDigests = {};
    const scripted = (window.__npiaFontFaces || []).filter((rec) => document.fonts.has(rec.face));
    scripted.forEach((rec, i) => {
      const face = {
        family: String(rec.family).replace(/["']/g, '').trim(),
        range: rec.descriptors.unicodeRange || 'U+0-10FFFF',
        urls: [], scripted: true,
      };
      if (typeof rec.source === 'string') {
        face.urls = [...rec.source.matchAll(/url\("?([^")]+)"?\)/g)].map((m) => m[1]);
      } else {
        face.url = 'scripted:' + i;
        face.bytes = rec.source instanceof ArrayBuffer ? new Uint8Array(rec.source)
          : (ArrayBuffer.isView(rec.source) ? new Uint8Array(rec.source.buffer, rec.source.byteOffset, rec.source.byteLength) : null);
      }
      faces.push(face);
    });
    const usedFaces = faces.filter((face) => usedFamilies.has(face.family));
    for (const face of usedFaces) {
      const url = face.url || face.urls.find((u) => /woff2|ttf|otf|blob:/.test(u)) || face.urls[0];
      face.url = url;
      if (!url || fontData[url] || fontDigests[url]) continue;
      try {
        const bytes = face.bytes || new Uint8Array(await (await fetch(url)).arrayBuffer());
        const digest = await sha256Hex(bytes);
        fontDigests[url] = digest;
        if (!known.has(digest)) fontData[url] = bytesToBase64(bytes);
      } catch (e) {
        fontData[url] = 'ERR ' + (e && e.message ? e.message : e);
      }
    }
    for (const face of usedFaces) delete face.bytes;
    return {
      scriptedCount: scripted.length, skipped: skipped.size, faces: usedFaces, fontData, fontDigests,
      paragraphs: paragraphs.filter((p) => p !== null), dropped, duplicates,
      unstableParagraphs: [...unstable], url: location.href, title: document.title,
    };
  };

  // ---------------------------------------------------------------------------------------------
  // qdn: glyph decoder
  //
  // Turns an extraction payload into text. Each cipher code point is drawn with another character's
  // outline (some mirrored, all slightly jittered), so a hash cannot match them. The glyph is rendered
  // on a canvas with the cipher font, reduced to a normalised 56x56 vector, and compared with the same
  // vector of every hanzi in a bundled reference font (Noto Sans CJK SC, the design the cipher glyphs
  // are drawn from). A character-frequency prior settles near-identical shapes (好/奸), and entries
  // verified against screenshots for these exact font bytes bypass matching. Before shape matching,
  // a glyph's own outline (read from the font file, WOFF2 included) is looked up in a table of labelled
  // outlines; character codes are scrambled per font, but outlines are stable, so a hit is exact.
  //
  // Matching is two-stage so it stays quick on a phone: a coarse 14x14 vector narrows ~9800 reference
  // characters to the best 48, and the full 56x56 comparison (the one the desktop uses) picks among those.
  //
  // The decoder is independent of the page it runs in. Native code usually runs it in a dedicated blank
  // page that outlives the chapter pages, so the reference (a few seconds to build) is made once.
  //
  // Resources, pushed with putResource (base64 chunks, any order):
  //   font       NotoSansCJKsc-Regular-hanzi.otf
  //   frequency  zh_char_frequency.txt            optional  "<char>\t<count>" per line
  //   verified   qdn_verified_maps.json           optional
  //   outlines   qdn_outline_table.json           optional
  //   brotli     brotli_dictionary.bin            optional  the static dictionary WOFF2 fonts may use
  // ---------------------------------------------------------------------------------------------

  qdn.decoder = (function () {
    const RENDER_SIZE = 96;
    const GRID = 56;
    const BLUR = 0.5;
    const COARSE_SIZE = 48;
    const COARSE_GRID = 14;
    const COARSE_KEEP = 48;
    const CANDIDATES = 5;
    const PRIOR_WEIGHT = 0.1;
    const PRIOR_WINDOW = 0.01;
    const CONFIDENCE_SCORE = 0.90;
    const CONFIDENCE_MARGIN = 0.03;
    // Component refinement (below). The desktop compares balanced scores strictly; a canvas rasterises
    // glyphs a little differently from the desktop's FreeType, which moves scores by about 0.01 and
    // can flip a near tie, so the comparison allows that much slack.
    const REFINE_LEFT = 0.95;
    const REFINE_SLACK = 0.02;
    const REF_FAMILY = '__qdn_ref';
    // The font hook (qdn.installFontHook) records every FontFace built after it; the decoder's own
    // faces must not be copied into that list.
    const nativeFontFace = () => root.FontFace.__npiaNative || root.FontFace;

    const state = {
      parts: {},            // resource name -> {count, chunks: Map(index -> bytes)}
      files: {},            // resource name -> Uint8Array once complete
      refFace: null,
      ref: null,            // finished reference
      build: null,          // reference being built
      cores: new Map(),     // font sha256 -> core {digest, css, face, cover: Map}
      glyphs: new Map(),    // `${digest}:${cp}:${mirrored}` -> match
      fine: new Map(),      // reference char -> Float32Array(GRID*GRID)
      verified: null,       // digest -> {entries}
      frequency: null,      // char -> count
      outlineTable: null,   // promise of the OutlineTable
      outlineSize: 0,       // exemplars in it once built
      outlineFonts: new Map(),  // font sha256 -> outlines (a few recent fonts) or null when unreadable
    };

    // --- resources -------------------------------------------------------------------------------

    const resourceNames = ['font', 'frequency', 'verified', 'outlines', 'brotli'];

    /** Stores chunk `index` (0-based) of `count` for a resource. Returns whether it is now complete. */
    function putResource(name, index, count, base64) {
      if (!resourceNames.includes(name)) throw new Error(`unknown resource ${name}`);
      // Chunks are decoded one by one, so every chunk but the last must end on a base64 group boundary.
      if (index < count - 1 && String(base64).length % 4 !== 0) throw new Error('a resource chunk must be a multiple of 4 characters');
      const entry = state.parts[name] && state.parts[name].count === count ? state.parts[name]
        : (state.parts[name] = { count, chunks: new Map() });
      entry.chunks.set(index, base64ToBytes(base64));
      if (entry.chunks.size < count) return { name, complete: false, received: entry.chunks.size, count };
      let size = 0;
      for (let i = 0; i < count; i += 1) size += entry.chunks.get(i).length;
      const joined = new Uint8Array(size);
      let offset = 0;
      for (let i = 0; i < count; i += 1) {
        const chunk = entry.chunks.get(i);
        joined.set(chunk, offset);
        offset += chunk.length;
      }
      state.files[name] = joined;
      delete state.parts[name];
      // New data replaces anything derived from the old.
      if (name === 'font') { state.ref = null; state.build = null; state.refFace = null; state.fine.clear(); state.glyphs.clear(); }
      if (name === 'frequency') { state.frequency = null; state.ref = null; state.build = null; }
      if (name === 'verified') state.verified = null;
      if (name === 'outlines') { state.outlineTable = null; state.outlineSize = 0; }
      if (name === 'brotli') state.outlineFonts.clear();
      return { name, complete: true, received: count, count };
    }

    function resourceStatus() {
      const out = {};
      for (const name of resourceNames) out[name] = !!state.files[name];
      return out;
    }

    function verifiedEntries(digest) {
      if (state.verified === null) {
        state.verified = {};
        try {
          const raw = state.files.verified;
          const saved = raw ? JSON.parse(new TextDecoder('utf-8').decode(raw)) : null;
          if (saved && saved.schema === 1) {
            for (const [fontDigest, font] of Object.entries(saved.fonts || {})) {
              if (!/^[0-9a-f]{64}$/.test(fontDigest)) continue;
              const entries = {};
              for (const [key, value] of Object.entries((font && font.entries) || {})) {
                if (/^[0-9A-F]{1,6}:[01]$/.test(key) && typeof value === 'string' && [...value].length === 1) entries[key] = value;
              }
              state.verified[fontDigest] = entries;
            }
          }
        } catch (e) {
          // A missing or corrupt optional map must not stop decoding.
          state.verified = {};
        }
      }
      return state.verified[digest] || {};
    }

    function frequency() {
      if (state.frequency === null) {
        state.frequency = new Map();
        const raw = state.files.frequency;
        if (raw) {
          for (const line of new TextDecoder('utf-8').decode(raw).split(/\r?\n/)) {
            const tab = line.indexOf('\t');
            if (tab <= 0) continue;
            const count = parseInt(line.slice(tab + 1), 10);
            if (Number.isFinite(count)) state.frequency.set(line.slice(0, tab), count);
          }
        }
      }
      return state.frequency;
    }

    // --- sfnt cmap (the bundled reference font is a plain OpenType file) ---------------------------

    function parseCmap(bytes) {
      const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
      const tables = view.getUint16(4, false);
      let cmapOffset = -1;
      for (let i = 0; i < tables; i += 1) {
        const base = 12 + i * 16;
        const tag = String.fromCharCode(bytes[base], bytes[base + 1], bytes[base + 2], bytes[base + 3]);
        if (tag === 'cmap') { cmapOffset = view.getUint32(base + 8, false); break; }
      }
      if (cmapOffset < 0) throw new Error('reference font has no cmap');
      const count = view.getUint16(cmapOffset + 2, false);
      let best = null;
      for (let i = 0; i < count; i += 1) {
        const base = cmapOffset + 4 + i * 8;
        const platform = view.getUint16(base, false);
        const encoding = view.getUint16(base + 2, false);
        const offset = cmapOffset + view.getUint32(base + 4, false);
        const format = view.getUint16(offset, false);
        const rank = (format === 12 && (platform === 3 && encoding === 10 || platform === 0)) ? 3
          : (format === 4 && (platform === 3 && encoding === 1 || platform === 0)) ? 2 : 0;
        if (rank && (!best || rank > best.rank)) best = { rank, offset, format };
      }
      if (!best) throw new Error('reference font has no usable cmap');
      const codes = [];
      if (best.format === 12) {
        const groups = view.getUint32(best.offset + 12, false);
        for (let i = 0; i < groups; i += 1) {
          const base = best.offset + 16 + i * 12;
          const start = view.getUint32(base, false);
          const end = view.getUint32(base + 4, false);
          for (let cp = start; cp <= end; cp += 1) codes.push(cp);
        }
      } else {
        const segCount = view.getUint16(best.offset + 6, false) / 2;
        const ends = best.offset + 14;
        const starts = ends + segCount * 2 + 2;
        const deltas = starts + segCount * 2;
        const rangeOffsets = deltas + segCount * 2;
        for (let i = 0; i < segCount; i += 1) {
          const end = view.getUint16(ends + i * 2, false);
          const start = view.getUint16(starts + i * 2, false);
          const idRangeOffset = view.getUint16(rangeOffsets + i * 2, false);
          const idDelta = view.getInt16(deltas + i * 2, false);
          for (let cp = start; cp <= end && cp !== 0xFFFF; cp += 1) {
            let glyph;
            if (idRangeOffset === 0) glyph = (cp + idDelta) & 0xFFFF;
            else {
              const at = rangeOffsets + i * 2 + idRangeOffset + (cp - start) * 2;
              glyph = at + 2 <= bytes.length ? view.getUint16(at, false) : 0;
              if (glyph) glyph = (glyph + idDelta) & 0xFFFF;
            }
            if (glyph) codes.push(cp);
          }
        }
      }
      return codes;
    }

    const isCjk = (cp) => (cp >= 0x3400 && cp <= 0x9FFF) || (cp >= 0xF900 && cp <= 0xFAFF)
      || (cp >= 0x20000 && cp <= 0x323AF);

    // --- glyph rendering and vectors -------------------------------------------------------------

    const canvases = new Map();
    function canvasFor(size) {
      let entry = canvases.get(size);
      if (!entry) {
        const canvas = document.createElement('canvas');
        canvas.width = size * 2;
        canvas.height = size * 2;
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        ctx.fillStyle = '#fff';
        ctx.textBaseline = 'alphabetic';
        entry = { canvas, ctx, side: size * 2 };
        canvases.set(size, entry);
      }
      return entry;
    }

    /** Renders one character; returns the cropped alpha mask {w, h, data} or null when nothing is drawn. */
    function renderGlyph(css, ch, size) {
      const { ctx, side } = canvasFor(size);
      ctx.clearRect(0, 0, side, side);
      ctx.font = `${size}px ${css}`;
      ctx.fillText(ch, size * 0.5, size * 1.3);
      const pixels = ctx.getImageData(0, 0, side, side).data;
      let minX = side, minY = side, maxX = -1, maxY = -1;
      for (let y = 0; y < side; y += 1) {
        const row = y * side * 4 + 3;
        for (let x = 0; x < side; x += 1) {
          if (pixels[row + x * 4] > 0) {
            if (x < minX) minX = x;
            if (x > maxX) maxX = x;
            if (y < minY) minY = y;
            if (y > maxY) maxY = y;
          }
        }
      }
      if (maxX < 0) return null;
      const w = maxX - minX + 1;
      const h = maxY - minY + 1;
      const data = new Uint8Array(w * h);
      for (let y = 0; y < h; y += 1) {
        const row = (minY + y) * side * 4 + 3;
        for (let x = 0; x < w; x += 1) data[y * w + x] = pixels[row + (minX + x) * 4];
      }
      return { w, h, data };
    }

    /** The two renders differ: the character comes from the font, not from a fallback font. */
    function sameInk(a, b) {
      if (!a || !b) return a === b;
      if (a.w !== b.w || a.h !== b.h) return false;
      for (let i = 0; i < a.data.length; i += 1) if (a.data[i] !== b.data[i]) return false;
      return true;
    }

    const axisCache = new Map();
    function axisWeights(inSize, outSize) {
      const key = inSize * 4096 + outSize;
      let weights = axisCache.get(key);
      if (weights) return weights;
      const lists = [];
      if (inSize >= outSize) {
        const scale = inSize / outSize;
        for (let i = 0; i < outSize; i += 1) {
          const from = i * scale;
          const to = (i + 1) * scale;
          const list = [];
          for (let j = Math.floor(from); j < Math.ceil(to) && j < inSize; j += 1) {
            const overlap = Math.min(j + 1, to) - Math.max(j, from);
            if (overlap > 0) list.push([j, overlap / scale]);
          }
          lists.push(list);
        }
      } else {
        const scale = inSize / outSize;
        for (let i = 0; i < outSize; i += 1) {
          const at = (i + 0.5) * scale - 0.5;
          const j0 = Math.floor(at);
          const frac = at - j0;
          const list = [];
          const clamp = (j) => Math.min(inSize - 1, Math.max(0, j));
          list.push([clamp(j0), 1 - frac]);
          if (frac > 0) list.push([clamp(j0 + 1), frac]);
          lists.push(list);
        }
      }
      weights = lists;
      axisCache.set(key, weights);
      return weights;
    }

    function resample(square, side, grid) {
      if (side === grid) return Float32Array.from(square);
      const weights = axisWeights(side, grid);
      const across = new Float32Array(side * grid);
      for (let y = 0; y < side; y += 1) {
        for (let x = 0; x < grid; x += 1) {
          let sum = 0;
          for (const [j, weight] of weights[x]) sum += square[y * side + j] * weight;
          across[y * grid + x] = sum;
        }
      }
      const out = new Float32Array(grid * grid);
      for (let y = 0; y < grid; y += 1) {
        for (let x = 0; x < grid; x += 1) {
          let sum = 0;
          for (const [j, weight] of weights[y]) sum += across[j * grid + x] * weight;
          out[y * grid + x] = sum;
        }
      }
      return out;
    }

    function blurGrid(values, grid, sigma) {
      const edge = Math.exp(-1 / (2 * sigma * sigma));
      const total = 1 + 2 * edge;
      const a = edge / total;
      const b = 1 / total;
      const tmp = new Float32Array(values.length);
      for (let y = 0; y < grid; y += 1) {
        for (let x = 0; x < grid; x += 1) {
          const left = values[y * grid + Math.max(0, x - 1)];
          const right = values[y * grid + Math.min(grid - 1, x + 1)];
          tmp[y * grid + x] = a * left + b * values[y * grid + x] + a * right;
        }
      }
      for (let y = 0; y < grid; y += 1) {
        for (let x = 0; x < grid; x += 1) {
          const up = tmp[Math.max(0, y - 1) * grid + x];
          const down = tmp[Math.min(grid - 1, y + 1) * grid + x];
          values[y * grid + x] = a * up + b * tmp[y * grid + x] + a * down;
        }
      }
      return values;
    }

    /** Square-pad, resample, blur, optionally mirror, then mean-centre and unit-normalise. Null if flat. */
    function glyphVector(crop, grid, blur, mirror) {
      if (!crop) return null;
      const { w, h, data } = crop;
      const side = Math.max(w, h);
      const square = new Float32Array(side * side);
      const offsetX = (side - w) >> 1;
      const offsetY = (side - h) >> 1;
      for (let y = 0; y < h; y += 1) {
        for (let x = 0; x < w; x += 1) square[(y + offsetY) * side + x + offsetX] = data[y * w + x];
      }
      const values = resample(square, side, grid);
      if (blur) blurGrid(values, grid, blur);
      if (mirror) {
        for (let y = 0; y < grid; y += 1) {
          for (let x = 0; x < grid >> 1; x += 1) {
            const i = y * grid + x;
            const j = y * grid + (grid - 1 - x);
            const swap = values[i]; values[i] = values[j]; values[j] = swap;
          }
        }
      }
      let mean = 0;
      for (let i = 0; i < values.length; i += 1) mean += values[i];
      mean /= values.length;
      let norm = 0;
      for (let i = 0; i < values.length; i += 1) {
        values[i] -= mean;
        norm += values[i] * values[i];
      }
      norm = Math.sqrt(norm);
      if (!norm) return null;
      for (let i = 0; i < values.length; i += 1) values[i] /= norm;
      return values;
    }

    // --- the reference ---------------------------------------------------------------------------

    async function loadReferenceFace() {
      if (state.refFace) return;
      const bytes = state.files.font;
      if (!bytes) throw new Error('the reference font (font resource) has not been pushed');
      const face = new (nativeFontFace())(REF_FAMILY, bytes.slice().buffer);
      await face.load();
      document.fonts.add(face);
      state.refFace = face;
    }

    /**
     * Builds the reference, at most `budgetMs` per call so one script call stays short. Returns
     * {done, built, total}; call again until done.
     */
    async function prepare(budgetMs) {
      if (state.ref) return { done: true, built: state.ref.chars.length, total: state.ref.chars.length, ms: 0 };
      const started = Date.now();
      await loadReferenceFace();
      if (!state.build) {
        const freq = frequency();
        const supported = new Set(parseCmap(state.files.font).filter(isCjk));
        const chars = [];
        for (const cp of [...supported].sort((x, y) => x - y)) {
          const ch = String.fromCodePoint(cp);
          const canonical = ch.normalize('NFKC');
          // Compatibility ideographs duplicate their unified form; do not let both compete.
          if ([...canonical].length === 1 && canonical !== ch && supported.has(canonical.codePointAt(0))) continue;
          chars.push(ch);
        }
        const dim = COARSE_GRID * COARSE_GRID;
        let top = 1;
        for (const count of freq.values()) if (count > top) top = count;
        state.build = {
          chars: [], coarse: new Float32Array(chars.length * dim), prior: new Float32Array(chars.length),
          pending: chars, next: 0, top, started,
        };
      }
      const build = state.build;
      const dim = COARSE_GRID * COARSE_GRID;
      const css = `"${REF_FAMILY}"`;
      const freq = frequency();
      const logTop = Math.log10(build.top + 1);
      const limit = budgetMs > 0 ? budgetMs : Infinity;
      while (build.next < build.pending.length) {
        const ch = build.pending[build.next];
        build.next += 1;
        const vector = glyphVector(renderGlyph(css, ch, COARSE_SIZE), COARSE_GRID, 0, false);
        if (!vector) continue;
        const slot = build.chars.length;
        build.coarse.set(vector, slot * dim);
        build.prior[slot] = Math.log10((freq.get(ch) || 0) + 1) / logTop;
        build.chars.push(ch);
        if ((build.next & 63) === 0 && Date.now() - started >= limit) {
          return { done: false, built: build.next, total: build.pending.length };
        }
      }
      const index = new Map();
      build.chars.forEach((ch, i) => index.set(ch, i));
      state.ref = { chars: build.chars, coarse: build.coarse.subarray(0, build.chars.length * dim),
        prior: build.prior.subarray(0, build.chars.length), index };
      state.build = null;
      return { done: true, built: build.chars.length, total: build.pending.length, ms: Date.now() - build.started };
    }

    function fineReference(ch) {
      let vector = state.fine.get(ch);
      if (vector === undefined) {
        vector = glyphVector(renderGlyph(`"${REF_FAMILY}"`, ch, RENDER_SIZE), GRID, BLUR, false);
        if (state.fine.size > 40000) state.fine.clear();
        state.fine.set(ch, vector);
      }
      return vector;
    }

    // --- cipher fonts ----------------------------------------------------------------------------

    function parseUnicodeRange(value) {
      const spans = [];
      for (const part of String(value || '').split(',')) {
        const match = part.match(/\s*U\+([0-9A-Fa-f?]+)(?:-([0-9A-Fa-f]+))?/);
        if (!match) continue;
        const low = match[1];
        if (low.includes('?')) {
          spans.push([parseInt(low.replace(/\?/g, '0'), 16), parseInt(low.replace(/\?/g, 'F'), 16)]);
        } else {
          const start = parseInt(low, 16);
          spans.push([start, parseInt(match[2] || low, 16)]);
        }
      }
      return spans.length ? spans : [[0, 0x10FFFF]];
    }

    async function coreFor(digest, bytes) {
      let core = state.cores.get(digest);
      if (!core) {
        const name = `__qdn_cf_${digest.slice(0, 16)}`;
        const face = new (nativeFontFace())(name, bytes.slice().buffer);
        await face.load();
        document.fonts.add(face);
        core = { digest, name, css: `"${name}"`, face, cover: new Map(), bytes };
        if (state.cores.size > 64) {
          for (const old of state.cores.values()) document.fonts.delete(old.face);
          state.cores.clear();
        }
        state.cores.set(digest, core);
      }
      return core;
    }

    // A character is the font's own (not a fallback) when drawing it with the font differs from
    // drawing it with a family that does not exist and with the generic families. WebKit draws a
    // character a loaded font lacks with its sans-serif fallback, but an unknown family with its serif
    // one, so comparing with the unknown family alone took every missing character for the font's own.
    // In Chromium the first comparison already decides, so its answers do not change.
    function coreCovers(core, ch) {
      let covered = core.cover.get(ch);
      if (covered === undefined) {
        const own = renderGlyph(core.css, ch, 32);
        covered = !['"__qdn_no_such_family"', 'sans-serif', 'serif', 'system-ui', 'monospace']
          .some((family) => sameInk(own, renderGlyph(family, ch, 32)));
        core.cover.set(ch, covered);
      }
      return covered;
    }

    class CipherFont {
      constructor(family, range, core) {
        this.family = family;
        this.spans = parseUnicodeRange(range);
        this.core = core;
      }

      covers(ch) {
        const cp = ch.codePointAt(0);
        if (!this.spans.some(([low, high]) => low <= cp && cp <= high)) return false;
        return coreCovers(this.core, ch);
      }
    }

    // --- brotli (RFC 7932), decode only: WOFF2 fonts are brotli-compressed ------------------------
    // Web views cannot decompress brotli for script, so this is a plain decoder. Prefix codes are
    // decoded canonically bit by bit, which is quick enough for a font of a few hundred kilobytes.
    // The static dictionary (resource `brotli`) is only needed by streams that refer to it.

    const BROTLI_ORDER = [1, 2, 3, 4, 0, 5, 17, 6, 16, 7, 8, 9, 10, 11, 12, 13, 14, 15];
    const BROTLI_CLC_LENGTH = [2, 2, 2, 3, 2, 2, 2, 4, 2, 2, 2, 3, 2, 2, 2, 4];
    const BROTLI_CLC_VALUE = [0, 4, 3, 2, 0, 4, 3, 1, 0, 4, 3, 2, 0, 4, 3, 5];
    const BROTLI_BLOCK_BASE = [1, 5, 9, 13, 17, 25, 33, 41, 49, 65, 81, 97, 113, 145, 177, 209, 241, 305, 369, 497,
      753, 1265, 2289, 4337, 8433, 16625];
    const BROTLI_BLOCK_EXTRA = [2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 6, 6, 7, 8, 9, 10, 11, 12, 13, 24];
    const BROTLI_INSERT_BASE = [0, 1, 2, 3, 4, 5, 6, 8, 10, 14, 18, 26, 34, 50, 66, 98, 130, 194, 322, 578, 1090, 2114,
      6210, 22594];
    const BROTLI_INSERT_EXTRA = [0, 0, 0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 7, 8, 9, 10, 12, 14, 24];
    const BROTLI_COPY_BASE = [2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 18, 22, 30, 38, 54, 70, 102, 134, 198, 326, 582, 1094,
      2118];
    const BROTLI_COPY_EXTRA = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 7, 8, 9, 10, 24];
    // Insert and copy length ranges of the 11 cells of 64 commands.
    const BROTLI_CELL_INSERT = [0, 0, 0, 0, 1, 1, 0, 2, 1, 2, 2];
    const BROTLI_CELL_COPY = [0, 1, 0, 1, 0, 1, 2, 0, 2, 1, 2];
    const BROTLI_SHORT_INDEX = [0, 1, 2, 3, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1];
    const BROTLI_SHORT_DELTA = [0, 0, 0, 0, -1, 1, -2, 2, -3, 3, -1, 1, -2, 2, -3, 3];
    const BROTLI_DICT_BITS = [0, 0, 0, 0, 10, 10, 11, 11, 10, 10, 10, 10, 10, 9, 9, 8, 7, 7, 8, 7, 7, 6, 6, 5, 5];
    const BROTLI_DICT_OFFSET = (() => {
      const offsets = new Array(25).fill(0);
      for (let n = 4; n < 24; n += 1) offsets[n + 1] = offsets[n] + n * (1 << BROTLI_DICT_BITS[n]);
      return offsets;
    })();
    const BROTLI_DICT_SIZE = 122784;
    // Context lookup tables (RFC 7932 section 7.1).
    const BROTLI_LUT = (() => {
      const utf0 = new Uint8Array(256), utf1 = new Uint8Array(256), signed = new Uint8Array(256);
      const ascii = '\0\0\0\0\0\0\0\0\0\u0004\u0004\0\0\u0004\0\0' + '\0'.repeat(16);
      const text0 = [8, 12, 16, 12, 12, 20, 12, 16, 24, 28, 12, 12, 32, 12, 36, 12,
        44, 44, 44, 44, 44, 44, 44, 44, 44, 44, 32, 32, 24, 40, 28, 12,
        12, 48, 52, 52, 52, 48, 52, 52, 52, 48, 52, 52, 52, 52, 52, 48,
        52, 52, 52, 52, 52, 48, 52, 52, 52, 52, 52, 24, 12, 28, 12, 12,
        12, 56, 60, 60, 60, 56, 60, 60, 60, 56, 60, 60, 60, 60, 60, 56,
        60, 60, 60, 60, 60, 56, 60, 60, 60, 60, 60, 24, 12, 28, 12, 0];
      const text1 = [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
        2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1,
        1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2,
        2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1,
        1, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3,
        3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 1, 1, 1, 1, 0];
      for (let i = 0; i < 32; i += 1) utf0[i] = ascii.charCodeAt(i);
      for (let i = 32; i < 128; i += 1) { utf0[i] = text0[i - 32]; utf1[i] = text1[i - 32]; }
      for (let i = 128; i < 256; i += 1) { utf0[i] = (i < 192 ? 0 : 2) + (i & 1); utf1[i] = i < 224 ? 0 : 2; }
      for (let i = 0; i < 256; i += 1) {
        signed[i] = i === 0 ? 0 : i < 16 ? 1 : i < 64 ? 2 : i < 128 ? 3 : i < 192 ? 4 : i < 240 ? 5 : i < 255 ? 6 : 7;
      }
      return { utf0, utf1, signed };
    })();
    // Word transforms (RFC 7932 appendix B): [prefix, kind, suffix]. Kinds: 0 identity, 1-9 omit the
    // last n bytes, 10 uppercase the first letter, 11 uppercase all, 12-20 omit the first n-11 bytes.
    const BROTLI_TRANSFORMS = (() => {
      const table = [
        '', 0, '', '', 0, ' ', ' ', 0, ' ', '', 12, '', '', 10, ' ', '', 0, ' the ', ' ', 0, '', 's ', 0, ' ',
        '', 0, ' of ', '', 10, '', '', 0, ' and ', '', 13, '', '', 1, '', ', ', 0, ' ', '', 0, ', ', ' ', 10, ' ',
        '', 0, ' in ', '', 0, ' to ', 'e ', 0, ' ', '', 0, '"', '', 0, '.', '', 0, '">', '', 0, '\n', '', 3, '',
        '', 0, ']', '', 0, ' for ', '', 14, '', '', 2, '', '', 0, ' a ', '', 0, ' that ', ' ', 10, '', '', 0, '. ',
        '.', 0, '', ' ', 0, ', ', '', 15, '', '', 0, ' with ', '', 0, '\'', '', 0, ' from ', '', 0, ' by ',
        '', 16, '', '', 17, '', ' the ', 0, '', '', 4, '', '', 0, '. The ', '', 11, '', '', 0, ' on ', '', 0, ' as ',
        '', 0, ' is ', '', 7, '', '', 1, 'ing ', '', 0, '\n\t', '', 0, ':', ' ', 0, '. ', '', 0, 'ed ', '', 20, '',
        '', 18, '', '', 6, '', '', 0, '(', '', 10, ', ', '', 8, '', '', 0, ' at ', '', 0, 'ly ', ' the ', 0, ' of ',
        '', 5, '', '', 9, '', ' ', 10, ', ', '', 10, '"', '.', 0, '(', '', 11, ' ', '', 10, '">', '', 0, '="',
        ' ', 0, '.', '.com/', 0, '', ' the ', 0, ' of the ', '', 10, '\'', '', 0, '. This ', '', 0, ',', '.', 0, ' ',
        '', 10, '(', '', 10, '.', '', 0, ' not ', ' ', 0, '="', '', 0, 'er ', ' ', 11, ' ', '', 0, 'al ', ' ', 11, '',
        '', 0, '=\'', '', 11, '"', '', 10, '. ', ' ', 0, '(', '', 0, 'ful ', ' ', 10, '. ', '', 0, 'ive ',
        '', 0, 'less ', '', 11, '\'', '', 0, 'est ', ' ', 10, '.', '', 11, '">', ' ', 0, '=\'', '', 10, ',',
        '', 0, 'ize ', '', 11, '.', '\u00c2\u00a0', 0, '', ' ', 0, ',', '', 10, '="', '', 11, '="', '', 0, 'ous ',
        '', 11, ', ', '', 10, '=\'', ' ', 10, ',', ' ', 11, '="', ' ', 11, ', ', '', 11, ',', '', 11, '(',
        '', 11, '. ', ' ', 11, '.', '', 11, '=\'', ' ', 11, '. ', ' ', 10, '="', ' ', 11, '=\'', ' ', 10, '=\''];
      const bytes = (text) => Uint8Array.from(text, (c) => c.charCodeAt(0));
      const out = [];
      for (let i = 0; i < table.length; i += 3) out.push([bytes(table[i]), table[i + 1], bytes(table[i + 2])]);
      return out;
    })();

    class BrotliBits {
      constructor(data) { this.data = data; this.pos = 0; this.acc = 0; this.count = 0; }
      bits(n) {
        while (this.count < n) {
          if (this.pos >= this.data.length) {
            if (this.pos > this.data.length + 4) throw new Error('brotli stream truncated');
            this.pos += 1;
          } else this.acc |= this.data[this.pos++] << this.count;
          this.count += 8;
        }
        const value = this.acc & ((1 << n) - 1);
        this.acc >>>= n;
        this.count -= n;
        return value;
      }
      peek4() {
        while (this.count < 4) { this.acc |= (this.pos < this.data.length ? this.data[this.pos] : 0) << this.count; this.pos += 1; this.count += 8; }
        return this.acc & 15;
      }
      align() { if (this.bits(this.count & 7) !== 0) throw new Error('brotli padding is not zero'); }
      byte() { return this.bits(8); }
    }

    /** A canonical prefix code from code lengths. `decode` reads one symbol. */
    class BrotliCode {
      constructor(lengths) {
        const count = new Int32Array(16);
        let used = 0, last = 0;
        for (let s = 0; s < lengths.length; s += 1) if (lengths[s]) { count[lengths[s]] += 1; used += 1; last = s; }
        this.single = used === 1 ? last : -1;
        this.first = new Int32Array(16);
        this.offset = new Int32Array(16);
        this.count = count;
        let code = 0, offset = 0;
        for (let len = 1; len < 16; len += 1) {
          code = (code + count[len - 1]) << 1;
          this.first[len] = code;
          this.offset[len] = offset;
          offset += count[len];
        }
        this.symbols = new Int32Array(offset);
        const next = this.offset.slice();
        for (let len = 1; len < 16; len += 1) {
          for (let s = 0; s < lengths.length; s += 1) if (lengths[s] === len) this.symbols[next[len]++] = s;
        }
      }
      decode(br) {
        if (this.single >= 0) return this.single;
        let code = 0;
        for (let len = 1; len < 16; len += 1) {
          code = (code << 1) | br.bits(1);
          const index = code - this.first[len];
          if (index < this.count[len]) return this.symbols[this.offset[len] + index];
        }
        throw new Error('brotli: invalid prefix code');
      }
    }

    function brotliReadCode(br, alphabet) {
      const lengths = new Uint8Array(alphabet);
      const skip = br.bits(2);
      if (skip === 1) {
        let width = 0;
        while ((1 << width) < alphabet) width += 1;
        const nsym = br.bits(2) + 1;
        const symbols = [];
        for (let i = 0; i < nsym; i += 1) {
          const symbol = br.bits(width);
          if (symbol >= alphabet) throw new Error('brotli: symbol out of range');
          symbols.push(symbol);
        }
        const shapes = nsym === 1 ? [[0]] : nsym === 2 ? [[1, 1]] : nsym === 3 ? [[1, 2, 2]] : [[2, 2, 2, 2], [1, 2, 3, 3]];
        const shape = shapes[nsym === 4 ? br.bits(1) : 0];
        if (nsym === 1) lengths[symbols[0]] = 1;
        else symbols.forEach((symbol, i) => { lengths[symbol] = shape[i]; });
        // A one-symbol code takes no bits.
        return new BrotliCode(lengths);
      }
      const clc = new Uint8Array(18);
      let space = 32, codes = 0;
      for (let i = skip; i < 18 && space > 0; i += 1) {
        const peek = br.peek4();
        br.bits(BROTLI_CLC_LENGTH[peek]);
        const length = BROTLI_CLC_VALUE[peek];
        clc[BROTLI_ORDER[i]] = length;
        if (length) { space -= 32 >> length; codes += 1; }
      }
      if (codes !== 1 && space !== 0) throw new Error('brotli: invalid code length code');
      const lengthCode = new BrotliCode(clc);
      let symbol = 0, previous = 8, repeat = 0, repeatLength = 0;
      space = 32768;
      while (symbol < alphabet && space > 0) {
        const p = lengthCode.decode(br);
        if (p < 16) {
          repeat = 0;
          lengths[symbol++] = p;
          if (p) { previous = p; space -= 32768 >> p; }
          continue;
        }
        const extra = p === 16 ? 2 : 3;
        const length = p === 16 ? previous : 0;
        if (repeatLength !== length) { repeat = 0; repeatLength = length; }
        const old = repeat;
        if (repeat > 0) repeat = (repeat - 2) << extra;
        repeat += br.bits(extra) + 3;
        const delta = repeat - old;
        if (symbol + delta > alphabet) throw new Error('brotli: code lengths overflow');
        for (let k = 0; k < delta; k += 1) lengths[symbol++] = length;
        if (length) space -= delta << (15 - length);
      }
      if (space !== 0) throw new Error('brotli: incomplete prefix code');
      return new BrotliCode(lengths);
    }

    function brotliVarUint8(br) {
      if (!br.bits(1)) return 0;
      const n = br.bits(3);
      return n === 0 ? 1 : (1 << n) + br.bits(n);
    }

    function brotliBlockLength(br, code) {
      const symbol = code.decode(br);
      return BROTLI_BLOCK_BASE[symbol] + br.bits(BROTLI_BLOCK_EXTRA[symbol]);
    }

    function brotliContextMap(br, size, trees) {
      const map = new Uint8Array(size);
      if (trees < 2) return map;
      const rle = br.bits(1) ? br.bits(4) + 1 : 0;
      const code = brotliReadCode(br, trees + rle);
      for (let i = 0; i < size;) {
        const symbol = code.decode(br);
        if (symbol === 0) i += 1;
        else if (symbol <= rle) {
          i += (1 << symbol) + br.bits(symbol);
          if (i > size) throw new Error('brotli: context map overflow');
        } else map[i++] = symbol - rle;
      }
      if (br.bits(1)) {
        const mtf = Array.from({ length: 256 }, (_, i) => i);
        for (let i = 0; i < size; i += 1) {
          const index = map[i];
          const value = mtf[index];
          map[i] = value;
          if (index) { mtf.splice(index, 1); mtf.unshift(value); }
        }
      }
      return map;
    }

    /** Decompresses a brotli stream. `dictionary` is the 122,784-byte static dictionary or null. */
    function brotliDecode(data, dictionary, sizeHint) {
      const br = new BrotliBits(data);
      let wbits = 16;
      if (br.bits(1)) {
        const n = br.bits(3);
        if (n) wbits = 17 + n;
        else {
          const m = br.bits(3);
          if (m === 1) throw new Error('brotli: large windows are not supported');
          wbits = m ? 8 + m : 17;
        }
      }
      const window = (1 << wbits) - 16;
      let out = new Uint8Array(Math.max(1024, sizeHint || data.length * 4));
      let pos = 0;
      const ensure = (extra) => {
        if (pos + extra <= out.length) return;
        const grown = new Uint8Array(Math.max(out.length * 2, pos + extra));
        grown.set(out.subarray(0, pos));
        out = grown;
      };
      const distances = [16, 15, 11, 4];
      let distanceIndex = 0;
      for (;;) {
        const last = br.bits(1);
        if (last && br.bits(1)) break;
        const nibbles = br.bits(2);
        if (nibbles === 3) {
          if (br.bits(1)) throw new Error('brotli: reserved bit set');
          const skipBytes = br.bits(2);
          let skip = 0;
          for (let i = 0; i < skipBytes; i += 1) skip |= br.bits(8) << (8 * i);
          if (skipBytes) skip += 1;
          br.align();
          for (let i = 0; i < skip; i += 1) br.byte();
          if (last) break;
          continue;
        }
        let length = 0;
        for (let i = 0; i < nibbles + 4; i += 1) length |= br.bits(4) << (4 * i);
        length += 1;
        if (!last && br.bits(1)) {
          br.align();
          ensure(length);
          for (let i = 0; i < length; i += 1) out[pos++] = br.byte();
          continue;
        }
        const types = [], typeCodes = [], countCodes = [], blockLeft = [], typeNow = [], typePrev = [];
        for (let k = 0; k < 3; k += 1) {
          types[k] = brotliVarUint8(br) + 1;
          typeNow[k] = 0;
          typePrev[k] = 1;
          if (types[k] >= 2) {
            typeCodes[k] = brotliReadCode(br, types[k] + 2);
            countCodes[k] = brotliReadCode(br, 26);
            blockLeft[k] = brotliBlockLength(br, countCodes[k]);
          } else blockLeft[k] = 1 << 28;
        }
        const postfix = br.bits(2);
        const direct = br.bits(4) << postfix;
        const postfixMask = (1 << postfix) - 1;
        const modes = new Uint8Array(types[0]);
        for (let i = 0; i < types[0]; i += 1) modes[i] = br.bits(2);
        const literalTrees = brotliVarUint8(br) + 1;
        const literalMap = brotliContextMap(br, 64 * types[0], literalTrees);
        const distanceTrees = brotliVarUint8(br) + 1;
        const distanceMap = brotliContextMap(br, 4 * types[2], distanceTrees);
        const literalCodes = [], commandCodes = [], distanceCodes = [];
        for (let i = 0; i < literalTrees; i += 1) literalCodes.push(brotliReadCode(br, 256));
        for (let i = 0; i < types[1]; i += 1) commandCodes.push(brotliReadCode(br, 704));
        for (let i = 0; i < distanceTrees; i += 1) distanceCodes.push(brotliReadCode(br, 16 + direct + (48 << postfix)));
        const switchBlock = (k) => {
          let type = typeCodes[k].decode(br);
          type = type === 0 ? typePrev[k] : type === 1 ? typeNow[k] + 1 : type - 2;
          if (type >= types[k]) type -= types[k];
          typePrev[k] = typeNow[k];
          typeNow[k] = type;
          blockLeft[k] = brotliBlockLength(br, countCodes[k]);
        };
        let left = length;
        ensure(length);
        while (left > 0) {
          if (blockLeft[1] === 0) switchBlock(1);
          blockLeft[1] -= 1;
          const command = commandCodes[typeNow[1]].decode(br);
          const cell = command >> 6;
          const insertCode = BROTLI_CELL_INSERT[cell] * 8 + ((command >> 3) & 7);
          const copyCode = BROTLI_CELL_COPY[cell] * 8 + (command & 7);
          const insert = BROTLI_INSERT_BASE[insertCode] + br.bits(BROTLI_INSERT_EXTRA[insertCode]);
          const copy = BROTLI_COPY_BASE[copyCode] + br.bits(BROTLI_COPY_EXTRA[copyCode]);
          for (let i = 0; i < insert; i += 1) {
            if (blockLeft[0] === 0) switchBlock(0);
            blockLeft[0] -= 1;
            const p1 = pos > 0 ? out[pos - 1] : 0;
            const p2 = pos > 1 ? out[pos - 2] : 0;
            const type = typeNow[0];
            const mode = modes[type];
            const context = mode === 0 ? p1 & 63 : mode === 1 ? p1 >> 2
              : mode === 2 ? BROTLI_LUT.utf0[p1] | BROTLI_LUT.utf1[p2]
                : (BROTLI_LUT.signed[p1] << 3) | BROTLI_LUT.signed[p2];
            out[pos++] = literalCodes[literalMap[64 * type + context]].decode(br);
          }
          left -= insert;
          if (left <= 0) break;
          let distance, distanceCode = 0;
          if (cell < 2) distance = distances[(distanceIndex + 3) & 3];
          else {
            if (blockLeft[2] === 0) switchBlock(2);
            blockLeft[2] -= 1;
            const context = copy > 4 ? 3 : copy - 2;
            distanceCode = distanceCodes[distanceMap[4 * typeNow[2] + context]].decode(br);
            if (distanceCode < 16) {
              distance = distances[(distanceIndex + 3 - BROTLI_SHORT_INDEX[distanceCode]) & 3] + BROTLI_SHORT_DELTA[distanceCode];
              if (distance <= 0) throw new Error('brotli: invalid distance');
            } else if (distanceCode < 16 + direct) distance = distanceCode - 15;
            else {
              const code = distanceCode - direct - 16;
              const bits = 1 + (code >> (postfix + 1));
              const offset = ((2 + ((code >> postfix) & 1)) << bits) - 4;
              distance = ((offset + br.bits(bits)) << postfix) + (code & postfixMask) + direct + 1;
            }
          }
          const reach = Math.min(window, pos);
          if (distance > reach) {
            if (!dictionary || dictionary.length < BROTLI_DICT_SIZE) throw new Error('brotli: the stream needs the dictionary');
            if (copy < 4 || copy > 24) throw new Error('brotli: invalid dictionary reference');
            const word = distance - reach - 1;
            const bits = BROTLI_DICT_BITS[copy];
            const transform = BROTLI_TRANSFORMS[word >> bits];
            if (!transform) throw new Error('brotli: invalid transform');
            const start = BROTLI_DICT_OFFSET[copy] + (word & ((1 << bits) - 1)) * copy;
            const [prefix, kind, suffix] = transform;
            let from = start, to = start + copy;
            if (kind >= 1 && kind <= 9) to -= Math.min(kind, copy);
            if (kind >= 12) from = Math.min(to, from + kind - 11);
            ensure(prefix.length + (to - from) + suffix.length);
            out.set(prefix, pos);
            pos += prefix.length;
            const wordStart = pos;
            out.set(dictionary.subarray(from, to), pos);
            pos += to - from;
            if (kind === 10 || kind === 11) {
              for (let i = wordStart; i < pos;) {
                const c = out[i];
                if (c < 0xc0) { if (c >= 97 && c <= 122) out[i] ^= 32; i += 1; }
                else if (c < 0xe0) { if (i + 1 < pos) out[i + 1] ^= 32; i += 2; }
                else { if (i + 2 < pos) out[i + 2] ^= 5; i += 3; }
                if (kind === 10) break;
              }
            }
            out.set(suffix, pos);
            pos += suffix.length;
            left -= prefix.length + (to - from) + suffix.length;
          } else {
            if (distanceCode !== 0) { distances[distanceIndex & 3] = distance; distanceIndex += 1; }
            ensure(copy);
            for (let i = 0; i < copy; i += 1, pos += 1) out[pos] = out[pos - distance];
            left -= copy;
          }
        }
        if (left < 0) throw new Error('brotli: meta-block overrun');
        if (last) break;
      }
      return out.subarray(0, pos);
    }

    // --- font outlines (TrueType glyf, plain or as WOFF2 rebuilds it) -----------------------------

    const WOFF2_TAGS = ['cmap', 'head', 'hhea', 'hmtx', 'maxp', 'name', 'OS/2', 'post', 'cvt ', 'fpgm', 'glyf', 'loca',
      'prep', 'CFF ', 'VORG', 'EBDT', 'EBLC', 'gasp', 'hdmx', 'kern', 'LTSH', 'PCLT', 'VDMX', 'vhea', 'vmtx', 'BASE',
      'GDEF', 'GPOS', 'GSUB', 'EBSC', 'JSTF', 'MATH', 'CBDT', 'CBLC', 'COLR', 'CPAL', 'SVG ', 'sbix', 'acnt', 'avar',
      'bdat', 'bloc', 'bsln', 'cvar', 'fdsc', 'feat', 'fmtx', 'fvar', 'gvar', 'hsty', 'just', 'lcar', 'mort', 'morx',
      'opbd', 'prop', 'trak', 'Zapf', 'Silf', 'Glat', 'Gloc', 'Feat', 'Sill'];

    const tagAt = (bytes, at) => String.fromCharCode(bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]);

    /** Font bytes -> {tag: {bytes, transformed}} for a TrueType/OpenType file or a WOFF2 one. */
    function fontTables(bytes, dictionary) {
      const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
      const tables = {};
      const signature = tagAt(bytes, 0);
      if (signature === 'wOF2') {
        if (view.getUint32(4, false) === 0x74746366) throw new Error('font collections are not supported');
        const count = view.getUint16(12, false);
        let at = 48;
        const base128 = () => {
          let value = 0;
          for (let i = 0; i < 5; i += 1) {
            const b = bytes[at++];
            value = value * 128 + (b & 127);
            if (!(b & 128)) return value;
          }
          throw new Error('woff2: bad UIntBase128');
        };
        const entries = [];
        let total = 0;
        for (let i = 0; i < count; i += 1) {
          const flags = bytes[at++];
          let tag = WOFF2_TAGS[flags & 63];
          if ((flags & 63) === 63) { tag = tagAt(bytes, at); at += 4; }
          const version = flags >> 6;
          const original = base128();
          const transformed = (tag === 'glyf' || tag === 'loca') ? version === 0 : version !== 0;
          const length = transformed ? base128() : original;
          entries.push({ tag, transformed, length });
          total += length;
        }
        const compressedSize = view.getUint32(20, false);
        const data = brotliDecode(bytes.subarray(at, at + compressedSize), dictionary, total);
        let offset = 0;
        for (const entry of entries) {
          tables[entry.tag] = { bytes: data.subarray(offset, offset + entry.length), transformed: entry.transformed };
          offset += entry.length;
        }
        return tables;
      }
      if (signature !== '\0\u0001\0\0' && signature !== 'true') throw new Error('not a TrueType font');
      const count = view.getUint16(4, false);
      for (let i = 0; i < count; i += 1) {
        const base = 12 + i * 16;
        const offset = view.getUint32(base + 8, false);
        tables[tagAt(bytes, base)] = { bytes: bytes.subarray(offset, offset + view.getUint32(base + 12, false)), transformed: false };
      }
      return tables;
    }

    const tableView = (table) => new DataView(table.buffer, table.byteOffset, table.byteLength);

    /** cmap subtable (format 4 or 12, best Unicode one) -> Map(code point -> glyph id). */
    function cmapGlyphs(table) {
      const view = tableView(table);
      const count = view.getUint16(2, false);
      let best = null;
      for (let i = 0; i < count; i += 1) {
        const base = 4 + i * 8;
        const platform = view.getUint16(base, false);
        const encoding = view.getUint16(base + 2, false);
        const offset = view.getUint32(base + 4, false);
        const format = view.getUint16(offset, false);
        const rank = (format === 12 && (platform === 3 && encoding === 10 || platform === 0)) ? 3
          : (format === 4 && (platform === 3 && (encoding === 1 || encoding === 0) || platform === 0)) ? 2 : 0;
        if (rank && (!best || rank > best.rank)) best = { rank, offset, format };
      }
      const glyphs = new Map();
      if (!best) return glyphs;
      if (best.format === 12) {
        const groups = view.getUint32(best.offset + 12, false);
        for (let i = 0; i < groups; i += 1) {
          const base = best.offset + 16 + i * 12;
          const start = view.getUint32(base, false);
          const end = view.getUint32(base + 4, false);
          const glyph = view.getUint32(base + 8, false);
          for (let cp = start; cp <= end; cp += 1) glyphs.set(cp, glyph + cp - start);
        }
        return glyphs;
      }
      const segCount = view.getUint16(best.offset + 6, false) / 2;
      const ends = best.offset + 14;
      const starts = ends + segCount * 2 + 2;
      const deltas = starts + segCount * 2;
      const rangeOffsets = deltas + segCount * 2;
      for (let i = 0; i < segCount; i += 1) {
        const end = view.getUint16(ends + i * 2, false);
        const start = view.getUint16(starts + i * 2, false);
        const idRangeOffset = view.getUint16(rangeOffsets + i * 2, false);
        const idDelta = view.getInt16(deltas + i * 2, false);
        for (let cp = start; cp <= end && cp !== 0xFFFF; cp += 1) {
          let glyph;
          if (idRangeOffset === 0) glyph = (cp + idDelta) & 0xFFFF;
          else {
            const at = rangeOffsets + i * 2 + idRangeOffset + (cp - start) * 2;
            glyph = at + 2 <= table.length ? view.getUint16(at, false) : 0;
            if (glyph) glyph = (glyph + idDelta) & 0xFFFF;
          }
          if (glyph) glyphs.set(cp, glyph);
        }
      }
      return glyphs;
    }

    /** Glyph id -> {contours: [[xs, ys, on]], components} from a plain glyf + loca pair. */
    function plainGlyphs(glyfTable, locaTable, longOffsets, numGlyphs) {
      const loca = tableView(locaTable);
      const glyf = tableView(glyfTable);
      const glyphs = new Array(numGlyphs).fill(null);
      for (let g = 0; g < numGlyphs; g += 1) {
        const start = longOffsets ? loca.getUint32(g * 4, false) : loca.getUint16(g * 2, false) * 2;
        const end = longOffsets ? loca.getUint32(g * 4 + 4, false) : loca.getUint16(g * 2 + 2, false) * 2;
        if (end <= start) continue;
        const contours = glyf.getInt16(start, false);
        let at = start + 10;
        if (contours < 0) {
          glyphs[g] = { components: readComponents(() => { const v = glyf.getUint16(at, false); at += 2; return v; },
            () => { const v = glyf.getInt16(at, false); at += 2; return v; }, () => glyf.getInt8(at++)) };
          continue;
        }
        const endPoints = [];
        for (let c = 0; c < contours; c += 1) { endPoints.push(glyf.getUint16(at, false)); at += 2; }
        const points = contours ? endPoints[contours - 1] + 1 : 0;
        at += 2 + glyf.getUint16(at, false);
        const flags = new Uint8Array(points);
        for (let p = 0; p < points;) {
          const flag = glyf.getUint8(at++);
          flags[p++] = flag;
          if (flag & 8) for (let r = glyf.getUint8(at++); r > 0 && p < points; r -= 1) flags[p++] = flag;
        }
        const xs = new Int32Array(points), ys = new Int32Array(points);
        for (const [array, short, same] of [[xs, 2, 16], [ys, 4, 32]]) {
          let value = 0;
          for (let p = 0; p < points; p += 1) {
            const flag = flags[p];
            if (flag & short) value += (flag & same) ? glyf.getUint8(at++) : -glyf.getUint8(at++);
            else if (!(flag & same)) { value += glyf.getInt16(at, false); at += 2; }
            array[p] = value;
          }
        }
        glyphs[g] = { contours: splitContours(xs, ys, Array.from(flags, (f) => (f & 1) === 1), endPoints) };
      }
      return glyphs;
    }

    function splitContours(xs, ys, on, endPoints) {
      const contours = [];
      let start = 0;
      for (const end of endPoints) {
        contours.push([Array.from(xs.subarray(start, end + 1)), Array.from(ys.subarray(start, end + 1)), on.slice(start, end + 1)]);
        start = end + 1;
      }
      return contours;
    }

    /** Composite glyph components: [{glyph, dx, dy, matrix}] (point-anchored ones are skipped). */
    function readComponents(u16, i16, i8) {
      const components = [];
      for (let more = true; more;) {
        const flags = u16();
        const glyph = u16();
        let a, b;
        if (flags & 1) { a = i16(); b = i16(); } else { a = i8(); b = i8(); }
        let matrix = [1, 0, 0, 1];
        const f2dot14 = () => i16() / 16384;
        if (flags & 8) { const s = f2dot14(); matrix = [s, 0, 0, s]; }
        else if (flags & 0x40) { const sx = f2dot14(); const sy = f2dot14(); matrix = [sx, 0, 0, sy]; }
        else if (flags & 0x80) matrix = [f2dot14(), f2dot14(), f2dot14(), f2dot14()];
        components.push({ glyph, dx: a, dy: b, matrix, anchored: !(flags & 2), instructions: !!(flags & 0x100) });
        more = !!(flags & 0x20);
      }
      return components;
    }

    /** WOFF2 transformed glyf (WOFF2 section 5.1) -> the same records as plainGlyphs. */
    function woff2Glyphs(table) {
      const view = tableView(table);
      const numGlyphs = view.getUint16(4, false);
      const sizes = [];
      for (let i = 0; i < 7; i += 1) sizes.push(view.getUint32(8 + i * 4, false));
      let offset = 36;
      const streams = sizes.map((size) => { const s = { at: offset, end: offset + size }; offset += size; return s; });
      const [nContour, nPoints, flagStream, glyphStream, compositeStream] = streams;
      const u8 = (s) => { if (s.at >= s.end) throw new Error('woff2: glyf stream overrun'); return table[s.at++]; };
      const u16 = (s) => (u8(s) << 8) | u8(s);
      const i16 = (s) => { const v = u16(s); return v & 0x8000 ? v - 0x10000 : v; };
      const u255 = (s) => {
        const code = u8(s);
        if (code === 253) return u16(s);
        if (code === 255) return u8(s) + 253;
        if (code === 254) return u8(s) + 506;
        return code;
      };
      const glyphs = new Array(numGlyphs).fill(null);
      for (let g = 0; g < numGlyphs; g += 1) {
        const contours = i16(nContour);
        if (contours === 0) continue;
        if (contours < 0) {
          const components = readComponents(() => u16(compositeStream), () => i16(compositeStream),
            () => { const v = u8(compositeStream); return v & 0x80 ? v - 0x100 : v; });
          if (components.some((c) => c.instructions)) u255(glyphStream);  // instructions have their own stream
          glyphs[g] = { components };
          continue;
        }
        const endPoints = [];
        let total = 0;
        for (let c = 0; c < contours; c += 1) { total += u255(nPoints); endPoints.push(total - 1); }
        const xs = new Int32Array(total), ys = new Int32Array(total);
        const on = new Array(total);
        let x = 0, y = 0;
        for (let p = 0; p < total; p += 1) {
          let flag = u8(flagStream);
          on[p] = !(flag & 128);
          flag &= 127;
          let dx, dy;
          if (flag < 10) { dx = 0; dy = ((flag & 14) << 7) + u8(glyphStream); }
          else if (flag < 20) { dx = (((flag - 10) & 14) << 7) + u8(glyphStream); dy = 0; }
          else if (flag < 84) { const b0 = flag - 20, b1 = u8(glyphStream); dx = 1 + (b0 & 0x30) + (b1 >> 4); dy = 1 + ((b0 & 0x0c) << 2) + (b1 & 15); }
          else if (flag < 120) { const b0 = flag - 84; dx = 1 + (Math.floor(b0 / 12) << 8) + u8(glyphStream); dy = 1 + (((b0 % 12) >> 2) << 8) + u8(glyphStream); }
          else if (flag < 124) { const b1 = u8(glyphStream), b2 = u8(glyphStream), b3 = u8(glyphStream); dx = (b1 << 4) + (b2 >> 4); dy = ((b2 & 15) << 8) + b3; }
          else { dx = u16(glyphStream); dy = u16(glyphStream); }
          if (flag < 10) dy = flag & 1 ? dy : -dy;
          else if (flag < 20) dx = flag & 1 ? dx : -dx;
          else { dx = flag & 1 ? dx : -dx; dy = flag & 2 ? dy : -dy; }
          x += dx; y += dy;
          xs[p] = x; ys[p] = y;
        }
        u255(glyphStream);  // instruction length; the instructions have their own stream
        glyphs[g] = { contours: splitContours(xs, ys, on, endPoints) };
      }
      return glyphs;
    }

    /** A glyph's contours with composite components resolved (null when that is not possible). */
    function glyphContours(glyphs, g, depth) {
      const glyph = glyphs[g];
      if (!glyph) return null;
      if (glyph.contours) return glyph.contours;
      if ((depth || 0) > 8) return null;
      const contours = [];
      for (const part of glyph.components) {
        if (part.anchored) return null;
        const inner = glyphContours(glyphs, part.glyph, (depth || 0) + 1);
        if (!inner) return null;
        const [a, b, c, d] = part.matrix;
        for (const [xs, ys, on] of inner) {
          contours.push([xs.map((x, i) => a * x + c * ys[i] + part.dx), ys.map((y, i) => b * xs[i] + d * y + part.dy), on]);
        }
      }
      return contours;
    }

    /** Font bytes -> {glyph(cp) -> {contours, advance} | null}. Throws on fonts it cannot read. */
    function fontOutlines(bytes, dictionary) {
      const tables = fontTables(bytes, dictionary);
      if (!tables.glyf || !tables.cmap || !tables.hmtx || !tables.hhea) throw new Error('font has no TrueType outlines');
      const cmap = cmapGlyphs(tables.cmap.bytes);
      let glyphs;
      if (tables.glyf.transformed) glyphs = woff2Glyphs(tables.glyf.bytes);
      else {
        const head = tableView(tables.head.bytes);
        const maxp = tableView(tables.maxp.bytes);
        glyphs = plainGlyphs(tables.glyf.bytes, tables.loca.bytes, head.getInt16(50, false) === 1, maxp.getUint16(4, false));
      }
      const metrics = tables.hhea.bytes.length >= 36 ? tableView(tables.hhea.bytes).getUint16(34, false) : 0;
      const hmtx = tables.hmtx.bytes;
      // A transformed hmtx starts with a flags byte; the advances come first either way.
      const advanceAt = tables.hmtx.transformed ? 1 : 0;
      const step = tables.hmtx.transformed ? 2 : 4;
      const advance = (g) => {
        const index = Math.min(g, metrics - 1);
        const at = advanceAt + index * step;
        return index >= 0 && at + 2 <= hmtx.length ? (hmtx[at] << 8) | hmtx[at + 1] : 0;
      };
      return {
        glyph(cp) {
          const g = cmap.get(cp);
          if (g === undefined) return null;
          const contours = glyphContours(glyphs, g);
          return contours && contours.length ? { contours, advance: advance(g) } : null;
        },
      };
    }

    // --- outline table ---------------------------------------------------------------------------
    // Every cipher font draws its glyphs from one design, and a character keeps its outline across
    // fonts up to contour order, start point, small curve edits and a few units of jitter. Symmetric
    // Hausdorff distance between flattened outlines separates them cleanly, so a glyph within
    // `tolerance` of a labelled exemplar (resource `outlines`, data/qdn_outline_table.json) is that
    // character when no other character is within `separation`. Mirrors qdn_font_decoder.py: the six
    // exemplars whose coarse edge maps are closest are compared exactly.

    const OUTLINE_STEP = 12;
    const OUTLINE_GRID = 24;
    const OUTLINE_CELLS = OUTLINE_GRID * OUTLINE_GRID;
    const OUTLINE_CANDIDATES = 6;

    /** base64 outline (desktop encode_outline) -> [[xs, ys, on]]. */
    function decodeOutline(text) {
      const raw = base64ToBytes(text);
      const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
      const count = view.getUint16(0, true);
      let at = 2;
      const contours = [];
      for (let c = 0; c < count; c += 1) {
        const size = view.getUint16(at, true);
        at += 2;
        const bits = raw.subarray(at, at + ((size + 7) >> 3));
        at += (size + 7) >> 3;
        const xs = new Array(size), ys = new Array(size), on = new Array(size);
        for (let i = 0; i < size; i += 1) {
          on[i] = ((bits[i >> 3] >> (i & 7)) & 1) === 1;
          xs[i] = view.getInt16(at, true);
          ys[i] = view.getInt16(at + 2, true);
          at += 4;
        }
        contours.push([xs, ys, on]);
      }
      return contours;
    }

    /** TrueType quadratic contours -> Float32Array [x0, y0, x1, y1, ...] sampled about 12 units apart. */
    function flattenOutline(contours) {
      const vx = [], vy = [], ends = [];
      for (const [xs0, ys0, on0] of contours) {
        if (!xs0.length) continue;
        let xs = xs0, ys = ys0, on = on0;
        if (!on.includes(true)) {
          // All off-curve: start at an implied midpoint.
          xs = [(xs[0] + xs[xs.length - 1]) / 2, ...xs];
          ys = [(ys[0] + ys[ys.length - 1]) / 2, ...ys];
          on = [true, ...on];
        }
        const n = xs.length;
        const first = on.indexOf(true);
        // Polyline vertices: on-curve points plus six samples per curve.
        vx.push(xs[first]);
        vy.push(ys[first]);
        for (let index = 1; index <= n;) {
          const i = (first + index) % n;
          if (on[i]) { vx.push(xs[i]); vy.push(ys[i]); index += 1; continue; }
          const j = (first + index + 1) % n;
          const ex = on[j] ? xs[j] : (xs[i] + xs[j]) / 2;
          const ey = on[j] ? ys[j] : (ys[i] + ys[j]) / 2;
          const ax = vx[vx.length - 1], ay = vy[vy.length - 1];
          for (let k = 1; k <= 6; k += 1) {
            const t = k / 6, u = 1 - t;
            vx.push(u * u * ax + 2 * u * t * xs[i] + t * t * ex);
            vy.push(u * u * ay + 2 * u * t * ys[i] + t * t * ey);
          }
          index += on[j] ? 2 : 1;
        }
        ends.push(vx.length);
      }
      if (!ends.length) return null;
      // Densify every polyline segment.
      let total = 0;
      for (let r = 0, start = 0; r < ends.length; start = ends[r], r += 1) {
        for (let k = start; k + 1 < ends[r]; k += 1) total += Math.max(1, Math.ceil(Math.hypot(vx[k + 1] - vx[k], vy[k + 1] - vy[k]) / OUTLINE_STEP));
      }
      const out = new Float32Array(total * 2);
      let o = 0;
      for (let r = 0, start = 0; r < ends.length; start = ends[r], r += 1) {
        for (let k = start; k + 1 < ends[r]; k += 1) {
          const dx = vx[k + 1] - vx[k], dy = vy[k + 1] - vy[k];
          const count = Math.max(1, Math.ceil(Math.hypot(dx, dy) / OUTLINE_STEP));
          for (let s = 0; s < count; s += 1) {
            out[o++] = vx[k] + dx * (s / count);
            out[o++] = vy[k] + dy * (s / count);
          }
        }
      }
      return out;
    }

    /** Point counts per 24x24 cell, each summed with its eight neighbours (unnormalised). */
    function edgeCounts(points) {
      const size = OUTLINE_GRID, wide = size + 2;
      const cell = Math.fround(1100 / size);  // float32 arithmetic, as the desktop's numpy
      const grid = new Uint16Array(wide * wide);
      for (let i = 0; i < points.length; i += 2) {
        const cx = Math.min(size - 1, Math.max(0, Math.trunc(Math.fround(Math.fround(points[i] + 50) / cell))));
        const cy = Math.min(size - 1, Math.max(0, Math.trunc(Math.fround(Math.fround(points[i + 1] + 200) / cell))));
        grid[(cy + 1) * wide + cx + 1] += 1;
      }
      const counts = new Uint16Array(OUTLINE_CELLS);
      for (let y = 0; y < size; y += 1) {
        for (let x = 0; x < size; x += 1) {
          const at = (y + 1) * wide + x + 1;
          counts[y * size + x] = grid[at - wide - 1] + grid[at - wide] + grid[at - wide + 1]
            + grid[at - 1] + grid[at] + grid[at + 1] + grid[at + wide - 1] + grid[at + wide] + grid[at + wide + 1];
        }
      }
      return counts;
    }

    function countNorm(counts) {
      let sum = 0;
      for (let i = 0; i < counts.length; i += 1) sum += counts[i] * counts[i];
      return Math.sqrt(sum);
    }

    function outlineBox(points) {
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (let i = 0; i < points.length; i += 2) {
        const x = points[i], y = points[i + 1];
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
      }
      return [x0, y0, x1, y1];
    }

    /** Largest distance from a point of `a` (every `stride`-th) to its nearest point of `b`. */
    function nearestWorst(a, b, stride) {
      const count = b.length >> 1;
      let worst = 0, near = 0;
      for (let i = 0; i < a.length; i += 2 * stride) {
        const ax = a[i], ay = a[i + 1];
        let best = Infinity, found = near;
        // Samples run along the contours, so the search starts where the last nearest point was.
        for (let step = 0, j = near; step < count; step += 1, j = j + 1 === count ? 0 : j + 1) {
          const dx = ax - b[2 * j], dy = ay - b[2 * j + 1];
          const d = dx * dx + dy * dy;
          if (d < best) {
            best = d;
            found = j;
            // Past this the point can no longer raise the maximum.
            if (best <= worst) break;
          }
        }
        near = found;
        if (best > worst) worst = best;
      }
      return Math.sqrt(worst);
    }

    /**
     * Symmetric Hausdorff distance between two sampled outlines. Returns a lower bound above `bound`
     * when the bounding boxes or a quarter of the samples already show that much.
     */
    function outlineDistance(a, b, bound, boxA, boxB) {
      const limit = bound === undefined ? Infinity : bound;
      const ba = boxA || outlineBox(a), bb = boxB || outlineBox(b);
      let gap = 0;
      for (let i = 0; i < 4; i += 1) gap = Math.max(gap, Math.abs(ba[i] - bb[i]));
      if (gap > limit) return gap;
      const coarse = Math.max(nearestWorst(a, b, 4), nearestWorst(b, a, 4));
      if (coarse > limit) return coarse;
      return Math.max(nearestWorst(a, b, 1), nearestWorst(b, a, 1));
    }

    /**
     * The labelled exemplars. Their edge maps sit in one cell-major matrix (cell c of exemplar i at
     * c * size + i); their sampled outlines are made again when they become candidates (all of them at
     * once would take tens of megabytes).
     */
    class OutlineTable {
      constructor() {
        this.tolerance = 0;
        this.separation = 0;
        this.chars = [];
        this.texts = [];
        this.counts = new Uint16Array(0);
        this.norms = new Float64Array(0);
        this.samples = new Map();  // exemplar index -> {points, box}
      }

      /** Builds the table from the resource bytes, yielding now and then so the page stays responsive. */
      static async build(raw) {
        const table = new OutlineTable();
        try {
          const saved = raw ? JSON.parse(new TextDecoder('utf-8').decode(raw)) : null;
          if (!saved || saved.schema !== 1) return table;
          const glyphs = Array.isArray(saved.glyphs) ? saved.glyphs : [];
          const counts = new Uint16Array(glyphs.length * OUTLINE_CELLS);
          const norms = [], chars = [], texts = [];
          let started = Date.now();
          for (const entry of glyphs) {
            const [char, text] = Array.isArray(entry) ? entry : [];
            if (typeof char !== 'string' || [...char].length !== 1 || typeof text !== 'string') continue;
            const points = flattenOutline(decodeOutline(text));
            if (!points) continue;
            const map = edgeCounts(points);
            counts.set(map, chars.length * OUTLINE_CELLS);
            norms.push(countNorm(map));
            chars.push(char);
            texts.push(text);
            if (Date.now() - started > 50) { await sleep(0); started = Date.now(); }
          }
          table.tolerance = Number(saved.tolerance === undefined ? 20 : saved.tolerance);
          table.separation = Number(saved.separation === undefined ? 32 : saved.separation);
          table.chars = chars;
          table.texts = texts;
          const size = chars.length;
          table.counts = new Uint16Array(size * OUTLINE_CELLS);
          for (let i = 0; i < size; i += 1) {
            for (let c = 0; c < OUTLINE_CELLS; c += 1) table.counts[c * size + i] = counts[i * OUTLINE_CELLS + c];
          }
          table.norms = Float64Array.from(norms);
        } catch (e) {
          // A missing or corrupt optional table must not stop decoding.
          return new OutlineTable();
        }
        return table;
      }

      get size() { return this.chars.length; }

      sample(index) {
        let entry = this.samples.get(index);
        if (!entry) {
          const points = flattenOutline(decodeOutline(this.texts[index]));
          entry = { points, box: outlineBox(points) };
          if (this.samples.size > 2000) this.samples.clear();
          this.samples.set(index, entry);
        }
        return entry;
      }

      /** {character, distance, runnerUp} for a clear, unique hit, else null. */
      match(points) {
        if (!this.chars.length || !points) return null;
        const query = edgeCounts(points);
        const size = this.chars.length;
        const sums = new Float64Array(size);
        for (let c = 0; c < OUTLINE_CELLS; c += 1) {
          const weight = query[c];
          if (!weight) continue;
          const column = this.counts.subarray(c * size, (c + 1) * size);
          for (let i = 0; i < size; i += 1) sums[i] += column[i] * weight;
        }
        // The six exemplars with the most similar edge maps (cosine).
        const top = [];
        for (let i = 0; i < size; i += 1) {
          const score = this.norms[i] ? sums[i] / this.norms[i] : 0;
          if (top.length < OUTLINE_CANDIDATES || score > top[top.length - 1][0]) {
            let at = top.length;
            while (at > 0 && top[at - 1][0] < score) at -= 1;
            top.splice(at, 0, [score, i]);
            if (top.length > OUTLINE_CANDIDATES) top.pop();
          }
        }
        // Distances past the separation only need to be known as "far".
        const box = outlineBox(points);
        const scored = top.map(([, i]) => {
          const exemplar = this.sample(i);
          return [outlineDistance(points, exemplar.points, this.separation, box, exemplar.box), this.chars[i]];
        }).sort((x, y) => x[0] - y[0] || (x[1] < y[1] ? -1 : x[1] > y[1] ? 1 : 0));
        const [distance, character] = scored[0];
        const rival = scored.slice(1).find(([, c]) => c !== character);
        const runnerUp = rival ? rival[0] : null;
        // Accept only a clear, unique hit: other characters must be far away.
        if (distance > this.tolerance || (runnerUp !== null && runnerUp <= this.separation)) return null;
        return { character, distance, runnerUp };
      }
    }

    /** A cipher glyph's sampled outline, mirrored about its advance when the reader mirrors it. */
    function outlinePoints(outlines, ch, mirrored) {
      const glyph = outlines ? outlines.glyph(ch.codePointAt(0)) : null;
      if (!glyph) return null;
      const points = flattenOutline(glyph.contours);
      if (points && mirrored) for (let i = 0; i < points.length; i += 2) points[i] = glyph.advance - points[i];
      return points;
    }

    function outlineTable() {
      if (!state.outlineTable) {
        const build = OutlineTable.build(state.files.outlines).then((table) => {
          if (state.outlineTable === build) state.outlineSize = table.size;
          return table;
        });
        state.outlineTable = build;
      }
      return state.outlineTable;
    }

    /** A cipher font's outlines, read once per font (null when the font cannot be read). */
    function coreOutlines(core) {
      if (state.outlineFonts.has(core.digest)) return state.outlineFonts.get(core.digest);
      let outlines = null;
      try {
        outlines = fontOutlines(core.bytes, state.files.brotli || null);
      } catch (e) {
        // Not a TrueType outline font, or a WOFF2 one without the dictionary it needs: shape matching only.
      }
      // Parsed outlines are large; the glyph cache keeps their answers.
      if (state.outlineFonts.size >= 4) state.outlineFonts.delete(state.outlineFonts.keys().next().value);
      state.outlineFonts.set(core.digest, outlines);
      return outlines;
    }

    // --- matching --------------------------------------------------------------------------------

    function dot(a, b) {
      let sum = 0;
      for (let i = 0; i < a.length; i += 1) sum += a[i] * b[i];
      return sum;
    }

    function bandCosines(source, target) {
      const edges = [0, Math.floor(GRID / 3), Math.floor((2 * GRID) / 3), GRID];
      const values = [];
      for (let band = 0; band < 3; band += 1) {
        let ab = 0, aa = 0, bb = 0;
        for (let y = 0; y < GRID; y += 1) {
          for (let x = edges[band]; x < edges[band + 1]; x += 1) {
            const a = source[y * GRID + x];
            const b = target[y * GRID + x];
            ab += a * b; aa += a * a; bb += b * b;
          }
        }
        const norm = Math.sqrt(aa) * Math.sqrt(bb);
        values.push(norm ? ab / norm : 0);
      }
      return values;
    }

    /** Shape match of one cipher glyph. `vector` is its 56x56 vector, `coarse` its 14x14 one. */
    function shapeMatch(vector, coarse) {
      const ref = state.ref;
      const dim = COARSE_GRID * COARSE_GRID;
      const total = ref.chars.length;
      // Stage 1: best COARSE_KEEP by the coarse vector.
      const keep = Math.min(COARSE_KEEP, total);
      const best = new Int32Array(keep).fill(-1);
      const bestScore = new Float32Array(keep).fill(-Infinity);
      let floor = -Infinity;
      for (let i = 0; i < total; i += 1) {
        const base = i * dim;
        let sum = 0;
        for (let k = 0; k < dim; k += 1) sum += coarse[k] * ref.coarse[base + k];
        if (sum <= floor) continue;
        let at = keep - 1;
        while (at > 0 && bestScore[at - 1] < sum) {
          bestScore[at] = bestScore[at - 1];
          best[at] = best[at - 1];
          at -= 1;
        }
        bestScore[at] = sum;
        best[at] = i;
        floor = bestScore[keep - 1];
      }
      // Stage 2: the desktop's full comparison among those.
      const scored = [];
      for (let k = 0; k < keep; k += 1) {
        if (best[k] < 0) continue;
        const reference = fineReference(ref.chars[best[k]]);
        if (reference) scored.push([best[k], dot(vector, reference)]);
      }
      scored.sort((x, y) => (y[1] - x[1]) || (x[0] - y[0]));
      const top = scored.slice(0, CANDIDATES);
      if (!top.length) return null;
      const topScores = top.map((entry) => entry[1]);
      let pick = 0;
      let pickValue = -Infinity;
      top.forEach((entry, i) => {
        if (topScores[i] >= topScores[0] - PRIOR_WINDOW) {
          const value = topScores[i] + PRIOR_WEIGHT * ref.prior[entry[0]];
          if (value > pickValue) { pickValue = value; pick = i; }
        }
      });
      // A large shared right-hand component can drown out the distinguishing left strokes (the
      // person/heart radicals). Only revisit weak matches when a candidate has an almost exact left
      // component and the original does not.
      let refined = false;
      let regions = null;
      if (topScores[0] < CONFIDENCE_SCORE) {
        regions = top.map((entry) => bandCosines(vector, fineReference(ref.chars[entry[0]])));
        const balanced = top.map((entry, i) => (topScores[i] + Math.min(...regions[i])) / 2);
        let proposed = 0;
        balanced.forEach((value, i) => { if (value > balanced[proposed]) proposed = i; });
        if (proposed === pick) {
          // A near tie: take the candidate with an almost exact left component that is within the slack.
          let alternative = -1;
          balanced.forEach((value, i) => {
            if (i !== pick && regions[i][0] >= REFINE_LEFT && value >= balanced[pick] - REFINE_SLACK
                && (alternative < 0 || value > balanced[alternative])) alternative = i;
          });
          if (alternative >= 0) proposed = alternative;
        }
        if (proposed !== pick && regions[proposed][0] >= REFINE_LEFT && regions[pick][0] < 0.85
            && balanced[proposed] >= balanced[pick] - REFINE_SLACK) {
          pick = proposed;
          refined = true;
        }
      }
      const score = topScores[pick];
      const margin = top.length > 1 ? topScores[0] - topScores[1] : 1;
      const reasons = [];
      if (score < CONFIDENCE_SCORE) reasons.push('low_shape_score');
      if (margin < CONFIDENCE_MARGIN) reasons.push('ambiguous_shape');
      if (refined) reasons.push('component_refinement');
      return {
        character: ref.chars[top[pick][0]], score, margin, method: 'shape_match', reasons,
        candidates: top.map((entry, i) => (regions
          ? { character: ref.chars[entry[0]], score: topScores[i], componentScores: regions[i] }
          : { character: ref.chars[entry[0]], score: topScores[i] })),
      };
    }

    // --- decoding --------------------------------------------------------------------------------

    class Decoder {
      constructor(payload) {
        this.payload = payload || {};
        this.faces = this.payload.faces || [];
        this.fonts = [];
        this.missingFonts = [];
        this.diagnostics = [];
        this.reported = new Set();
        this.suspiciousPassthrough = 0;
        this.decoded = 0;
        this.verified = 0;
        this.outline = 0;
        this.lowConfidence = 0;
      }

      async loadFonts() {
        const data = this.payload.fontData || {};
        const digests = this.payload.fontDigests || {};
        for (const face of this.faces) {
          const family = face.family || '';
          const encoded = data[face.url || ''];
          try {
            let core;
            if (encoded && !String(encoded).startsWith('ERR')) {
              const bytes = base64ToBytes(encoded);
              core = await coreFor(await sha256Hex(bytes), bytes);
            } else if (!encoded && digests[face.url || ''] && state.cores.has(digests[face.url])) {
              core = state.cores.get(digests[face.url]);
            } else {
              this.missingFonts.push({ family, reason: 'missing_font_data' });
              continue;
            }
            this.fonts.push(new CipherFont(family, face.range || '', core));
          } catch (e) {
            this.missingFonts.push({ family, reason: 'invalid_font', error: String((e && e.message) || e) });
          }
        }
      }

      // Within a family the browser tries the most recently defined face first. The reader adds a
      // script-built face after the stylesheet ones; it redraws the decoy characters the others render.
      cipherFor(ch, families) {
        for (const family of families) {
          for (let i = this.fonts.length - 1; i >= 0; i -= 1) {
            const cipher = this.fonts[i];
            if (cipher.family === family && cipher.covers(ch)) return cipher;
          }
        }
        return null;
      }

      literal(ch, families) {
        const cp = ch.codePointAt(0);
        if (isCjk(cp) && this.faces.some((face) => families.includes(face.family)
            && parseUnicodeRange(face.range).some(([low, high]) => low <= cp && cp <= high))) {
          this.suspiciousPassthrough += 1;
          const key = `literal:${ch}:${families.join(',')}`;
          if (!this.reported.has(key)) {
            this.reported.add(key);
            this.diagnostics.push({ source: ch, character: ch, reasons: ['unmapped_cipher_range'], families, candidates: [] });
          }
        }
        return ch;
      }

      /** Resolves every glyph in `keys` ([cipher, ch, mirrored]) that is not cached yet. */
      async resolve(keys) {
        const todo = [];
        const seen = new Set();
        for (const [cipher, ch, mirrored] of keys) {
          const key = `${cipher.core.digest}:${ch.codePointAt(0)}:${mirrored ? 1 : 0}`;
          if (state.glyphs.has(key) || seen.has(key)) continue;
          seen.add(key);
          todo.push([cipher, ch, mirrored, key]);
        }
        let work = 0;
        const table = todo.length ? await outlineTable() : null;
        for (const [cipher, ch, mirrored, key] of todo) {
          const entries = verifiedEntries(cipher.core.digest);
          const verified = entries[`${ch.codePointAt(0).toString(16).toUpperCase()}:${mirrored ? 1 : 0}`];
          if (verified !== undefined) {
            state.glyphs.set(key, { character: verified, score: null, margin: null, candidates: [], reasons: [], method: 'verified_font_map' });
            continue;
          }
          const known = table.size ? table.match(outlinePoints(coreOutlines(cipher.core), ch, mirrored)) : null;
          if (known) {
            state.glyphs.set(key, {
              character: known.character, score: null, margin: null,
              outlineDistance: Math.round(known.distance * 100) / 100,
              outlineRunnerUp: known.runnerUp === null ? null : Math.round(known.runnerUp * 100) / 100,
              candidates: [], reasons: [], method: 'outline_table',
            });
            work += 1;
            if ((work & 15) === 0) await sleep(0);
            continue;
          }
          const crop = renderGlyph(cipher.core.css, ch, RENDER_SIZE);
          const vector = glyphVector(crop, GRID, BLUR, mirrored);
          if (!vector) {
            state.glyphs.set(key, { character: ch, score: 0, margin: 0, candidates: [], reasons: ['empty_glyph'] });
            continue;
          }
          if (!state.ref) while (!(await prepare(15000)).done) { /* build the reference */ }
          const coarse = glyphVector(crop, COARSE_GRID, 0, mirrored);
          const match = shapeMatch(vector, coarse);
          if (state.glyphs.size > 200000) state.glyphs.clear();
          state.glyphs.set(key, match || { character: ch, score: 0, margin: 0, candidates: [], reasons: ['empty_glyph'] });
          work += 1;
          if ((work & 15) === 0) await sleep(0);
        }
      }

      lookup(cipher, ch, mirrored) {
        const key = `${cipher.core.digest}:${ch.codePointAt(0)}:${mirrored ? 1 : 0}`;
        const match = state.glyphs.get(key);
        this.decoded += 1;
        if (match.method === 'verified_font_map') this.verified += 1;
        else if (match.method === 'outline_table') this.outline += 1;
        if (match.reasons && match.reasons.length) this.lowConfidence += 1;
        if (!this.reported.has(key)) {
          this.reported.add(key);
          this.diagnostics.push(Object.assign({ fontDigest: cipher.core.digest, family: cipher.family, source: ch, mirrored: !!mirrored }, match));
        }
        return match.character;
      }

      async decodeParagraphs(paragraphs) {
        const familyLists = new Map();
        const plan = [];
        for (const items of paragraphs) {
          const row = [];
          for (const item of items) {
            const mirrored = !!item.mirrored;
            for (const part of item.parts || []) {
              const fonts = part.fonts || '';
              let families = familyLists.get(fonts);
              if (!families) {
                families = fonts.split(',').map((name) => name.trim().replace(/^["']+|["']+$/g, ''));
                familyLists.set(fonts, families);
              }
              for (const ch of part.text || '') row.push([this.cipherFor(ch, families), ch, mirrored, families]);
            }
          }
          plan.push(row);
        }
        await this.resolve(plan.flatMap((row) => row.filter((entry) => entry[0] !== null).map((entry) => entry.slice(0, 3))));
        const lines = [];
        for (const row of plan) {
          const line = row.map(([cipher, ch, mirrored, families]) =>
            (cipher === null ? this.literal(ch, families) : this.lookup(cipher, ch, mirrored))).join('').trim();
          if (line) lines.push(line);
        }
        return lines;
      }

      stats() {
        return {
          decoded: this.decoded,
          verifiedGlyphs: this.verified,
          outlineTableGlyphs: this.outline,
          shapeMatchedGlyphs: this.decoded - this.verified - this.outline,
          lowConfidence: this.lowConfidence,
          fonts: this.fonts.map((cipher) => cipher.family),
          missingFonts: this.missingFonts,
          suspiciousPassthrough: this.suspiciousPassthrough,
          unstableParagraphs: this.payload.unstableParagraphs || [],
          thresholds: { score: CONFIDENCE_SCORE, margin: CONFIDENCE_MARGIN, priorWindow: PRIOR_WINDOW },
        };
      }
    }

    /** Payload (as qdn.extract returns it) -> {lines, stats[, diagnostics]}. */
    async function decode(payload, options) {
      const decoder = new Decoder(payload);
      await decoder.loadFonts();
      const lines = await decoder.decodeParagraphs((payload && payload.paragraphs) || []);
      const result = { lines, stats: decoder.stats() };
      if (options && options.diagnostics) result.diagnostics = decoder.diagnostics;
      return result;
    }

    /** Same, with the payload as the JSON text native code kept (avoids parsing it twice). */
    async function decodeJson(json, options) {
      return decode(JSON.parse(json), options);
    }

    /** SHA-256 digests of the cipher fonts held, so qdn.extract can leave them out. */
    function knownFonts() {
      return [...state.cores.keys()];
    }

    function status() {
      return {
        resources: resourceStatus(),
        prepared: !!state.ref,
        referenceChars: state.ref ? state.ref.chars.length : 0,
        fonts: state.cores.size,
        glyphs: state.glyphs.size,
        outlineExemplars: state.outlineSize,
      };
    }

    /** Outline-table answers ({character, distance, runnerUp} or null) for [char, mirrored] pairs of one font. */
    async function matchOutlines(fontBytes, glyphs) {
      const table = await outlineTable();
      const outlines = fontOutlines(fontBytes, state.files.brotli || null);
      return glyphs.map(([ch, mirrored]) => table.match(outlinePoints(outlines, ch, !!mirrored)));
    }

    return {
      putResource, resourceStatus, prepare, decode, decodeJson, knownFonts, status, parseUnicodeRange, parseCmap,
      // The dictionary defaults to the `brotli` resource.
      brotliDecode: (data, dictionary) => brotliDecode(data, dictionary === undefined ? state.files.brotli || null : dictionary),
      fontOutlines: (bytes, dictionary) => fontOutlines(bytes, dictionary === undefined ? state.files.brotli || null : dictionary),
      matchOutlines,
    };
  }());

  // ---------------------------------------------------------------------------------------------
  // rbooks: product page, episode catalog (book API), viewer extraction, library ownership
  //
  // The book API sits behind a bot check that only a real browser navigation passes, and the product
  // page's CSP blocks fetches to it. So native code opens the API URL as a top-level page and calls
  // rbooks.discover there (same-origin fetches), and calls the other entry points on the product page,
  // the viewer page, or the library page as noted on each.
  // ---------------------------------------------------------------------------------------------

  const rbooks = hard.rbooks;
  const reEscape = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const rbooksOrigin = `https://${SITE.rbooksDomain}`;
  const viewerSelectors = ['div.pages', '#viewer_contents', '[id^="rbooks_c"]', '[id^="\u0072\u0069\u0064\u0069_c"]', 'article.chapter', '.chapter'];
  const firstViewer = () => {
    for (const selector of viewerSelectors) {
      const node = document.querySelector(selector);
      if (node) return node;
    }
    return null;
  };
  // Zero-width, bidi and soft-hyphen characters, by code point so the source stays plain ASCII.
  const INVISIBLE = new RegExp('[' + [[0x200B, 0x200F], [0x2028, 0x202F], [0x2060, 0x2069], [0xFEFF, 0xFEFF], [0xAD, 0xAD]]
    .map(([from, to]) => String.fromCharCode(from) + (from === to ? '' : '-' + String.fromCharCode(to))).join('') + ']', 'g');
  const stripInvisible = (value) => String(value == null ? '' : value).replace(INVISIBLE, '');
  const bookIdOf = (value) => {
    const match = /\/books\/(\d+)/i.exec(String(value || ''));
    return match ? match[1] : '';
  };

  /** Product page: has the title and either episode buttons or the embedded series id. */
  rbooks.productReady = function () {
    if (!document.querySelector('h1, meta[property="og:title"]')) return false;
    if (document.querySelector('.serial_book_direct_view_button')) return true;
    return [...document.querySelectorAll('script:not([src])')].some((node) => /"seriesId"\s*:\s*"\d+"/.test(node.textContent || ''));
  };

  /** Library page: which product it stands for, and whether the title is unavailable / adult only. */
  rbooks.libraryProbe = function () {
    const canonical = (document.querySelector('link[rel="canonical"]') || {}).href || metaContent('og:url');
    const current = location.href;
    const productRe = new RegExp(`^https?:\\/\\/([^/]+\\.)?${reEscape(SITE.rbooksDomain)}\\/books\\/(\\d+)(?:[/?#]|$)`, 'i');
    let product = [current, canonical].find((value) => productRe.test(value || '')) || '';
    // The library SPA has no canonical link; its volume rows link to each product. Volume ids rise with
    // volume order, so the lowest is the series start.
    if (!product) {
      const ids = [...document.querySelectorAll('a[href]')]
        .map((a) => (productRe.exec(a.href) || [])[2]).filter(Boolean).map(Number);
      if (ids.length) product = `${rbooksOrigin}/books/${Math.min(...ids)}`;
    }
    const body = bodyText();
    return {
      product,
      unavailable: /이용불가/.test(body),
      adult: /19세 미만/.test(body),
      title: metaContent('og:title') || (document.querySelector('h1') || {}).innerText || document.title || '',
      viewer: !!firstViewer(),
    };
  };

  /** Metadata, the rendered episode links and the embedded series root of a product page. */
  rbooks.productInfo = function () {
    const pickText = (selectors) => {
      for (const selector of selectors) {
        try {
          const node = document.querySelector(selector);
          const value = clean(node && (node.innerText || node.textContent));
          if (value) return value;
        } catch (e) { /* bad selector */ }
      }
      return '';
    };
    // Metadata logic from the rbooks-dl extension (scrapeNovelMeta).
    const jsonData = { author: '', description: '', image: '', title: '', publisher: '' };
    const descriptions = [];
    const coverPattern = new RegExp(`${reEscape(SITE.rbooksCdn)}\\/cover`);
    for (const script of document.querySelectorAll('script[type="application/json"]')) {
      let rootValue;
      try { rootValue = JSON.parse(script.textContent || ''); } catch (e) { continue; }
      const seen = new Set();
      (function walk(value) {
        if (!value || typeof value !== 'object' || seen.has(value)) return;
        seen.add(value);
        if (Array.isArray(value)) { for (const item of value) walk(item); return; }
        if (value.author != null && !jsonData.author) {
          if (typeof value.author === 'string' && value.author.trim()) jsonData.author = value.author.trim();
          else if (typeof value.author === 'object') {
            const names = Object.values(value.author).filter((item) => typeof item === 'string' && item.trim());
            if (names.length) jsonData.author = names.join(', ');
          }
        }
        if (typeof value.description === 'string' && value.description.trim()) descriptions.push(value.description);
        if (typeof value.image === 'string' && coverPattern.test(value.image) && !jsonData.image) jsonData.image = value.image;
        if (typeof value.title === 'string' && value.title.trim() && value.title.length < 200 && !jsonData.title) jsonData.title = value.title;
        if (typeof value.publisher === 'string' && value.publisher.trim() && !jsonData.publisher) jsonData.publisher = value.publisher;
        for (const key in value) { try { walk(value[key]); } catch (e) { /* skip */ } }
      }(rootValue));
    }
    const promo = /리디\s*가입|포인트|가입하면|이벤트|쿠폰|할인받|무료.?체험/;
    let bestDescription = '';
    let bestScore = -Infinity;
    for (const description of descriptions) {
      if (promo.test(description)) continue;
      let score = description.length;
      if (/책\s*소개|작가\s*의\s*말|줄거리/.test(description)) score += 3000;
      if (/&lt;|<\/?[a-z][^>]*>/i.test(description)) score += 5000;
      if (/[.。…]{2,}\s*$/.test(description)) score -= 2000;
      if (score > bestScore) { bestScore = score; bestDescription = description; }
    }
    const synopsisFromScripts = () => {
      let best = null;
      const expression = /"description"\s*:\s*"((?:[^"\\]|\\.)*)"/g;
      for (const script of document.querySelectorAll('script')) {
        const source = script.textContent || '';
        if (!source.includes('description')) continue;
        expression.lastIndex = 0;
        let match;
        while ((match = expression.exec(source))) {
          let value;
          try { value = JSON.parse('"' + match[1] + '"'); } catch (e) { continue; }
          if (!value || !value.trim() || promo.test(value)) continue;
          const marker = /책\s*소개|작품\s*소개|작가\s*의\s*말|줄거리|<b>|&lt;/i.test(value);
          const truncated = /[.。…]{2,}\s*$/.test(value);
          if (!marker && value.length < 60) continue;
          const score = value.length + (marker ? 100000 : 0) + (truncated ? 0 : 5000);
          if (!best || score > best.score) best = { value, score };
        }
      }
      return best ? best.value : '';
    };
    const synopsisByHeading = () => {
      for (const heading of document.querySelectorAll('h1,h2,h3,h4')) {
        if (!/^(작품\s*소개|책\s*소개|줄거리|소개)$/.test(clean(heading.textContent))) continue;
        let node = heading.nextElementSibling;
        for (let index = 0; node && index < 3; index += 1, node = node.nextElementSibling) {
          if (node.tagName === 'BUTTON') continue;
          const value = clean(node.innerText || node.textContent);
          if (value.length > 4) return value;
        }
      }
      return '';
    };
    let synopsis = synopsisFromScripts() || synopsisByHeading() || bestDescription
      || metaContent('og:description', 'twitter:description', 'description');
    if (synopsis) {
      synopsis = synopsis
        .replace(/<br\s*\/?>/gi, '\n').replace(/<\/(p|div|li)>/gi, '\n').replace(/<[^>]+>/g, '')
        .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&quot;/g, '"')
        .replace(/&#0?39;/g, "'").replace(/&nbsp;/g, ' ')
        .replace(/^\s*[<\[]\s*(책\s*소개|작품\s*소개)\s*[>\]]\s*/i, '')
        .replace(/\s*(더보기|접기)\s*$/, '')
        .replace(/\n{3,}/g, '\n\n').trim();
    }
    const coverNode = document.querySelector(
      `img[src*="${SITE.rbooksCdn}/cover"], img[srcset*="${SITE.rbooksCdn}/cover"], `
      + '[class*="cover"] img, [class*="Cover"] img, .thumbnail img',
    );
    const cover = absolute(
      metaContent('og:image', 'twitter:image', 'twitter:image:src') || jsonData.image
      || (coverNode && (coverNode.currentSrc || coverNode.src))
      || ((coverNode && coverNode.getAttribute('srcset')) || '').split(',').pop().trim().split(' ')[0]
      || (coverNode && coverNode.getAttribute('data-src')) || '',
    );
    const title = metaContent('og:title') || jsonData.title || pickText(['h1', '[class*="title"]', '.book_title']) || '';
    const author = jsonData.author || pickText(['a[href^="/author/"]', 'a[href*="/author/"]', '[class*="author"] a', '[class*="Author"]'])
      || metaContent('og:novel:author', 'book:author', 'author');

    // The cheaply available DOM titles, before the episode API runs.
    const titleById = {};
    const collectLinks = () => Array.from(document.querySelectorAll('.serial_book_direct_view_button'))
      .map((node) => node.getAttribute('href')).filter(Boolean);
    for (const button of document.querySelectorAll('.serial_book_direct_view_button')) {
      const id = bookIdOf(button.getAttribute('href') || '');
      if (!id) continue;
      let node = button;
      let episodeTitle = '';
      for (let depth = 0; depth < 5 && node; depth += 1) {
        node = node.parentElement;
        if (!node) break;
        const candidate = node.querySelector('[class*="title"], [class*="Title"], h3, h4, .book_title, strong');
        if (candidate && clean(candidate.textContent)) { episodeTitle = clean(candidate.textContent); break; }
      }
      if (episodeTitle && episodeTitle.length <= 200) titleById[id] = episodeTitle;
    }
    let links = collectLinks();
    const diagnostics = [`Rendered episode links: ${links.length}`];
    let seriesId = '';
    for (const script of document.querySelectorAll('script:not([src])')) {
      const match = (script.textContent || '').match(/"seriesId"\s*:\s*"(\d+)"/);
      if (match) { seriesId = match[1]; break; }
    }
    if (!seriesId) seriesId = bookIdOf(links[0]);
    if (!seriesId) seriesId = bookIdOf(location.pathname);
    diagnostics.push(`Series ID: ${seriesId || 'not found'}`);
    const finalLinks = collectLinks();
    if (finalLinks.length > links.length) links = finalLinks;
    const seenLinks = new Set();
    links = links.map((value) => absolute(value)).filter((href) => {
      if (!href || seenLinks.has(href)) return false;
      seenLinks.add(href);
      return /\/books\/\d+\/view(?:[/?#]|$)/.test(href);
    });
    const seenTags = new Set();
    const tags = Array.from(document.querySelectorAll('[class*="keyword"] a, [class*="tag"] a'))
      .map((node) => clean(node.textContent).replace(/^#/, '')).filter((tag) => tag && !seenTags.has(tag) && seenTags.add(tag));
    return {
      title, author, synopsis, cover, publisher: jsonData.publisher || '', tags,
      links, titleById, seriesId, diagnostics,
    };
  };

  // --- episode catalog -------------------------------------------------------------------------

  const apiTitle = (payload) => {
    const title = payload && payload.title;
    if (typeof title === 'string') return title.trim();
    if (title && typeof title === 'object') {
      for (const key of ['main', 'full', 'name', 'short']) {
        const value = title[key];
        if (typeof value === 'string' && value.trim()) return value.trim();
      }
    }
    return '';
  };
  const apiNextId = (payload) => {
    const property = (((payload || {}).series || {}).property) || {};
    const next = property.next_books;
    if (!next || typeof next !== 'object' || Array.isArray(next)) return '';
    for (const key of Object.keys(next)) {
      const value = String(key || '').trim();
      if (/^\d+$/.test(value)) return value;
    }
    return '';
  };
  /** A serial catalog is inferred only when the rendered run and the root API prove the ids are contiguous. */
  const contiguousIds = (seriesId, total, renderedLinks, firstNext) => {
    seriesId = String(seriesId || '');
    if (!/^\d+$/.test(seriesId) || total < 2 || total > 10000) return [];
    const rendered = [];
    for (const link of renderedLinks || []) {
      const id = bookIdOf(link);
      if (id && !rendered.includes(id)) rendered.push(id);
    }
    if (rendered.length < 2 || !rendered.includes(seriesId)) return [];
    const start = parseInt(seriesId, 10);
    const numbers = rendered.map(Number).sort((a, b) => a - b);
    if (numbers.some((value, i) => value !== start + i)) return [];
    const width = seriesId.length;
    if (firstNext !== String(start + 1).padStart(width, '0')) return [];
    return Array.from({ length: total }, (_, offset) => String(start + offset).padStart(width, '0'));
  };
  /** Expands a first title such as "Book 1화" over the generated ids. */
  const generatedTitles = (firstTitle, ids) => {
    firstTitle = String(firstTitle || '').trim();
    if (!firstTitle || !ids.length) return {};
    const match = /^(.*?)(\d+)(\s*(?:화|회|권|episode|chapter|volume))(.*)$/i.exec(firstTitle);
    if (!match) return { [ids[0]]: firstTitle };
    const number = parseInt(match[2], 10);
    const out = {};
    ids.forEach((id, index) => { out[id] = `${match[1]}${number + index}${match[3]}${match[4]}`; });
    return out;
  };
  const viewLink = (id) => `${rbooksOrigin}/books/${id}/view`;

  /**
   * Walks the series on the book API's origin. Resumable: call with {seriesId, renderedLinks, budgetMs}
   * first, then with {state: <the state it returned>, budgetMs} until done. {giveUp: true} ends it with
   * what was found. Result: {done, stalled, state, links, titleById, diagnostics, isSerial, error}.
   * `stalled` means the API stopped answering at state.nextId; native code may reload the API page and continue.
   */
  rbooks.discover = async function (input) {
    const started = Date.now();
    const budget = input.budgetMs > 0 ? input.budgetMs : 20000;
    const fetchBook = async (id, retries) => {
      for (let attempt = 1; attempt <= Math.max(1, retries); attempt += 1) {
        let delay = attempt;
        try {
          const response = await fetch(`/books/${id}`, { credentials: 'include', headers: { Accept: 'application/json' } });
          if (response.status >= 200 && response.status < 300) return JSON.parse(await response.text());
          if (response.status === 429) delay = 2 * attempt;
        } catch (e) { /* try again */ }
        if (attempt < retries) await sleep(delay * 1000);
      }
      return null;
    };
    const finish = (st, stalled) => {
      const done = st.phase === 'done';
      return {
        done, stalled: !!stalled, state: st, isSerial: st.isSerial, error: st.error, diagnostics: st.diagnostics,
        titleById: st.titleById, links: done ? st.ids.map(viewLink) : [],
      };
    };
    let st = input.state;
    if (!st) {
      st = {
        phase: 'start', seriesId: String(input.seriesId), ids: [String(input.seriesId)], titleById: {}, diagnostics: [],
        isSerial: null, total: 0, nextId: '', firstTitle: '', error: '',
      };
      const first = await fetchBook(st.seriesId, 3);
      if (!first) {
        st.error = 'book API did not return JSON';
        st.phase = 'done';
        st.ids = [];
        return finish(st);
      }
      const property = ((first.series || {}).property) || {};
      st.total = parseInt(property.total_book_count || property.opened_book_count || 0, 10) || 0;
      st.isSerial = !!property.is_serial;
      st.diagnostics.push(`Series episode count: ${st.total || 'unknown'}`);
      st.firstTitle = apiTitle(first);
      if (st.firstTitle) st.titleById[st.seriesId] = st.firstTitle;
      st.nextId = apiNextId(first);
      if (!st.isSerial) {
        // Volume ebooks have a finite next_books chain; each volume stays one reader entry.
        st.phase = st.total < 2 ? 'done' : 'volumes';
        if (st.total < 2) st.ids = [];
      } else {
        const fast = contiguousIds(st.seriesId, st.total, input.renderedLinks, st.nextId);
        if (fast.length) {
          const lastId = fast[fast.length - 1];
          const last = await fetchBook(lastId, 2);
          const lastProperty = ((last || {}).series || {}).property || {};
          const lastTotal = parseInt(lastProperty.total_book_count || lastProperty.opened_book_count || 0, 10) || 0;
          const valid = !!last && !!lastProperty.is_serial && (!lastTotal || lastTotal === st.total) && !apiNextId(last);
          if (valid) {
            st.ids = fast;
            Object.assign(st.titleById, generatedTitles(st.firstTitle, fast));
            const lastTitle = apiTitle(last);
            if (lastTitle) st.titleById[lastId] = lastTitle;
            st.diagnostics.push(`Fast episode catalog: ${fast.length} contiguous links validated.`);
            st.phase = 'done';
            return finish(st);
          }
        }
        st.phase = 'chain';
      }
    }
    if (input.giveUp) {
      st.phase = 'done';
      if (st.ids.length < 2 && !st.isSerial) st.ids = [];
      return finish(st);
    }
    // A serial's episodes a window of IDs per request (book API's /books?b_ids=): each window's books of
    // this series are chained by their own next_books (the store's order, nothing guessed); a jump past
    // the window starts the next one there. 136 episodes: 3.5 s instead of 55. A failed batch request
    // leaves the rest to the one-at-a-time walk below.
    if (st.phase === 'chain' && !st.batchFailed) {
      while (st.nextId && !st.ids.includes(st.nextId) && st.ids.length < 10000 && Date.now() - started < budget) {
        const next = st.nextId;
        if (!/^\d+$/.test(next)) { st.batchFailed = true; break; }
        const window = [];
        for (let offset = 0; offset < 100; offset += 1) window.push(String(Number(next) + offset).padStart(next.length, '0'));
        let books = null;
        try {
          const response = await fetch(`/books?b_ids=${window.join(',')}`, { credentials: 'include', headers: { Accept: 'application/json' } });
          if (response.status >= 200 && response.status < 300) books = JSON.parse(await response.text());
        } catch (e) { books = null; }
        if (!Array.isArray(books)) { st.batchFailed = true; break; }
        const series = {};
        for (const book of books) {
          if (book && book.id && String(((book.series || {}).id) || '') === st.seriesId) series[String(book.id)] = book;
        }
        if (!series[next]) {
          const single = await fetchBook(next, 2);
          if (!single) return finish(st, true);
          series[next] = single;
        }
        while (st.nextId && series[st.nextId] && !st.ids.includes(st.nextId)) {
          const book = series[st.nextId];
          st.ids.push(st.nextId);
          const title = apiTitle(book);
          if (title) st.titleById[st.nextId] = title;
          st.nextId = apiNextId(book);
        }
        st.batched = true;
      }
    }
    const limit = st.phase === 'volumes' ? Math.min(st.total, 100) : Math.min(Math.max(st.total || 10000, 1), 10000);
    while (st.phase !== 'done' && st.ids.length < limit && Date.now() - started < budget) {
      const next = st.nextId;
      if (!next || st.ids.includes(next)) break;
      const book = await fetchBook(next, 3);
      if (!book) return finish(st, true);
      st.ids.push(next);
      const title = apiTitle(book);
      if (title) st.titleById[next] = title;
      st.nextId = apiNextId(book);
      if (st.ids.length % 20 === 0) await sleep(300);
    }
    if (st.phase !== 'done' && (!st.nextId || st.ids.includes(st.nextId) || st.ids.length >= limit)) {
      st.phase = 'done';
      st.diagnostics.push(st.isSerial ? `${st.batched ? 'Batched' : 'Chained'} episode links: ${st.ids.length}` : `Linked ebook volumes: ${st.ids.length}`);
      if (!st.isSerial && st.ids.length < 2) st.ids = [];
    }
    return finish(st);
  };

  /** API page: is the body the JSON document yet (the bot check passed)? */
  rbooks.apiState = function () {
    const raw = ((document.body && document.body.innerText) || '').trim();
    return {
      json: raw.startsWith('{'), title: document.title || '', url: location.href,
      challenge: /just a moment/i.test(document.title || '') || /cloudflare/i.test(raw.slice(0, 2000)),
      text: raw.slice(0, 160),
    };
  };

  // --- viewer ----------------------------------------------------------------------------------

  /** Viewer readiness: the text is drawn and long enough to be a chapter. */
  rbooks.contentState = function () {
    const content = firstViewer();
    const length = ((content && content.innerText) || '').trim().length;
    const notice = (bodyText()).slice(0, 2000);
    return {
      complete: document.readyState === 'complete', found: !!content,
      ready: document.readyState === 'complete' && length > 500, length,
      refused: !content && /작품을 열 수 없습니다|웹 뷰어에서 지원하지 않는/.test(notice),
      url: location.href,
    };
  };

  /** Classifies purchase / login redirects without retrying them. */
  rbooks.accessWall = function () {
    if (firstViewer()) return { wall: false, offViewer: false, hits: 0, url: location.href };
    const offViewer = !/\/books\/\d+\/view(?:\/|$|\?|#)/.test(location.href);
    const body = bodyText().slice(0, 6000);
    const hits = ['구매', '대여', '로그인', '결제', '소장', '미리보기'].filter((word) => body.includes(word)).length;
    const refused = /작품을 열 수 없습니다|웹 뷰어에서 지원하지 않는/.test(body);
    return { wall: offViewer || hits >= 2 || refused, offViewer, refused, hits, url: location.href };
  };

  /**
   * Extracts the rendered viewer. options: {fallbackTitle, chapterIndex}. Images are renamed to
   * `images/<chapterIndex 4 digits>_<n 2 digits>.<ext>` in the returned XHTML, with {url, name} listed.
   * Result: {title, content (XHTML fragment), contentText, images[]} or null when there is no viewer.
   */
  rbooks.extract = function (options) {
    const source = firstViewer();
    if (!source) return null;
    // Work on a clone: the page is reused for the next chapter. The viewer's top-bar headings show the
    // series title only; the document title names the episode and is server-rendered, so it is ready
    // before the content.
    const pageTitle = (document.title || '').replace(/\s*[-|]\s*리디(?:북스)?\s*$/, '').trim();
    return cleanViewer(source.cloneNode(true), options, pageTitle);
  };

  /**
   * One episode through the web viewer's own API (POST /api/web-viewer/generate, then its spine files),
   * from a page on the store's address: no viewer page to draw (about 1 s an episode instead of 4).
   * {bookId, fallbackTitle, chapterIndex} -> {status: 'ok', extract} (the shape rbooks.extract returns),
   * {status: 'locked'} (the store says this account may not read it), or {status: 'fallback'} for anything
   * else (not a web-viewer book, signed out, a failed request): the native side then loads the viewer.
   */
  rbooks.apiChapter = async function (options) {
    const host = location.hostname;
    if (host !== SITE.rbooksDomain && host !== `www.${SITE.rbooksDomain}`) return { status: 'fallback' };
    let response;
    let body = null;
    try {
      response = await fetch('/api/web-viewer/generate', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ book_id: String((options && options.bookId) || '') }),
      });
      body = await response.json().catch(() => null);
    } catch (e) {
      return { status: 'fallback' };
    }
    if (response.status === 403 && body && body.error && body.error.code === 'INVALID_ACCESS') return { status: 'locked' };
    const data = (body && body.success && body.data) || {};
    // A webtoon or comic episode: its page images, in order.
    if (data.type === 'comic' && Array.isArray(data.pages)) {
      const pages = document.createElement('div');
      for (const item of data.pages) {
        if (!item || !item.src) continue;
        const paragraph = document.createElement('p');
        const image = document.createElement('img');
        image.setAttribute('src', item.src);
        paragraph.appendChild(image);
        pages.appendChild(paragraph);
      }
      const extract = cleanViewer(pages, options, '');
      return extract.images.length ? { status: 'ok', extract } : { status: 'fallback' };
    }
    const spines = data.spines || [];
    if (!spines.length) return { status: 'fallback' };
    const parts = await Promise.all(spines.map((url) => fetch(url).then((r) => r.json()).then((j) => (j && j.value) || null)
      .catch(() => null)));
    if (parts.some((part) => part === null)) return { status: 'fallback' };
    const holder = document.createElement('div');
    for (const part of parts) {
      const parsed = new DOMParser().parseFromString(part, 'text/html');
      for (const child of [...parsed.body.childNodes]) holder.appendChild(document.importNode(child, true));
    }
    const extract = cleanViewer(holder, options, '');
    if (!extract.contentText && !extract.images.length) return { status: 'fallback' };
    // The catalog's episode name: the file's own heading is often only "#2".
    extract.title = ((options && options.fallbackTitle) || '').trim() || extract.title;
    return { status: 'ok', extract };
  };

  /** A viewer's (or the API's) content as {title, content, contentText, images}: the markup cleaned for the EPUB. */
  const cleanViewer = function (content, options, pageTitle) {
    const fallbackTitle = (options && options.fallbackTitle) || '';
    const chapterIndex = String((options && options.chapterIndex) || 0).padStart(4, '0');
    let title = (pageTitle && pageTitle.length < 200 ? pageTitle : '') || fallbackTitle;
    content.querySelectorAll('.content_footer').forEach((node) => node.remove());
    const chapterHeader = content.querySelector('.chapter-header');
    if (chapterHeader) {
      const heading = chapterHeader.querySelector('h1,h2,h3,h4');
      const value = ((heading && heading.textContent) || '').trim();
      if (value && value.length < 200) title = value;
      chapterHeader.remove();
    } else {
      const heading = content.querySelector('h1,h2,h3');
      const value = ((heading && heading.textContent) || '').trim();
      if (value && value.length < 200) {
        title = value;
        heading.remove();
      } else {
        const firstParagraph = content.querySelector('p');
        const bold = firstParagraph && firstParagraph.querySelector('strong,b');
        const paragraphText = ((firstParagraph && firstParagraph.textContent) || '').trim();
        const boldText = ((bold && bold.textContent) || '').trim();
        if (bold && paragraphText === boldText && boldText && boldText.length < 200) {
          title = boldText;
          firstParagraph.remove();
        }
      }
    }
    content.querySelectorAll('script,style,iframe,noscript').forEach((node) => node.remove());
    content.querySelectorAll('pre').forEach((node) => { if (!(node.textContent || '').trim()) node.remove(); });
    content.querySelectorAll('hr').forEach((node) => node.remove());
    content.querySelectorAll(`div.${SITE.rbooksBorderClass}`).forEach((node) => {
      const blockquote = document.createElement('blockquote');
      while (node.firstChild) blockquote.appendChild(node.firstChild);
      node.parentNode.replaceChild(blockquote, node);
    });
    content.querySelectorAll(`.${SITE.rbooksCenterClass}`).forEach((node) => {
      const paragraph = document.createElement('p');
      paragraph.setAttribute('align', 'center');
      while (node.firstChild) paragraph.appendChild(node.firstChild);
      node.parentNode.replaceChild(paragraph, node);
    });
    for (const selector of ['article', '.aegix', '.chapter-content']) {
      content.querySelectorAll(selector).forEach((node) => {
        while (node.firstChild) node.parentNode.insertBefore(node.firstChild, node);
        node.remove();
      });
    }
    // Images: absolute URL, a stable local name, and the markup pointing at that name.
    const images = [];
    const names = new Map();
    for (const image of content.querySelectorAll('img')) {
      const original = image.getAttribute('src') || image.getAttribute('data-src') || '';
      if (!original) { image.remove(); continue; }
      const url = absolute(original);
      if (!/^https?:|^data:/i.test(url)) { image.remove(); continue; }
      let name = names.get(url);
      if (!name) {
        let extension = 'jpg';
        if (/^data:image\/(\w+)/i.test(url)) extension = RegExp.$1.toLowerCase().replace('jpeg', 'jpg');
        else {
          const last = url.split(/[?#]/)[0].split('/').pop() || '';
          const dot = last.lastIndexOf('.');
          const candidate = dot >= 0 ? last.slice(dot + 1).toLowerCase() : '';
          if (candidate && candidate.length <= 5 && /^[a-z0-9]+$/.test(candidate)) extension = candidate;
        }
        name = `images/${chapterIndex}_${String(images.length + 1).padStart(2, '0')}.${extension}`;
        names.set(url, name);
        images.push({ url, name });
      }
      image.setAttribute('src', name);
      image.removeAttribute('srcset');
    }
    content.querySelectorAll('*').forEach((node) => {
      node.removeAttribute('id');
      node.removeAttribute('class');
      node.removeAttribute('style');
      for (const attribute of [...node.attributes]) {
        if (attribute.name.startsWith('data-') || attribute.name.startsWith('on')) node.removeAttribute(attribute.name);
      }
    });
    const walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT);
    const textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);
    for (const node of textNodes) {
      node.nodeValue = stripInvisible(node.nodeValue).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '');
      if (!node.nodeValue) node.remove();
    }
    content.querySelectorAll('div').forEach((node) => {
      if (!(node.textContent || '').trim() && !node.querySelector('img')) node.remove();
    });
    let blocks = [...content.querySelectorAll('p,h1,h2,h3,h4,h5,h6,li,blockquote')]
      .map((node) => stripInvisible(node.textContent).replace(/[ \t\f\v]+/g, ' ').trim()).filter(Boolean);
    if (!blocks.length) {
      blocks = stripInvisible(content.textContent).split(/\n+/).map((line) => line.replace(/\s+/g, ' ').trim()).filter(Boolean);
    }
    // XHTML for the EPUB: serialise each child as XML, then drop the namespace the serialiser adds.
    const serializer = new XMLSerializer();
    const xhtml = Array.from(content.childNodes).map((node) => serializer.serializeToString(node))
      .join('').replace(/ xmlns="http:\/\/www\.w3\.org\/1999\/xhtml"/g, '').replace(/<!--[\s\S]*?-->/g, '')
      .replace(/\s+/g, ' ').replace(/>\s+</g, '>\n<');
    return {
      title: stripInvisible(title).trim() || fallbackTitle,
      content: stripInvisible(xhtml).trim(),
      contentText: blocks.join('\n'),
      images,
    };
  };

  /**
   * Which of `ids` the signed-in account owns, asked the way the library page does. Run it on the
   * library origin page (its CORS rules allow it). Result: {owned: [id...]} or {owned: null}.
   */
  rbooks.ownedIds = async function (ids) {
    try {
      const response = await fetch(`https://library-api.${SITE.rbooksDomain}/items`, {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ b_ids: (ids || []).slice(0, 500) }),
      });
      if (!response.ok) return { owned: null, status: response.status };
      const data = await response.json();
      return { owned: (data.items || []).map((item) => String(item.b_id)) };
    } catch (e) {
      return { owned: null, error: String((e && e.message) || e) };
    }
  };

  // ---------------------------------------------------------------------------------------------
  // Install
  // ---------------------------------------------------------------------------------------------

  // ---------------------------------------------------------------------------------------------
  // mpia: the mpia site's webnovels. The chapter list comes from the site's JSON API (same-origin fetch).
  // Chapter text is drawn by the site's own wasm reader onto a canvas; the observer below (installed at
  // document start) records the reader's own fillText output and the paragraphs are rebuilt from it. A
  // server-rendered page falls back to its DOM. Ported from external_scraper.py (_mpia_*). No purchases,
  // access checks or encryption are performed; only the authorized reader's own output is read.
  // ---------------------------------------------------------------------------------------------

  const mpia = hard.mpia;
  const mpiaOrigin = () => `https://www.${SITE.mpiaDomain}`;
  const mpiaMobileHost = () => location.hostname === 'm.' + SITE.mpiaDomain;
  const mpiaMobileContent = () => document.querySelector('.content > article');
  const mpiaLocked = (error) => !!error && (error.status === 401
    || /로그인|구매|대여|결제|권한|성인.{0,3}인증|login|\bsign in\b|\bpurchase\b|\brent\b/i.test(error.message || ''));
  const mpiaViewerUrl = (novelId, chapterId) =>
    `${mpiaOrigin()}/novel/viewer/${encodeURIComponent(novelId)}/${encodeURIComponent(chapterId)}`;

  const mpiaOnHost = () => {
    const host = (location.hostname || '').toLowerCase();
    return host === SITE.mpiaDomain || host.endsWith('.' + SITE.mpiaDomain);
  };

  const mpiaApiGet = async (path) => {
    try {
      const response = await fetch(path, { credentials: 'include', headers: { Accept: 'application/json' } });
      let body = null;
      try { body = await response.json(); } catch (e) { body = null; }
      const code = body && body.code;
      const result = body && body.result;
      const ok = response.status === 200 && code === 'M000_00000' && result && typeof result === 'object';
      return { ok: !!ok, status: response.status, code: code || '', result: ok ? result : null };
    } catch (e) {
      return { ok: false, status: 0, code: '', result: null, error: String((e && e.message) || e) };
    }
  };

  const mpiaImageName = (url, chapterIndex, number) => {
    const prefix = String(chapterIndex || 0).padStart(4, '0');
    let extension = 'jpg';
    const data = /^data:image\/([a-z0-9]+)/i.exec(url);
    if (data) {
      extension = data[1].toLowerCase().replace('jpeg', 'jpg');
    } else {
      const last = (url.split(/[?#]/)[0].split('/').pop() || '');
      const dot = last.lastIndexOf('.');
      const candidate = dot >= 0 ? last.slice(dot + 1).toLowerCase() : '';
      if (candidate && candidate.length <= 5 && /^[a-z0-9]+$/.test(candidate)) extension = candidate;
    }
    return `images/${prefix}_${String(number).padStart(2, '0')}.${extension}`;
  };

  // Observe the authorized reader's own requests and canvas output. Installed at document start so the
  // fetch / XHR / fillText hooks are in place before the site's wasm reader runs.
  mpia.installReaderHook = function () {
    if (!mpiaOnHost()) return false;
    if (window.__ndMpiaReader) return true;
    const state = window.__ndMpiaReader = { entry: null, entryId: '', contentOK: false, error: null, mobileError: null };
    const path = (value) => {
      try { return new URL(value, location.href).pathname; } catch (e) { return ''; }
    };
    const infoPattern = /\/novel-detail\/\d+\/entries\/(\d+)\/info$/;
    const contentPattern = /\/novel-detail\/\d+\/entries\/(\d+)\/content$/;
    const mobileEntry = (url) => mpiaMobileHost()
      && path(url) === location.pathname.replace(/^\/novel\/viewer\/(\d+)\/(\d+)\/?$/,
        '/api/v1/mobile/novel-detail/$1/entries/$2');
    const recordMobileError = (status, data) => {
      if (status === 200 && data && data.code === 'M000_00000') state.mobileError = null;
      else state.mobileError = {status, code: data && data.code, message: data && data.message};
    };
    const recordInfo = (url, data) => {
      const match = path(url).match(infoPattern);
      const entry = data && data.result && data.result.entry;
      if (match && entry && String(entry.id) === match[1]) {
        state.entry = { id: entry.id, title: entry.title, attachments: entry.attachments || [] };
      }
    };
    const nativeFetch = window.fetch;
    window.fetch = async function (input, options) {
      const url = typeof input === 'string' ? input : (input && input.url);
      const match = path(url).match(contentPattern);
      if (match) {
        state.entryId = match[1];
        state.contentOK = false;
        state.error = null;
        document.documentElement.removeAttribute('data-nd-mpia-ready');
        document.documentElement.removeAttribute('data-nd-mpia-denied');
      }
      const response = await nativeFetch.apply(this, arguments);
      const json = /application\/json/i.test(response.headers.get('content-type') || '');
      if (match) {
        state.contentOK = response.ok && !json;
        if (!state.contentOK) {
          const data = json ? await response.clone().json().catch(() => null) : null;
          state.error = { status: response.status, code: data && data.code, message: data && data.message };
          document.documentElement.setAttribute('data-nd-mpia-denied', '1');
        }
      } else if (mobileEntry(url)) {
        const data = json ? await response.clone().json().catch(() => null) : null;
        recordMobileError(response.status, data);
      } else if (path(url).match(infoPattern) && json) {
        response.clone().json().then((data) => recordInfo(url, data)).catch(() => {});
      }
      return response;
    };
    const nativeOpen = XMLHttpRequest.prototype.open;
    const nativeSend = XMLHttpRequest.prototype.send;
    const requestUrls = new WeakMap();
    XMLHttpRequest.prototype.open = function (method, url) {
      requestUrls.set(this, url);
      return nativeOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function () {
      const url = requestUrls.get(this);
      if (path(url).match(infoPattern) || mobileEntry(url)) {
        this.addEventListener('load', () => {
          let data = null;
          try {
            data = this.responseType === 'json' ? this.response : JSON.parse(this.responseText);
          } catch (e) { /* not the JSON we wanted */ }
          if (mobileEntry(url)) recordMobileError(this.status, data);
          else recordInfo(url, data);
        }, { once: true });
      }
      return nativeSend.apply(this, arguments);
    };
    const nativeFillText = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (value) {
      const result = nativeFillText.apply(this, arguments);
      if (state.contentOK && String(value == null ? '' : value).trim()) {
        document.documentElement.setAttribute('data-nd-mpia-ready', '1');
      }
      return result;
    };
    return true;
  };

  /** Book metadata from the novel-detail API, shaped for the native side. */
  mpia.detail = async function (novelId) {
    const reply = await mpiaApiGet('/api/v1/pc/novel-detail/' + encodeURIComponent(novelId));
    if (!reply.ok) return { ok: false, status: reply.status, error: reply.error || ('detail ' + (reply.code || reply.status)) };
    const result = reply.result || {};
    const info = result.novelInfo || {};
    const tags = Array.isArray(info.tags) ? info.tags.map((t) => clean(t && t.title)).filter(Boolean) : [];
    return {
      ok: true,
      login: result.login === true,
      id: String(info.id == null ? novelId : info.id),
      title: clean(info.title),
      author: clean(info.authorName),
      synopsis: String(info.introduction == null ? '' : info.introduction),
      cover: absolute(info.coverUrl),
      tags,
      finished: info.finish === true,
      total: Number(info.chapterCount) || 0,
    };
  };

  /** One page (size 30) of the chapter list, oldest first, shaped for the native side. */
  mpia.chapters = async function (novelId, page) {
    const path = '/api/v1/pc/novel-detail/' + encodeURIComponent(novelId)
      + '/chapters?order=ENTRY_FIRST&page=' + encodeURIComponent(page) + '&size=30';
    const reply = await mpiaApiGet(path);
    if (!reply.ok) return { ok: false, status: reply.status, error: reply.error || ('chapters ' + (reply.code || reply.status)) };
    const result = reply.result || {};
    const rows = Array.isArray(result.list) ? result.list : [];
    const list = rows.map((row) => {
      const id = String(row.id == null ? '' : row.id);
      const free = row.free === true;
      const purchased = row.purchased === true;
      const rented = Number(row.remainRentSec) > 0;
      return {
        id,
        order: Number(row.num) || 0,
        title: clean(row.title),
        url: mpiaViewerUrl(novelId, id),
        isPaid: !free,
        accessible: !!(free || purchased || rented),
        date: clean(row.createdAt),
      };
    }).filter((row) => row.id);
    return { ok: true, total: Number(result.total) || 0, list };
  };

  /** Viewer readiness, mirroring the desktop reader selector. */
  mpia.readerState = function () {
    const el = document.documentElement;
    const state = window.__ndMpiaReader;
    const failure = mpiaMobileHost() ? state?.mobileError : state?.error;
    const denied = mpiaLocked(failure);
    const error = failure && !denied
      ? `The mpia reader returned HTTP ${failure.status}${failure.code ? ' (' + failure.code + ')' : ''}: ${failure.message || 'chapter unavailable'}`
      : '';
    const drawn = el.getAttribute('data-nd-mpia-ready') === '1';
    const content = mpiaMobileHost() ? mpiaMobileContent() : document.querySelector('#ENTRY-CONTENT .tcontent');
    const ready = drawn || !!(content && (content.textContent.trim() || content.querySelector('img')));
    return { ready, denied, found: ready, error };
  };

  // Rebuild the chapter from the reader's own canvas output. Reuses the module the authorized reader
  // already loaded; its render function draws plaintext, which the fillText hook records. No session
  // keys or encrypted response bodies are read.
  const mpiaCanvasExtract = async (chapterIndex) => {
    const state = window.__ndMpiaReader;
    if (!state || !state.contentOK) {
      const error = state && state.error;
      const locked = mpiaLocked(error);
      return { locked, error: error ? 'Reader denied content' : 'Reader content is not ready',
        chapterName: '', contentText: '', contentHtml: '', images: [] };
    }
    const entryMatch = location.pathname.match(/\/novel\/viewer\/\d+\/(\d+)/);
    const entryId = entryMatch ? entryMatch[1] : '';
    if (!entryId || state.entryId !== entryId) {
      return { error: 'Reader chapter changed', chapterName: '', contentText: '', contentHtml: '', images: [] };
    }
    const moduleUrls = performance.getEntriesByType('resource')
      .map((item) => item.name)
      .filter((url) => /\/novel_wasm-[^/]+\.js(?:\?|$)/.test(url));
    const moduleUrl = moduleUrls[moduleUrls.length - 1];
    if (!moduleUrl) return { error: 'Reader module was not loaded', chapterName: '', contentText: '', contentHtml: '', images: [] };
    const renderer = await import(moduleUrl);
    if (typeof renderer.render_page !== 'function' || typeof renderer.get_total_pages !== 'function') {
      return { error: 'Reader rendering API changed', chapterName: '', contentText: '', contentHtml: '', images: [] };
    }
    const canvas = document.createElement('canvas');
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(4096 * ratio);
    canvas.height = Math.round(64 * ratio);
    canvas.style.width = '4096px';
    canvas.style.height = '64px';
    const context = canvas.getContext('2d');
    if (!context) return { error: 'Canvas rendering is unavailable', chapterName: '', contentText: '', contentHtml: '', images: [] };
    const fontSize = 17, lineHeight = 1.8, padding = 40, linesPerPage = 80;
    const font = 'Arial, sans-serif';
    let draws = [];
    context.fillText = function (value, x, y) {
      value = String(value == null ? '' : value);
      if (value) draws.push({ text: value, x, y, width: this.measureText(value).width });
    };
    const pages = renderer.get_total_pages(canvas, linesPerPage, fontSize, lineHeight, font, padding, 0, 0);
    if (!Number.isInteger(pages) || pages < 1 || pages > 10000) {
      return { error: 'Reader returned an invalid page count', chapterName: '', contentText: '', contentHtml: '', images: [] };
    }
    const cleanId = (value) => String(value || '').replace(/^(?:@PIC:)+/, '');
    const attachments = new Map(((state.entry && state.entry.attachments) || []).map((item) => [cleanId(item.id), item]));
    const blocks = [], imageIds = new Set();
    for (let page = 0; page < pages; page++) {
      draws = [];
      renderer.render_page(canvas, page, linesPerPage, fontSize, lineHeight, font, '#111111', '#ffffff', padding, 0, 0);
      const rows = [];
      for (const draw of draws.sort((a, b) => a.y - b.y || a.x - b.x)) {
        let row = rows[rows.length - 1];
        if (!row || Math.abs(row.y - draw.y) > 0.5) {
          row = { kind: 'text', text: '', x: draw.x, y: draw.y, end: draw.x };
          rows.push(row);
        }
        if (row.text && draw.x - row.end > 1 && !/\s$/.test(row.text) && !/^\s/.test(draw.text)) row.text += ' ';
        row.text += draw.text;
        row.end = Math.max(row.end, draw.x + draw.width);
      }
      if (attachments.size && typeof renderer.get_page_image_rects === 'function') {
        const rects = JSON.parse(renderer.get_page_image_rects(canvas, page, linesPerPage, fontSize, lineHeight, font, padding, 0, 0));
        for (const rect of rects) {
          const id = cleanId(rect.id), attachment = attachments.get(id);
          if (attachment && attachment.imageUrl && !imageIds.has(id)) {
            rows.push({ kind: 'image', url: attachment.imageUrl, y: rect.y });
            imageIds.add(id);
          }
        }
      }
      rows.sort((a, b) => a.y - b.y);
      for (const row of rows) {
        if (row.kind === 'image') {
          blocks.push({ kind: 'image', url: row.url });
        } else {
          const value = row.text.replace(/\u00a0/g, ' ').trim();
          if (!value) continue;
          const previous = blocks[blocks.length - 1];
          if (previous && previous.kind === 'text' && row.x < padding + fontSize / 2
            && (previous.page !== page || row.y - previous.y <= fontSize * lineHeight * 1.5)) {
            previous.text += ' ' + value;
            previous.y = row.y;
            previous.page = page;
          } else {
            blocks.push({ kind: 'text', text: value, y: row.y, page });
          }
        }
      }
    }
    for (const [id, attachment] of attachments) {
      if (attachment.imageUrl && !imageIds.has(id)) blocks.push({ kind: 'image', url: attachment.imageUrl });
    }
    const images = [], markup = [], texts = [];
    for (const block of blocks) {
      if (block.kind === 'text') {
        texts.push(block.text);
        markup.push(`<p>${escapeHtml(block.text)}</p>`);
      } else {
        let url;
        try { url = new URL(block.url, location.href); } catch (e) { continue; }
        if (!/^https?:$/.test(url.protocol)) continue;
        const name = mpiaImageName(url.href, chapterIndex, images.length + 1);
        images.push({ url: url.href, name });
        markup.push(`<p><img src="${escapeHtml(name)}" /></p>`);
      }
    }
    if (!texts.length && !images.length) {
      return { error: 'Reader returned empty content', chapterName: (state.entry && state.entry.title) || '', contentText: '', contentHtml: '', images: [] };
    }
    return {
      chapterName: (state.entry && state.entry.title) || '',
      contentText: texts.join('\n'),
      contentHtml: `<div class="mpia-content">${markup.join('\n')}</div>`,
      images,
    };
  };

  // A server-rendered chapter page (not the canvas viewer): read its DOM layout directly.
  const mpiaDomExtract = (chapterIndex) => {
    const tidy = (value) => (value || '').replace(/[\u00a0\t ]+/g, ' ').replace(/\n\s+/g, '\n').trim();
    const abs = (value) => { try { return new URL(value || '', location.href).href; } catch (e) { return value || ''; } };
    const mobile = mpiaMobileHost();
    const entry = mobile ? null : document.querySelector('#ENTRY-CONTENT');
    const content = mobile ? mpiaMobileContent() : document.querySelector('#ENTRY-CONTENT .tcontent');
    const heading = tidy(
      (entry && entry.querySelector('.subinfo h3') && entry.querySelector('.subinfo h3').textContent)
      || (entry && entry.querySelector('h3') && entry.querySelector('h3').textContent)
      || (document.querySelector('h4') && document.querySelector('h4').textContent)
      || document.title.replace(/\s*-\s*.*$/, ''),
    );
    if (!content) {
      const body = tidy(document.body && document.body.textContent);
      return {
        locked: /(로그인|구매|대여|결제|성인인증|권한|이용권|보안 점검)/.test(body),
        chapterName: heading, contentText: '', contentHtml: '', images: [],
        error: 'Reader content not found',
      };
    }
    // The mobile article can be one text node. Its computed white-space rule
    // gives the original newlines and spaces their meaning in the viewer.
    const whiteSpace = mobile ? getComputedStyle(content).whiteSpace : '';
    const preserveWhiteSpace = /^(pre|pre-wrap|pre-line|break-spaces)$/.test(whiteSpace);
    const node = content.cloneNode(true);
    node.querySelectorAll('.dummy, script, style, noscript, iframe, button, object, embed, form').forEach((el) => el.remove());
    node.querySelectorAll('*').forEach((el) => {
      for (const {name, value} of [...el.attributes]) {
        if (/^on/i.test(name) || (/^(?:href|src)$/i.test(name) && /^\s*javascript:/i.test(value))) el.removeAttribute(name);
      }
    });
    const images = [];
    node.querySelectorAll('img').forEach((img) => {
      const src = abs(img.getAttribute('src') || '');
      if (!src || /blank\.png/i.test(src)) { img.remove(); return; }
      const name = mpiaImageName(src, chapterIndex, images.length + 1);
      images.push({ url: src, name });
      img.setAttribute('src', name);
    });
    let blocks = Array.from(node.querySelectorAll('p, h1, h2, h3, h4, h5, h6, li, blockquote'))
      .map((el) => tidy(el.textContent)).filter(Boolean);
    if (!blocks.length) blocks = tidy(node.textContent).split('\n').map((line) => tidy(line)).filter(Boolean);
    const contentText = blocks.join('\n');
    const style = preserveWhiteSpace ? ` style="white-space: ${whiteSpace}"` : '';
    const html = preserveWhiteSpace ? node.innerHTML : node.innerHTML.trim();
    return {
      locked: !contentText && !images.length,
      chapterName: mobile ? '' : heading,
      contentText,
      contentHtml: `<div class="mpia-content"${style}>${html}</div>`,
      images,
    };
  };

  /** Extracts the loaded chapter. The viewer draws on a canvas; a server-rendered page uses its DOM. */
  mpia.extract = async function (options) {
    const chapterIndex = (options && options.chapterIndex) || 0;
    if (!mpiaMobileHost() && /^\/novel\/viewer\/\d+\/\d+/.test(location.pathname)) return await mpiaCanvasExtract(chapterIndex);
    return mpiaDomExtract(chapterIndex);
  };

  // ---------------------------------------------------------------------------------------------
  // Tasks: long operations (qdn.extract, qdn.decoder.decodeJson) are started and then polled, so no
  // single native script call has to outlive a platform's script timeout.
  //   tasks.start('qdn.extract', [options])            -> {ticket}
  //   tasks.poll(ticket)  -> {state: 'running'} | {state: 'done', json: '<JSON text of the result>'}
  //                          | {state: 'error', error: '...'}
  // The result comes back as JSON text so native code can pass it on without parsing it.
  // ---------------------------------------------------------------------------------------------

  // ---------------------------------------------------------------------------------------------
  // ntk: page side (book index + chapter list, chapter content via the site's own ad-ack handshake
  // and v3 AES-GCM, and webtoon image pages). The site runs a Cloudflare / anti-bot gate; the native
  // side opens the sign-in browser so the user clears it, then this reads everything in the verified
  // session. Nothing is purchased; the content request reuses the token/nonce/proof the site's own
  // reader sends. Ported from the desktop _ntk_* browser path.
  // ---------------------------------------------------------------------------------------------

  const ntk = hard.ntk;

  // Webtoon episodes from mirror sources have ids such as goodtoon-745176 or kp-5656-6755; pages beside
  // the episodes (reviews, comments) have no digit.
  const ntkEpisodeId = /^(?=\D*\d)[A-Za-z0-9][\w-]*$/;

  const ntkCollectChapters = async (novelId, kind, pageParam) => {
  const basePath = `/${kind}/${novelId}`;
  const chapterPrefix = `${basePath}/`;
  const chapters = new Map();
  const text = (element) => element && element.textContent
    ? element.textContent.trim()
    : '';
  const pause = (milliseconds) => new Promise(
    (resolve) => setTimeout(resolve, milliseconds)
  );

  const collect = (root, baseUrl) => {
    const allLinks = Array.from(root.querySelectorAll('a[href]'));
    const episodeRowLinks = allLinks.filter(
      (link) => link.closest('li[data-ep], li[data-episode-number]')
    );
    const candidateLinks = episodeRowLinks.length ? episodeRowLinks : allLinks;
    for (const link of candidateLinks) {
      let target;
      try {
        const href = link.getAttribute('href') || link.href;
        target = new URL(href, baseUrl);
      } catch (_) {
        continue;
      }
      if (target.origin !== location.origin
          || !target.pathname.startsWith(chapterPrefix)) {
        continue;
      }
      const remainder = target.pathname
        .slice(chapterPrefix.length)
        .replace(/\/$/, '');
      if (!ntkEpisodeId.test(remainder)) continue;
      const row = link.closest(
        'li[data-ep], li[data-episode-number], li, tr, article, div'
      );
      // Webtoon rows put an "UP" badge before the title; read the title itself.
      const titleElement = link.querySelector('.ep-row-v2-title strong')
        || link.querySelector('.ne-title, .ep-title, [class*="title"]');
      const chapterName = text(titleElement) || text(link) || text(row) || '';
      if (/(?:1\s*화부터\s*보기|최신화부터)/.test(chapterName)) continue;
      // Not every NewToki build renders data-ep; fall back to the "301화"
      // number label so cursor paging and ordering still work.
      const numberLabel = text(
        link.querySelector('.ne-num, .ep-num, .ep-row-v2-no, [class*="num"]')
      );
      const labelMatch = numberLabel.match(/(\d+)/);
      chapters.set(remainder, {
        url: target.href,
        episodeId: remainder,
        displayNumber: (row && (row.getAttribute('data-ep')
          || row.getAttribute('data-episode-number')))
          || (labelMatch ? labelMatch[1] : ''),
        name: chapterName,
      });
    }
  };

  collect(document, location.href);

  // sbxh only renders the newest 100 rows and loads older ones through a
  // cursor API behind its "이전 회차 더 보기" button. The DOM keeps at most
  // 300 rows, so read the API directly. The cursor is `${number}:${id}` of
  // the oldest row seen so far, matching the site's own client.
  const notes = [];
  const oldestRow = () => {
    let oldest = null;
    for (const item of chapters.values()) {
      const number = Number.parseInt(item.displayNumber, 10);
      if (!Number.isFinite(number) || number <= 0) continue;
      if (!oldest || number < oldest.number
          || (number === oldest.number
              && /^\d+$/.test(item.episodeId) && /^\d+$/.test(oldest.id)
              && BigInt(item.episodeId) < BigInt(oldest.id))) {
        oldest = { number, id: item.episodeId };
      }
    }
    return oldest;
  };
  // The server-rendered page data carries the list's own olderCursor
  // (e.g. "301:8702759"); it does not depend on how rows are marked up.
  let pageCursor = '';
  for (const script of document.querySelectorAll('script')) {
    const source = script.textContent || '';
    const match = source.match(
      /\\?"hasOlder\\?"\s*:\s*true\s*,[^{}]*?\\?"olderCursor\\?"\s*:\s*\\?"(\d+:\d+)\\?"/
    );
    if (match) { pageCursor = match[1]; break; }
  }
  const cursorRow = oldestRow();
  if (pageCursor || (cursorRow && cursorRow.number > 1)) {
    let cursor = pageCursor || `${cursorRow.number}:${cursorRow.id}`;
    const seenCursors = new Set();
    let windows = 0;
    while (cursor && !seenCursors.has(cursor) && windows < 500) {
      seenCursors.add(cursor);
      windows += 1;
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 20000);
      let data = null;
      try {
        const query = new URLSearchParams({ direction: 'older', cursor });
        const response = await fetch(
          `/api/${kind}/${encodeURIComponent(novelId)}/episodes/window?${query}`,
          {
            headers: { Accept: 'application/json' },
            credentials: 'include',
            cache: 'no-store',
            signal: controller.signal,
          },
        );
        const body = await response.text();
        try { data = JSON.parse(body); } catch (_) { data = null; }
        if (!response.ok || !data || data.ok !== true
            || !Array.isArray(data.items)) {
          notes.push(
            `Episode window API stopped at cursor ${cursor} `
            + `(HTTP ${response.status}).`
          );
          break;
        }
      } catch (error) {
        notes.push(`Episode window API failed at cursor ${cursor}: ${error}`);
        break;
      } finally {
        clearTimeout(timer);
      }
      for (const item of data.items) {
        const episodeId = String(item && item.id || '');
        if (!ntkEpisodeId.test(episodeId)) continue;
        chapters.set(episodeId, {
          url: new URL(`${chapterPrefix}${episodeId}`, location.origin).href,
          episodeId,
          displayNumber: Number.isInteger(item.number)
            ? String(item.number) : '',
          name: String(item.title || item.episodeLabel || '').trim(),
        });
      }
      const nextRow = oldestRow();
      if (!data.hasOlder || !data.items.length || !nextRow
          || nextRow.number <= 1) {
        break;
      }
      cursor = typeof data.olderCursor === 'string' && data.olderCursor
        ? data.olderCursor
        : `${nextRow.number}:${nextRow.id}`;
    }
    if (windows) {
      notes.push(
        `Episode window API: ${windows} request(s), `
        + `${chapters.size} episode(s) collected.`
      );
    }
  } else if (chapters.size) {
    notes.push(
      `Episode window API skipped: no older cursor found `
      + `(${chapters.size} row(s), oldest number `
      + `${cursorRow ? cursorRow.number : 'unknown'}).`
    );
  }

  // Some sbxh indexes virtualize the long episode list. Walk every relevant
  // scroll container while retaining rows that disappear from the live DOM.
  const initialNumbers = Array.from(chapters.values())
    .map((item) => Number.parseInt(item.displayNumber, 10))
    .filter((number) => Number.isFinite(number) && number > 0);
  if (initialNumbers.length && Math.min(...initialNumbers) > 1) {
    const clicked = new Set();
    let stableRounds = 0;
    for (let round = 0; round < 80 && stableRounds < 3; round += 1) {
      const before = chapters.size;
      let moved = false;

      for (const element of document.querySelectorAll(
        'button, [role="button"], a[href="#"], a[href^="javascript:"]'
      )) {
        if (clicked.has(element)) continue;
        const label = text(element).replace(/\s+/g, ' ');
        if (!/(?:더\s*보기|전체\s*(?:목록|보기)|이전\s*(?:목록|회차)|load\s*more|show\s*all|older)/i.test(label)) {
          continue;
        }
        clicked.add(element);
        try {
          element.click();
          moved = true;
        } catch (_) {}
      }

      const scrollers = new Set();
      for (const link of document.querySelectorAll(
        `a[href*="${chapterPrefix}"]`
      )) {
        let node = link.parentElement;
        while (node && node !== document.body) {
          const style = getComputedStyle(node);
          if (node.scrollHeight > node.clientHeight + 8
              && /(auto|scroll)/.test(style.overflowY)) {
            scrollers.add(node);
          }
          node = node.parentElement;
        }
      }
      if (document.scrollingElement) scrollers.add(document.scrollingElement);
      for (const scroller of scrollers) {
        const oldTop = scroller.scrollTop;
        const step = Math.max(scroller.clientHeight * 0.8, 300);
        scroller.scrollTop = Math.min(
          scroller.scrollHeight - scroller.clientHeight,
          oldTop + step,
        );
        if (scroller.scrollTop > oldTop + 1) moved = true;
        try { scroller.dispatchEvent(new Event('scroll')); } catch (_) {}
      }

      await pause(100);
      collect(document, location.href);
      if (chapters.size === before && !moved) stableRounds += 1;
      else stableRounds = 0;
    }
  }

  // Traditional indexes expose older episode pages as links. Follow them in
  // the same verified browser session and recursively discover further pages.
  const queue = [];
  const queued = new Set();
  const visited = new Set([new URL(location.href).href.split('#')[0]]);
  const discoverPages = (root, baseUrl) => {
    for (const link of root.querySelectorAll('a[href]')) {
      let target;
      try {
        target = new URL(link.getAttribute('href') || link.href, baseUrl);
      } catch (_) {
        continue;
      }
      target.hash = '';
      if (target.origin !== location.origin) continue;
      const normalizedPath = target.pathname.replace(/\/$/, '');
      const suffix = normalizedPath.startsWith(basePath)
        ? normalizedPath.slice(basePath.length)
        : '';
      const isIndexPage = normalizedPath === basePath && Boolean(target.search);
      const isPagedPath = /^\/(?:page|p)\/\d+$/i.test(suffix);
      if (!isIndexPage && !isPagedPath) continue;
      if (visited.has(target.href) || queued.has(target.href)) continue;
      queued.add(target.href);
      queue.push(target.href);
    }
  };

  discoverPages(document, location.href);
  // Current NewToki/sbxh builds do not always render pagination anchors.
  // Probe the site's page query (epage on NewToki, page on sbxh) so
  // older/newer rows are still discovered.
  const requestedUrl = new URL(location.href);
  requestedUrl.hash = '';
  requestedUrl.searchParams.delete(pageParam);
  const probes = new Set();
  for (let pageNumber = 1; pageNumber <= 20; pageNumber += 1) {
    const target = new URL(requestedUrl.href);
    target.searchParams.set(pageParam, String(pageNumber));
    probes.add(target.href);
    if (!visited.has(target.href) && !queued.has(target.href)) {
      queued.add(target.href);
      queue.push(target.href);
    }
  }
  let fetchedPages = 0;
  let emptyProbes = 0;
  while (queue.length && fetchedPages < 20) {
    const pageUrl = queue.shift();
    queued.delete(pageUrl);
    if (visited.has(pageUrl)) continue;
    // Stop blind probing once consecutive probed pages add nothing; the
    // site has run out of pages. Discovered pager links are still followed.
    if (probes.has(pageUrl) && emptyProbes >= 2) continue;
    visited.add(pageUrl);
    fetchedPages += 1;
    const sizeBefore = chapters.size;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(pageUrl, {
        credentials: 'include',
        cache: 'no-store',
        signal: controller.signal,
      });
      if (!response.ok) continue;
      const pageHtml = await response.text();
      const lowered = pageHtml.toLowerCase();
      if (lowered.includes('access denied')
          || lowered.includes('site security policy')
          || lowered.includes('just a moment')) {
        continue;
      }
      const pageDocument = new DOMParser().parseFromString(pageHtml, 'text/html');
      collect(pageDocument, pageUrl);
      discoverPages(pageDocument, pageUrl);
    } catch (_) {
      // Keep the already collected rows if one optional index page fails.
    } finally {
      clearTimeout(timer);
      if (probes.has(pageUrl)) {
        emptyProbes = chapters.size > sizeBefore ? 0 : emptyProbes + 1;
      }
    }
  }

  return Array.from(chapters.values());
};

  const ntkFetchChapterPlaintext = async (chapterUrl) => {
  const fetchWithTimeout = async (url, options = {}) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  };
  const cookieValue = (name) => {
    for (const part of document.cookie.split(';')) {
      const item = part.trim();
      const split = item.indexOf('=');
      if (split < 0 || item.slice(0, split) !== name) continue;
      const value = item.slice(split + 1);
      try { return decodeURIComponent(value); } catch (_) { return value; }
    }
    return '';
  };
  const deleteCookie = (name) => {
    const secure = location.protocol === 'https:' ? '; Secure' : '';
    document.cookie = `${name}=; Path=/; Max-Age=0; SameSite=Lax${secure}`;
  };
  const b64url = (bytes) => {
    let binary = '';
    for (const value of bytes) binary += String.fromCharCode(value);
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  };
  const fromB64url = (value) => {
    let normalized = String(value || '').replace(/-/g, '+').replace(/_/g, '/');
    while (normalized.length % 4) normalized += '=';
    const binary = atob(normalized);
    return Uint8Array.from(binary, (char) => char.charCodeAt(0));
  };
  const randomHex = (size) => Array.from(
    crypto.getRandomValues(new Uint8Array(size)),
    (value) => value.toString(16).padStart(2, '0'),
  ).join('');
  const jsonPost = async (url, body, extraHeaders = {}) => {
    const response = await fetchWithTimeout(url, {
      method: 'POST',
      credentials: 'include',
      cache: 'no-store',
      headers: {
        'content-type': 'application/json',
        ...extraHeaders,
      },
      body: JSON.stringify(body),
    });
    return {
      response,
      data: await response.json().catch(() => null),
    };
  };
  const normalizeChallenge = (data, fallbackScope) => {
    if (!data || typeof data !== 'object') return null;
    const nestedData = data.data && typeof data.data === 'object'
      ? data.data
      : null;
    const source = data.challenge
      || (nestedData && nestedData.challenge)
      || nestedData
      || data;
    if (!source || typeof source !== 'object') return null;
    const token = source.token || source.challengeToken || '';
    if (!token) return null;
    const slotNonces = Array.isArray(source.slotNonces)
      ? source.slotNonces
      : (Array.isArray(source.nonces) ? source.nonces : []);
    return {
      token,
      slotNonces,
      scope: source.scope || data.scope || fallbackScope,
    };
  };

  try {
    const target = new URL(chapterUrl, location.href);
    const ids = target.pathname.match(/^\/(?:novel|webtoon)\/(\d+)\/(\d+)/);
    if (!ids) return { ok: false, stage: 'url', status: 0 };
    const contentScope = target.pathname.split('/')[1] || 'novel';
    const encoder = new TextEncoder();
    const extractTokens = (pageHtml) => {
      const pattern = /(?:\\"|")token(?:\\"|")\s*:\s*(?:\\"|")([A-Za-z0-9_=.-]+)(?:\\"|")/g;
      return Array.from(pageHtml.matchAll(pattern), (match) => match[1]);
    };

    for (const badCookie of ['ntk_blk', 'ntk_dev_warn']) {
      if (cookieValue(badCookie)) deleteCookie(badCookie);
    }

    if (!window.__npiaNtkEventSynced) {
      await fetchWithTimeout(new URL('/api/me', target.origin).href, {
        credentials: 'include',
        cache: 'no-store',
      }).catch(() => null);
      let eventId = cookieValue('__ntk_ev_id');
      if (!eventId) {
        eventId = randomHex(32);
        const secure = location.protocol === 'https:' ? '; Secure' : '';
        document.cookie = `__ntk_ev_id=${eventId}; Path=/; SameSite=Lax${secure}`;
      }
      const sync = await jsonPost(
        new URL('/api/ev/sync', target.origin).href,
        { evId: eventId },
      ).catch(() => null);
      if (sync && sync.response.ok) window.__npiaNtkEventSynced = true;
    }

    let forceNvReissue = false;
    let lastRetryFailure = { ok: false, stage: 'retry', status: 0 };
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      target.searchParams.set('cb', String(Date.now()));
      const chapterResponse = await fetchWithTimeout(target.href, {
        credentials: 'include',
        cache: 'no-store',
      });
      const chapterHtml = await chapterResponse.text();
      if (!chapterResponse.ok) {
        return { ok: false, stage: 'chapter', status: chapterResponse.status };
      }

      const tokens = extractTokens(chapterHtml);
      let token = tokens.find((value) => (
        value.startsWith('eyJuIjoi') || value.startsWith('eyJlIjoi')
      ));
      if (!token) return { ok: false, stage: 'token', status: 0 };

      const cookieMatch = chapterHtml.match(/\\"cookieName\\":\\"([^\\"]+)\\"/)
        || chapterHtml.match(/"cookieName"\s*:\s*"([^"]+)"/);
      let cookieName = cookieMatch ? cookieMatch[1] : 'nv';
      const chapterPath = target.pathname;
      const handshake = [];

      const explicitChallengeMatch = chapterHtml.match(
        /(?:\\"|")challengeToken(?:\\"|")\s*:\s*(?:\\"|")([^"\\]+)(?:\\"|")/,
      );
      const challengeToken = explicitChallengeMatch
        ? explicitChallengeMatch[1]
        : tokens.find((value) => value !== token && value.startsWith('eyJ'));
      const scopePattern = /(?:\\"|")(?:adScope|challengeScope|scope)(?:\\"|")\s*:\s*(?:\\"|")([^"\\]+)(?:\\"|")/g;
      const embeddedScopes = Array.from(
        chapterHtml.matchAll(scopePattern),
        (match) => match[1],
      ).filter((value, index, values) => (
        value && value.length <= 500 && values.indexOf(value) === index
      ));
      const slotMatch = chapterHtml.match(
        /(?:\\"|")slotNonces(?:\\"|")\s*:\s*\[(.*?)\]/,
      );
      const slotNonces = slotMatch
        ? Array.from(
          slotMatch[1].matchAll(/(?:\\"|")([^"\\]+)(?:\\"|")/g),
          (match) => match[1],
        )
        : [];
      let challenge = challengeToken
        ? {
          token: challengeToken,
          slotNonces,
          scope: embeddedScopes[0] || contentScope,
        }
        : null;
      if (!challenge) {
        const challengeUrl = new URL('/api/ad/challenge', target.origin).href;
        const scopeCandidates = [
          ...embeddedScopes,
          'chapter',
          `${contentScope}-content`,
          contentScope,
          chapterPath,
        ].filter((value, index, values) => values.indexOf(value) === index);
        const challengePayloads = [{ path: chapterPath, force: false }];
        challengePayloads.push(...scopeCandidates.map((scope) => ({
          scope,
          path: chapterPath,
        })));
        challengePayloads.push({ path: chapterPath });
        for (const challengePayload of challengePayloads) {
          const challengeReply = await jsonPost(
            challengeUrl,
            challengePayload,
          );
          const challengeData = challengeReply.data;
          const challengeError = challengeData && challengeData.error
            ? String(challengeData.error)
            : '';
          handshake.push(
            `challenge=${challengeReply.response.status}`
            + (challengeError ? `/${challengeError}` : ''),
          );
          if (challengeReply.response.ok && challengeData) {
            challenge = normalizeChallenge(
              challengeData,
              challengePayload.scope || contentScope,
            );
            if (challenge) break;
          }
          if (challengeError !== 'bad_scope') break;
        }
      } else {
        handshake.push('challenge=embedded');
      }

      if (challenge && challenge.token) {
        const canary = await jsonPost(
          new URL('/api/ad/canary', target.origin).href,
          {
            adAckCanary: true,
            challengeToken: challenge.token,
            scope: challenge.scope || contentScope,
            path: chapterPath,
          },
        );
        const canaryError = canary.data && canary.data.error
          ? String(canary.data.error)
          : '';
        handshake.push(
          `canary=${canary.response.status}`
          + (canaryError ? `/${canaryError}` : ''),
        );
        let totalAds = Array.from(
          chapterHtml.matchAll(/data-br-n=(?:\\"|")?(\d+)(?:\\"|")?/g),
          (match) => Number.parseInt(match[1], 10) || 0,
        ).reduce((total, value) => total + value, 0);
        const nonces = Array.isArray(challenge.slotNonces)
          ? challenge.slotNonces
          : [];
        if (!totalAds && nonces.length) totalAds = nonces.length;
        const visibleNonces = totalAds > 0 ? nonces.slice(0, totalAds) : nonces;
        const ack = await jsonPost(
          new URL('/api/ad/ack', target.origin).href,
          {
            challengeToken: challenge.token,
            scope: challenge.scope || contentScope,
            total: totalAds,
            visible: totalAds,
            path: chapterPath,
            slotNonces: visibleNonces,
          },
        );
        if (!ack.response.ok || !ack.data || !ack.data.ok) {
          return {
            ok: false,
            stage: 'ad-ack',
            status: ack.response.status,
            error: ack.data && ack.data.error ? String(ack.data.error) : '',
          };
        }
        handshake.push(`ack=${ack.response.status}`);

        // The content token is scoped to the acknowledgement state. Fetch a
        // fresh chapter document after a successful acknowledgement instead
        // of reusing the token issued before it.
        target.searchParams.set('cb', String(Date.now() + 1));
        const refreshedResponse = await fetchWithTimeout(target.href, {
          credentials: 'include',
          cache: 'no-store',
        });
        const refreshedHtml = await refreshedResponse.text();
        if (!refreshedResponse.ok) {
          return {
            ok: false,
            stage: 'post-ack-chapter',
            status: refreshedResponse.status,
          };
        }
        const refreshedToken = extractTokens(refreshedHtml).find((value) => (
          value.startsWith('eyJuIjoi') || value.startsWith('eyJlIjoi')
        ));
        if (!refreshedToken) {
          return { ok: false, stage: 'post-ack-token', status: 0 };
        }
        token = refreshedToken;
        const refreshedCookieMatch = refreshedHtml.match(
          /\\"cookieName\\":\\"([^\\"]+)\\"/,
        ) || refreshedHtml.match(/"cookieName"\s*:\s*"([^"]+)"/);
        if (refreshedCookieMatch) cookieName = refreshedCookieMatch[1];
        handshake.push('refresh=token');
      }

      let nv = cookieValue(cookieName);
      if (!nv || forceNvReissue) {
        if (forceNvReissue) deleteCookie(cookieName);
        await fetchWithTimeout(new URL('/api/nv-issue', target.origin).href, {
          method: 'POST',
          credentials: 'include',
          cache: 'no-store',
        });
        nv = cookieValue(cookieName);
        forceNvReissue = false;
      }
      if (!nv) return { ok: false, stage: 'cookie', status: 0 };

      const nonce = b64url(crypto.getRandomValues(new Uint8Array(24)));
      const hmacKey = await crypto.subtle.importKey(
        'raw',
        encoder.encode(nv),
        { name: 'HMAC', hash: 'SHA-256' },
        false,
        ['sign'],
      );
      const signature = await crypto.subtle.sign(
        'HMAC',
        hmacKey,
        encoder.encode(`${token}.${nonce}.${navigator.userAgent}`),
      );
      const proof = b64url(new Uint8Array(signature));
      const contentReply = await jsonPost(
        new URL('/api/novel-content', target.origin).href,
        {
          novelId: ids[1],
          episodeId: ids[2],
          token,
          nonce,
          proof,
        },
        { 'x-novel-client': 'shadow-v3' },
      );
      const content = contentReply.data;
      if (
        !contentReply.response.ok
        || !content
        || !content.ok
        || !content.payload
      ) {
        const error = content && content.error ? String(content.error) : '';
        if (error === 'expired') {
          lastRetryFailure = {
            ok: false,
            stage: 'content',
            status: contentReply.response.status,
            error: `${error} (${handshake.join(', ')})`,
          };
          forceNvReissue = true;
          continue;
        }
        if (error === 'ad_ack_required') {
          lastRetryFailure = {
            ok: false,
            stage: 'content',
            status: contentReply.response.status,
            error: `${error} (${handshake.join(', ')})`,
          };
          continue;
        }
        return {
          ok: false,
          stage: 'content',
          status: contentReply.response.status,
          error,
        };
      }

      const cookieKey = fromB64url(nv.split('.')[0] || '');
      const encrypted = fromB64url(content.payload);
      if (!cookieKey.length || encrypted.length < 29) {
        return { ok: false, stage: 'decrypt', status: 0 };
      }
      const suffix = encoder.encode(`:${ids[1]}:${ids[2]}:v3`);
      const material = new Uint8Array(cookieKey.length + suffix.length);
      material.set(cookieKey, 0);
      material.set(suffix, cookieKey.length);
      const aesKeyBytes = await crypto.subtle.digest('SHA-256', material);
      const aesKey = await crypto.subtle.importKey(
        'raw',
        aesKeyBytes,
        { name: 'AES-GCM' },
        false,
        ['decrypt'],
      );
      const plaintext = await crypto.subtle.decrypt(
        {
          name: 'AES-GCM',
          iv: encrypted.slice(0, 12),
          tagLength: 128,
        },
        aesKey,
        encrypted.slice(12),
      );
      return {
        ok: true,
        plaintext: new TextDecoder('utf-8').decode(plaintext),
      };
    }
    return lastRetryFailure;
  } catch (error) {
    return { ok: false, stage: 'exception', status: 0, error: String(error) };
  }
};

  const ntkFetchHtml = async (url) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const target = new URL(url, location.href);
    target.searchParams.set('cb', String(Date.now()));
    const response = await fetch(target.href, {
      credentials: 'include',
      cache: 'no-store',
      signal: controller.signal,
    });
    return {
      ok: response.ok,
      status: response.status,
      text: await response.text(),
    };
  } catch (error) {
    return { ok: false, status: 0, error: String(error) };
  } finally {
    clearTimeout(timer);
  }
};

  const ntkBlockMarkers = ['access denied', 'site security policy', 'just a moment', 'country restriction', 'cf-challenge', '/cdn-cgi/challenge'];
  const ntkHtml = () => ((document.documentElement && document.documentElement.innerHTML) || '');
  const ntkBlocked = () => {
    const lower = ntkHtml().toLowerCase();
    return ntkBlockMarkers.some((marker) => lower.includes(marker));
  };
  const ntkChallenge = () => /just a moment|cf-challenge|\/cdn-cgi\/challenge|checking your browser/i.test(ntkHtml());
  const ntkPageParam = () => (/(?:^|\.)sbxh\d+\./.test(location.host) ? 'page' : 'epage');
  const ntkDisplayNumber = (item) => {
    const display = parseInt(item.displayNumber, 10);
    if (Number.isFinite(display) && display > 0) return display;
    const labels = String(item.name || '').match(/(\d+)/g);
    if (labels && labels.length) {
      const last = parseInt(labels[labels.length - 1], 10);
      if (Number.isFinite(last) && last > 0) return last;
    }
    const number = parseInt(item.number, 10);
    if (Number.isFinite(number) && number > 0) return number;
    return null;
  };
  // board_uploads holds the site's banner ads, which fill `main` and rotate.
  const ntkIsBanner = (url) => /\/board_uploads\//.test(url || '');
  const ntkImageName = (url, chapter, page) => {
    const last = (String(url).split(/[?#]/)[0].split('/').pop() || '');
    const dot = last.lastIndexOf('.');
    const candidate = dot >= 0 ? last.slice(dot + 1).toLowerCase() : '';
    // Mirrors disguise page files as .js, .css or .woff; the image stage names them by their bytes.
    const extension = /^(?:jpe?g|png|webp|gif|avif|bmp)$/.test(candidate) ? candidate : 'webp';
    return `images/${String(chapter).padStart(4, '0')}_${String(page).padStart(4, '0')}.${extension}`;
  };
  const ntkCleanText = (value) => String(value == null ? '' : value)
    .replace(/\r\n/g, '\n')
    .replace(/[\u200b\ufeff]/g, '')
    .split('\n').map((line) => line.replace(/[ \t\u00a0]+/g, ' ').replace(/\s+$/, '')).join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  const ntkUnwrapPlaintext = (plaintext) => {
    let data = null;
    try { data = JSON.parse(plaintext); } catch (e) { data = null; }
    if (data && typeof data === 'object') {
      if (data.kind === 'html' && typeof data.html === 'string') return { html: data.html };
      if (data.kind === 'text-shuffled' && Array.isArray(data.paragraphs) && Array.isArray(data.perm)) {
        const ordered = [];
        for (let i = 0; i < data.paragraphs.length; i += 1) {
          const pos = data.perm[i];
          if (Number.isInteger(pos)) ordered[pos] = data.paragraphs[i];
        }
        return { text: ordered.filter((p) => p != null).join('\n\n') };
      }
      if (data.kind === 'text' && Array.isArray(data.paragraphs)) return { text: data.paragraphs.join('\n\n') };
      if (typeof data.text === 'string') return { text: data.text };
    }
    return { text: String(plaintext == null ? '' : plaintext) };
  };
  const ntkTextResult = (title, unwrapped) => {
    if (unwrapped.html != null) {
      let doc = null;
      try { doc = new DOMParser().parseFromString(unwrapped.html, 'text/html'); } catch (e) { doc = null; }
      if (!doc || !doc.body) return null;
      doc.body.querySelectorAll('script, style, iframe, noscript').forEach((el) => el.remove());
      const body = ntkCleanText(doc.body.textContent || '');
      const inner = doc.body.innerHTML.trim();
      if (!body && !/<img/i.test(inner)) return null;
      return { chapterName: title || 'Chapter', contentText: body, contentHtml: `<div class="ntk-content">${inner}</div>` };
    }
    const body = ntkCleanText(unwrapped.text);
    if (!body) return null;
    let paragraphs = body.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
    if (!paragraphs.length) paragraphs = body.split('\n').map((p) => p.trim()).filter(Boolean);
    const html = '<div class="ntk-content">' + paragraphs.map((p) => `<p>${escapeHtml(p)}</p>`).join('\n') + '</div>';
    return { chapterName: title || 'Chapter', contentText: body, contentHtml: html };
  };
  const ntkImageUrls = (htmlText, baseUrl) => {
    const out = [];
    const seen = new Set();
    const add = (raw) => {
      if (!raw) return;
      let abs;
      try { abs = new URL(String(raw).trim(), baseUrl).href; } catch (e) { return; }
      if (!/^https?:/i.test(abs)) return;
      if (!/\.(?:jpe?g|png|webp|gif|avif)(?:[?#]|$)/i.test(abs)) return;
      if (/(?:favicon|avatar|profile|emoji|logo|sprite|blank)/i.test(abs)) return;
      if (/(?:novel|webtoon)_thumb|og-default/i.test(abs) || ntkIsBanner(abs)) return;
      if (seen.has(abs)) return;
      seen.add(abs);
      out.push(abs);
    };
    let doc = null;
    try { doc = new DOMParser().parseFromString(htmlText, 'text/html'); } catch (e) { doc = null; }
    if (doc) {
      for (const img of doc.querySelectorAll('img')) {
        add(img.getAttribute('data-src') || img.getAttribute('data-original')
          || img.getAttribute('data-lazy-src') || img.getAttribute('src'));
        const srcset = img.getAttribute('srcset') || img.getAttribute('data-srcset');
        if (srcset) srcset.split(',').forEach((part) => add(part.trim().split(/\s+/)[0]));
      }
    }
    const re = /https?:\/\/[^\s"'<>]+?\.(?:jpe?g|png|webp|gif|avif)/gi;
    let match;
    while ((match = re.exec(htmlText))) {
      if (/toonflix|imagebox|\/(?:data|upload|webtoon|image)s?\//i.test(match[0])) add(match[0]);
    }
    return out;
  };

  ntk.pageState = function (options) {
    const opts = options || {};
    const kind = opts.kind || 'novel';
    const novelId = String(opts.novelId || '');
    if (ntkChallenge()) return { state: 'verification' };
    if (ntkBlocked()) return { state: 'blocked' };
    const prefix = `/${kind}/${novelId}/`;
    const ready = Array.from(document.querySelectorAll('a[href]')).some((link) => {
      try { return new URL(link.href, location.href).pathname.startsWith(prefix); } catch (e) { return false; }
    });
    return { state: (document.readyState !== 'loading' && ready) ? 'ready' : 'waiting' };
  };

  ntk.chapterState = function () {
    if (ntkChallenge()) return { ready: true, blocked: false, verification: true };
    if (ntkBlocked()) return { ready: true, blocked: true, verification: false };
    return { ready: document.readyState !== 'loading', blocked: false, verification: false };
  };

  ntk.bookInfo = function (options) {
    const opts = options || {};
    const kind = opts.kind || 'novel';
    const novelId = String(opts.novelId || '');
    if (ntkChallenge()) return { ok: false, error: 'verification' };
    if (ntkBlocked()) return { ok: false, blocked: true, error: 'blocked' };
    const prefix = `/${kind}/${novelId}/`;
    const ready = Array.from(document.querySelectorAll('a[href]')).some((link) => {
      try { return new URL(link.href, location.href).pathname.startsWith(prefix); } catch (e) { return false; }
    });
    if (!ready && document.readyState === 'loading') return { ok: false, error: 'waiting' };
    const firstText = (selectors) => {
      for (const selector of selectors) {
        const value = text(document.querySelector(selector));
        if (value) return value;
      }
      return '';
    };
    const firstImage = (selectors) => {
      for (const selector of selectors) {
        for (const el of document.querySelectorAll(selector)) {
          const url = el.currentSrc || el.src;
          if (url && !ntkIsBanner(url)) return url;
        }
      }
      return '';
    };
    const ogImage = metaContent('og:image') || '';
    const tags = [];
    for (const el of document.querySelectorAll('.hero-v2-tag, [class*="tag"] a, a[href*="tag"], a[href*="genre"]')) {
      const value = text(el).replace(/^#/, '').trim();
      if (value && value.length <= 50 && !tags.includes(value)) tags.push(value);
    }
    let title = firstText(['.novel-detail h1', '.novel-title', '.nd-title', '[data-novel-title]'])
      || metaContent('og:title') || firstText(['main h1', 'h1']) || document.title || '';
    title = title.split(/\s+[-|]\s+/)[0].trim();
    let cover = absolute(firstImage(['.nd-thumb img', '.novel-detail img'])
      || (!ntkIsBanner(ogImage) && !/og-default/.test(ogImage) ? ogImage : '')
      || firstImage(['main img']));
    if (cover) cover = cover.replace(/aws-cdn\d+\.site/i, 'aws-cdn1.site');
    return {
      ok: true,
      kind,
      title,
      author: firstText(['.nd-meta span:first-child', '[class*="author"]', '[rel="author"]']),
      synopsis: firstText(['.nd-desc', '[class*="description"]', '[class*="intro"]']),
      cover: cover || '',
      tags,
    };
  };

  ntk.chapterList = async function (options) {
    const opts = options || {};
    const kind = opts.kind || 'novel';
    const novelId = String(opts.novelId || '');
    if (!novelId) return { ok: false, error: 'missing novel id' };
    let rows;
    try { rows = await ntkCollectChapters(novelId, kind, ntkPageParam()); } catch (e) { return { ok: false, error: String((e && e.message) || e) }; }
    const map = new Map();
    for (const row of (rows || [])) {
      const episodeId = String(row.episodeId || '');
      if (!ntkEpisodeId.test(episodeId) || map.has(episodeId)) continue;
      map.set(episodeId, { url: row.url, episodeId, name: String(row.name || '').trim(), sourceNumber: ntkDisplayNumber(row) });
    }
    let chapters = Array.from(map.values());
    if (!chapters.length) return { ok: false, error: 'no chapters found' };
    const numbers = chapters.map((c) => c.sourceNumber);
    const allNumbered = numbers.every((n) => n != null) && new Set(numbers).size === numbers.length;
    if (allNumbered) chapters.sort((a, b) => a.sourceNumber - b.sourceNumber);
    else chapters.reverse();
    chapters = chapters.map((c, index) => ({
      url: c.url,
      episodeId: c.episodeId,
      number: index + 1,
      name: c.name || `Chapter ${index + 1}`,
    }));
    return { ok: true, chapters };
  };

  // The site now acknowledges its ads in a wasm module that signs the ack (ad_proof_required /
  // missing_canary without it), so the reader's own requests are the ones that work. As on the desktop
  // batch path, the chapter page is left to run its ad check and ask /api/novel-content itself; the
  // reply is captured here and decrypted with the page's nv cookie. A webtoon chapter's page list comes
  // the same way, from /api/webtoon-images.
  const ntkCaptured = new Map();
  const ntkCapturedPages = new Map();
  const ntkIds = (url) => {
    try { return new URL(url, location.href).pathname.match(/^\/(?:novel|webtoon)\/(\d+)\/([\w-]+)/); } catch (e) { return null; }
  };
  const ntkReplyKey = (bodyText) => {
    let body = null;
    try { body = JSON.parse(bodyText || 'null'); } catch (e) { body = null; }
    const work = body && (body.novelId != null ? body.novelId : body.workId);
    if (work != null && body.episodeId != null) return `${work}/${body.episodeId}`;
    const page = ntkIds(location.href);
    return page ? `${page[1]}/${page[2]}` : '';
  };
  const ntkRecordContent = (bodyText, status, data) => {
    const key = ntkReplyKey(bodyText);
    if (!key) return;
    const previous = ntkCaptured.get(key);
    // A good reply is kept even if a later retry of the reader fails.
    if (previous && previous.data && previous.data.ok && previous.data.payload) return;
    ntkCaptured.set(key, { status, data });
  };
  const ntkRecordPages = (bodyText, status, data) => {
    const key = ntkReplyKey(bodyText);
    if (!key) return;
    const previous = ntkCapturedPages.get(key);
    if (previous && previous.data && previous.data.ok && Array.isArray(previous.data.images) && previous.data.images.length) return;
    ntkCapturedPages.set(key, { status, data });
  };
  ntk.installContentHook = function () {
    if (typeof root.fetch !== 'function' || root.__npiaNtkFetchHooked) return;
    root.__npiaNtkFetchHooked = true;
    const original = root.fetch;
    const hooked = function (input, init) {
      const reply = original.apply(this, arguments);
      try {
        const url = typeof input === 'string' ? input : ((input && input.url) || String(input));
        const path = new URL(url, location.href).pathname;
        const record = path === '/api/novel-content' ? ntkRecordContent : (path === '/api/webtoon-images' ? ntkRecordPages : null);
        if (record) {
          const bodyText = init && typeof init.body === 'string'
            ? Promise.resolve(init.body)
            : (input && typeof input.clone === 'function' ? input.clone().text().catch(() => '') : Promise.resolve(''));
          reply.then((response) => Promise.all([
            bodyText,
            response.clone().json().catch(() => null),
          ]).then(([text, data]) => record(text, response.status, data))).catch(() => {});
        }
      } catch (e) { /* never break the site's own request */ }
      return reply;
    };
    try { hooked.toString = original.toString.bind(original); } catch (e) { /* cosmetic */ }
    root.fetch = hooked;
  };
  const ntkDecrypt = async (novelId, episodeId, payload) => {
    const html = (document.documentElement && document.documentElement.innerHTML) || '';
    const cookieMatch = html.match(/\\"cookieName\\":\\"([^\\"]+)\\"/) || html.match(/"cookieName"\s*:\s*"([^"]+)"/);
    const cookieName = cookieMatch ? cookieMatch[1] : 'nv';
    let nv = '';
    for (const part of document.cookie.split(';')) {
      const item = part.trim();
      const split = item.indexOf('=');
      if (split < 0 || item.slice(0, split) !== cookieName) continue;
      nv = item.slice(split + 1);
      try { nv = decodeURIComponent(nv); } catch (e) { /* raw value */ }
      break;
    }
    if (!nv) return { ok: false, stage: 'page-cookie', status: 0 };
    const fromB64url = (value) => {
      let normalized = String(value || '').replace(/-/g, '+').replace(/_/g, '/');
      while (normalized.length % 4) normalized += '=';
      return Uint8Array.from(atob(normalized), (char) => char.charCodeAt(0));
    };
    const encoder = new TextEncoder();
    const cookieKey = fromB64url(nv.split('.')[0] || '');
    const encrypted = fromB64url(payload);
    if (!cookieKey.length || encrypted.length < 29) return { ok: false, stage: 'page-decrypt', status: 0 };
    const suffix = encoder.encode(`:${novelId}:${episodeId}:v3`);
    const material = new Uint8Array(cookieKey.length + suffix.length);
    material.set(cookieKey, 0);
    material.set(suffix, cookieKey.length);
    const aesKey = await crypto.subtle.importKey('raw', await crypto.subtle.digest('SHA-256', material), { name: 'AES-GCM' }, false, ['decrypt']);
    const plaintext = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: encrypted.slice(0, 12), tagLength: 128 }, aesKey, encrypted.slice(12));
    return { ok: true, plaintext: new TextDecoder('utf-8').decode(plaintext) };
  };
  /** Waits for the reader's own content reply for this chapter, then decrypts it. */
  const ntkPageContent = async (chapterUrl, waitMs) => {
    const ids = ntkIds(chapterUrl);
    if (!ids) return { ok: false, stage: 'url', status: 0 };
    const key = `${ids[1]}/${ids[2]}`;
    const deadline = Date.now() + waitMs;
    let seen = null;
    while (Date.now() < deadline) {
      seen = ntkCaptured.get(key) || null;
      if (seen && seen.data && seen.data.ok && seen.data.payload) {
        try { return await ntkDecrypt(ids[1], ids[2], seen.data.payload); } catch (e) { return { ok: false, stage: 'page-decrypt', status: 0, error: String((e && e.message) || e) }; }
      }
      await sleep(250);
    }
    if (!seen) return { ok: false, stage: 'page-content', status: 0, error: 'the reader did not request the chapter' };
    return { ok: false, stage: 'page-content', status: seen.status, error: (seen.data && seen.data.error) ? String(seen.data.error) : 'no payload' };
  };

  /**
   * One asset (the cover) fetched by the page itself. The image host resets connections from plain
   * HTTP clients, so the bytes come back base64 for a data: URL (desktop _ntk_fetch_binary_browser).
   */
  ntk.fetchBinary = async function (options) {
    const opts = options || {};
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const target = new URL(String(opts.url || ''), location.href);
      // CDN covers usually answer Access-Control-Allow-Origin: *, which fails with credentials.
      const response = await fetch(target.href, {
        credentials: target.origin === location.origin ? 'include' : 'omit',
        cache: 'no-store',
        signal: controller.signal,
      });
      if (!response.ok) return { ok: false, status: response.status };
      const bytes = new Uint8Array(await response.arrayBuffer());
      let binary = '';
      for (let offset = 0; offset < bytes.length; offset += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
      }
      return { ok: true, status: response.status, contentType: response.headers.get('content-type') || '', data: btoa(binary) };
    } catch (e) {
      return { ok: false, status: 0, error: String((e && e.message) || e) };
    } finally {
      clearTimeout(timer);
    }
  };

  ntk.chapter = async function (options) {
    const opts = options || {};
    const chapterUrl = opts.chapterUrl || location.href;
    const title = opts.name || '';
    let fetched;
    const pageWait = Number.isFinite(opts.pageWaitMs) ? opts.pageWaitMs : 25000;
    let fromPage = null;
    if (root.__npiaNtkFetchHooked && pageWait > 0) {
      try { fromPage = await ntkPageContent(chapterUrl, pageWait); } catch (e) { fromPage = { ok: false, stage: 'page-content', status: 0, error: String((e && e.message) || e) }; }
    }
    const rateLimited = fromPage && !fromPage.ok
      && (fromPage.status === 429 || /too many|rate.?limit/i.test(String(fromPage.error || '')));
    if (fromPage && fromPage.ok) {
      fetched = fromPage;
    } else if (rateLimited) {
      // The old handshake cannot help here (the site refuses it with ad_proof_required); the native
      // side waits and reloads the chapter instead.
      return { ok: false, stage: 'rate-limit', status: 429, error: String(fromPage.error || 'too many requests') };
    } else {
      // The older hand-made handshake, for mirrors that still accept it.
      try { fetched = await ntkFetchChapterPlaintext(chapterUrl); } catch (e) { fetched = { ok: false, stage: 'exception', status: 0, error: String((e && e.message) || e) }; }
      if (fromPage && fetched && !fetched.ok) {
        fetched = { ...fetched, error: `${fromPage.error || fromPage.stage}; then ${fetched.error || fetched.stage || 'failed'}` };
      }
    }
    if (!fetched || !fetched.ok) {
      const status = (fetched && fetched.status) || 0;
      const stage = (fetched && fetched.stage) || '';
      const blocked = status === 403 || status === 451 || stage === 'blocked';
      return { ok: false, blocked, stage, status, error: (fetched && fetched.error) || stage || 'chapter fetch failed' };
    }
    const built = ntkTextResult(title, ntkUnwrapPlaintext(fetched.plaintext));
    if (!built || ntkCleanText(built.contentText).length < 40) {
      return { ok: false, stage: 'content', status: 0, error: 'the chapter had no readable text' };
    }
    return { ok: true, chapterName: built.chapterName, contentText: built.contentText, contentHtml: built.contentHtml, images: [] };
  };

  /** Waits for the reader's own page list for this chapter: `{status, data}`, or null if it never asked. */
  const ntkWebtoonReply = async (chapterUrl, waitMs) => {
    const ids = ntkIds(chapterUrl);
    if (!ids) return null;
    const key = `${ids[1]}/${ids[2]}`;
    const deadline = Date.now() + waitMs;
    let seen = null;
    while (Date.now() < deadline) {
      seen = ntkCapturedPages.get(key) || null;
      if (seen && seen.data && seen.data.ok && Array.isArray(seen.data.images) && seen.data.images.length) return seen;
      await sleep(250);
    }
    return seen;
  };
  /** A page list as `[{url, candidates}]` in reading order; `candidates` is every host serving the page. */
  ntk.webtoonPages = function (data) {
    const items = ((data && data.images) || []).filter((item) => item && typeof item === 'object');
    items.sort((a, b) => (a.page || 0) - (b.page || 0));
    const pages = [];
    for (const item of items) {
      const urls = [];
      for (const raw of [item.src].concat(item.srcCandidates || [])) {
        if (typeof raw !== 'string' || !raw) continue;
        let abs;
        try { abs = new URL(raw, location.href).href; } catch (e) { continue; }
        if (/^https?:/i.test(abs) && !ntkIsBanner(abs) && !urls.includes(abs)) urls.push(abs);
      }
      if (urls.length) pages.push({ url: urls[0], candidates: urls });
    }
    return pages;
  };
  const ntkImageChapter = (title, pages, chapter) => {
    const images = [];
    const parts = [];
    pages.forEach((page, index) => {
      const name = ntkImageName(page.url, chapter, index + 1);
      images.push({ url: page.url, name, candidates: page.candidates || [] });
      parts.push(`<div class="ntk-webtoon-page"><img src="${escapeHtml(name)}" alt="page ${index + 1}"/></div>`);
    });
    return { ok: true, chapterName: title || 'Chapter', contentText: '', contentHtml: `<div class="ntk-content">${parts.join('\n')}</div>`, images };
  };

  ntk.webtoon = async function (options) {
    const opts = options || {};
    const chapterUrl = opts.chapterUrl || location.href;
    const title = opts.name || '';
    const chapter = Number.isFinite(opts.index) ? opts.index : 0;
    const pageWait = Number.isFinite(opts.pageWaitMs) ? opts.pageWaitMs : 45000;
    if (root.__npiaNtkFetchHooked && pageWait > 0) {
      const reply = await ntkWebtoonReply(chapterUrl, pageWait);
      const pages = reply && reply.data && reply.data.ok ? ntk.webtoonPages(reply.data) : [];
      if (pages.length) return ntkImageChapter(title, pages, chapter);
      const error = String((reply && reply.data && reply.data.error) || '');
      if (reply && (reply.status === 429 || /too many|rate.?limit/i.test(error))) {
        return { ok: false, stage: 'rate-limit', status: 429, error: error || 'too many requests' };
      }
    }
    // Builds that still put the pages in the chapter HTML.
    let fetched;
    try { fetched = await ntkFetchHtml(chapterUrl); } catch (e) { return { ok: false, stage: 'exception', status: 0, error: String((e && e.message) || e) }; }
    if (!fetched || !fetched.ok) {
      return { ok: false, status: (fetched && fetched.status) || 0, error: (fetched && fetched.error) || 'webtoon fetch failed' };
    }
    const urls = ntkImageUrls(fetched.text, chapterUrl);
    if (!urls.length) return { ok: false, error: 'no webtoon images were found' };
    return ntkImageChapter(title, urls.map((url) => ({ url })), chapter);
  };


  const tasks = new Map();
  let taskCounter = 0;
  hard.tasks = {
    start(path, args) {
      let owner = hard;
      let target = hard;
      for (const part of String(path).split('.')) {
        owner = target;
        target = target == null ? undefined : target[part];
      }
      if (typeof target !== 'function') throw new Error(`unknown entry point ${path}`);
      taskCounter += 1;
      const ticket = `t${taskCounter}`;
      const entry = { state: 'running', json: '', error: '' };
      tasks.set(ticket, entry);
      Promise.resolve().then(() => target.apply(owner, args || [])).then(
        (value) => { entry.json = JSON.stringify(value === undefined ? null : value); entry.state = 'done'; },
        (error) => { entry.error = String((error && error.message) || error); entry.state = 'error'; },
      );
      return { ticket };
    },
    poll(ticket) {
      const entry = tasks.get(ticket);
      if (!entry) return { state: 'error', error: 'unknown task' };
      if (entry.state === 'running') return { state: 'running' };
      tasks.delete(ticket);
      return entry.state === 'done' ? { state: 'done', json: entry.json } : { state: 'error', error: entry.error };
    },
  };

  util.reEscape = reEscape;
  if (root.top === root) {
    try { qdn.installFontHook(); } catch (e) { /* the reader still works; only scripted fonts are missed */ }
    try { mpia.installReaderHook(); } catch (e) { /* only the mpia reader observer is missed */ }
    try { ntk.installContentHook(); } catch (e) { /* ntk falls back to its own handshake */ }
  }
  root.__npiaHard = hard;
}(typeof window !== 'undefined' ? window : globalThis));
