/**
 * reader_host.js - entry points ReaderRuntime calls from Swift.
 *
 * Loaded after bridge.js. Swift calls __npia_call(token, name, argsJson);
 * the result goes back through __npia_native.settle(token, ok, text).
 */
(function (global) {
  "use strict";

  var env = global.__npia_env;
  var dom = global.__npia_dom;
  var web = global.__npia_web;
  var native = env.native;

  var NAVIGATION_HEADERS = {
    accept: "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "user-agent": env.DESKTOP_UA,
    "upgrade-insecure-requests": "1",
  };

  // The desktop splits reader HTML on these tags (_reader_html_blocks).
  var BLOCK_TAGS = { p: 1, div: 1, li: 1, blockquote: 1, h1: 1, h2: 1, h3: 1, h4: 1, h5: 1, h6: 1, table: 1, tr: 1, section: 1, article: 1, center: 1, pre: 1 };
  var DROP_TAGS = { script: 1, style: 1, noscript: 1, button: 1 };

  function errorText(e) {
    if (e == null) return "unknown error";
    if (typeof e === "string") return e;
    if (e.message) return e.message;
    if (e.error) return String(e.error);
    try {
      return JSON.stringify(e);
    } catch (x) {
      return String(e);
    }
  }

  function sniffCharset(headers, b64) {
    var type = (headers && (headers["content-type"] || headers["Content-Type"])) || "";
    var m = /charset=["']?([\w.:-]+)/i.exec(type);
    if (m) return m[1].toLowerCase();
    var head = env.bytesToLatin1(env.base64ToBytes(String(b64 || "").slice(0, 5464)));
    if (head.charCodeAt(0) === 0xef && head.charCodeAt(1) === 0xbb && head.charCodeAt(2) === 0xbf) return "utf-8";
    m = /<meta[^>]+charset\s*=\s*["']?\s*([\w.:-]+)/i.exec(head);
    return m ? m[1].toLowerCase() : "utf-8";
  }

  function runInlineScripts(doc, maxLength) {
    var scripts = doc.querySelectorAll("script");
    var ran = 0;
    global.__npia_dropTimers(true);
    try {
      for (var i = 0; i < scripts.length; i++) {
        var script = scripts[i];
        if (script.hasAttribute("src")) continue;
        var type = (script.getAttribute("type") || "").toLowerCase();
        if (type && !/^(text|application)\/(x-)?(java|ecma)script$/.test(type) && type !== "module") continue;
        if (type === "module") continue;
        var code = script.textContent;
        if (!code.trim() || code.length > maxLength) continue;
        try {
          (0, eval)(code);
          ran++;
        } catch (e) {
          // Page scripts expect the full browser; ignore the ones that fail.
        }
      }
    } finally {
      global.__npia_dropTimers(false);
    }
    return ran;
  }

  function loadPage(url, html, charset, options) {
    var doc = web.setPage(url, html, charset);
    if (!options || options.runScripts !== false) runInlineScripts(doc, (options && options.maxScriptLength) || 20000);
    return doc;
  }

  function fetchPage(url) {
    return web.nativeRequest(url, "GET", NAVIGATION_HEADERS, null, 30000).then(function (result) {
      if (result.status >= 400) throw new Error("HTTP " + result.status + " for " + url);
      var charset = sniffCharset(result.headers, result.body);
      var html = native.decode(result.body || "", charset);
      return { url: result.url || url, html: html == null ? "" : String(html), charset: charset };
    });
  }

  function ensureRules() {
    if (typeof global.__ND_parseBook !== "function") throw new Error("bridge.js is not loaded");
    if (!global.__ND_READY || typeof global.__ND_getRule !== "function") throw new Error("rules-lib.js did not initialise");
  }

  /** Splits rule output into ordered [kind, value] blocks, like _reader_html_blocks. */
  function htmlBlocks(html, baseUrl, attachments) {
    baseUrl = env.resolveURL(baseUrl || "", global.location.href) || global.location.href;
    var holder = dom.parseDocument("<body></body>", { url: baseUrl || "about:blank" }).body;
    holder.innerHTML = html || "";
    var blocks = [];
    var line = [];

    function flush() {
      var text = line.join("").replace(/ /g, " ").replace(/[ \t\r\f\v]+/g, " ").trim();
      line.length = 0;
      if (text) blocks.push(["text", text]);
    }

    function walk(node) {
      for (var i = 0; i < node.childNodes.length; i++) {
        var child = node.childNodes[i];
        if (child.nodeType === 8) continue;
        if (child.nodeType === 3 || child.nodeType === 4) {
          var parts = child.data.split("\n");
          for (var k = 0; k < parts.length; k++) {
            if (k) flush();
            line.push(parts[k]);
          }
          continue;
        }
        if (child.nodeType !== 1) continue;
        var name = child.localName;
        if (DROP_TAGS[name]) continue;
        if (name === "br") flush();
        else if (name === "img") {
          // cleanDOM swaps src for data-src-address, the attachment's name.
          var address = child.getAttribute("data-src-address");
          var src = ((address && attachments && attachments[address]) || child.getAttribute("data-src") || child.getAttribute("data-original") || child.getAttribute("src") || "").trim();
          var url = src ? env.resolveURL(src, baseUrl) : "";
          if (url && /^https?:\/\//i.test(url)) {
            flush();
            blocks.push(["img", url]);
          }
        } else if (BLOCK_TAGS[name]) {
          flush();
          walk(child);
          flush();
        } else walk(child);
      }
    }

    walk(holder);
    flush();
    return blocks;
  }

  function plainBlocks(text) {
    return String(text || "")
      .replace(/\r\n/g, "\n")
      .split("\n")
      .map(function (line) {
        return line.replace(/ /g, " ").trim();
      })
      .filter(Boolean)
      .map(function (line) {
        return ["text", line];
      });
  }

  var HOST = {
    /** True when a rule claims `url`; false when getRule throws "Not Found Rule!". */
    hasRule: function (url) {
      ensureRules();
      loadPage(url, "<html><head></head><body></body></html>", "utf-8", { runScripts: false });
      return Promise.resolve(global.__ND_getRule()).then(
        function () {
          return true;
        },
        function (e) {
          return !/Not Found Rule/i.test(errorText(e));
        }
      );
    },

    /** Loads the book page (fetching it unless `html` is given) and runs bookParse. */
    openBook: function (url, html, charset, options) {
      ensureRules();
      var page = html != null ? Promise.resolve({ url: url, html: html, charset: charset || "utf-8" }) : fetchPage(url);
      return page
        .then(function (loaded) {
          loadPage(loaded.url, loaded.html, loaded.charset, options);
          return global.__ND_parseBook();
        })
        .then(function (json) {
          var data = JSON.parse(json);
          if (data.error) throw new Error(data.error);
          data.pageUrl = global.location.href;
          data.charset = global.document.characterSet;
          return data;
        });
    },

    /** Runs the rule's chapterParse for one chapter and returns text/image blocks. */
    parseChapter: function (url, name, isVIP, isPaid) {
      ensureRules();
      if (!global.__ND_ruleInstance) throw new Error("No book is open");
      return global.__ND_parseChapter(url, name, !!isVIP, !!isPaid).then(function (json) {
        var data = JSON.parse(json);
        if (data.error) throw new Error(data.error);
        var attachments = {};
        (data.images || []).forEach(function (image) {
          if (image && image.name && image.url) attachments[image.name] = image.url;
        });
        var blocks = htmlBlocks(data.contentHtml, url, attachments);
        if (!blocks.length && data.contentText) blocks = plainBlocks(data.contentText);
        return {
          chapterName: data.chapterName || name || "",
          blocks: blocks,
          textLength: (data.contentText || "").length,
        };
      });
    },

    htmlBlocks: function (html, baseUrl, attachments) {
      return htmlBlocks(html, baseUrl, attachments);
    },

    diagnostics: function () {
      return global.__ND_getDiagnostics().then(JSON.parse);
    },
  };

  global.__npia_host = HOST;

  global.__npia_call = function (token, name, argsJson) {
    var settled = false;
    function done(ok, value) {
      if (settled) return;
      settled = true;
      native.settle(token, ok, value);
    }
    var promise;
    try {
      var fn = HOST[name];
      if (!fn) throw new Error("Unknown host function " + name);
      promise = Promise.resolve(fn.apply(null, JSON.parse(argsJson || "[]")));
    } catch (e) {
      promise = Promise.reject(e);
    }
    promise.then(
      function (value) {
        done(true, typeof value === "string" ? value : JSON.stringify(value === undefined ? null : value));
      },
      function (e) {
        done(false, errorText(e));
      }
    );
  };
})(this);
