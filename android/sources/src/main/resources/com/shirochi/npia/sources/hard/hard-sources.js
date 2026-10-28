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
  });

  const hard = { version: VERSION, qdn: {}, rbooks: {}, util: {} };
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
  // verified against screenshots for these exact font bytes bypass matching.
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
    };

    // --- resources -------------------------------------------------------------------------------

    const resourceNames = ['font', 'frequency', 'verified'];

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
        core = { digest, name, css: `"${name}"`, face, cover: new Map() };
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
        for (const [cipher, ch, mirrored, key] of todo) {
          const entries = verifiedEntries(cipher.core.digest);
          const verified = entries[`${ch.codePointAt(0).toString(16).toUpperCase()}:${mirrored ? 1 : 0}`];
          if (verified !== undefined) {
            state.glyphs.set(key, { character: verified, score: null, margin: null, candidates: [], reasons: [], method: 'verified_font_map' });
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
          shapeMatchedGlyphs: this.decoded - this.verified,
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
      };
    }

    return { putResource, resourceStatus, prepare, decode, decodeJson, knownFonts, status, parseUnicodeRange, parseCmap };
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
      st.diagnostics.push(st.isSerial ? `Chained episode links: ${st.ids.length}` : `Linked ebook volumes: ${st.ids.length}`);
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
    const fallbackTitle = (options && options.fallbackTitle) || '';
    const chapterIndex = String((options && options.chapterIndex) || 0).padStart(4, '0');
    const source = firstViewer();
    if (!source) return null;
    // Work on a clone: the page is reused for the next chapter.
    const content = source.cloneNode(true);
    // The viewer's top-bar headings show the series title only. The document title names the episode
    // and is server-rendered, so it is ready before the content.
    const pageTitle = (document.title || '').replace(/\s*[-|]\s*리디(?:북스)?\s*$/, '').trim();
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
  // Tasks: long operations (qdn.extract, qdn.decoder.decodeJson) are started and then polled, so no
  // single native script call has to outlive a platform's script timeout.
  //   tasks.start('qdn.extract', [options])            -> {ticket}
  //   tasks.poll(ticket)  -> {state: 'running'} | {state: 'done', json: '<JSON text of the result>'}
  //                          | {state: 'error', error: '...'}
  // The result comes back as JSON text so native code can pass it on without parsing it.
  // ---------------------------------------------------------------------------------------------

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
  }
  root.__npiaHard = hard;
}(typeof window !== 'undefined' ? window : globalThis));
