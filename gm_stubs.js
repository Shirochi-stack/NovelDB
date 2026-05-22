/**
 * gm_stubs.js - Tampermonkey/Greasemonkey API stubs for headless use.
 *
 * The novel-downloader bundle expects these globals to exist because it runs
 * as a userscript in the browser extension context. When we inject the bundle
 * into a plain QWebEngineView or Playwright page these are missing, so we
 * provide lightweight stubs that satisfy the initialisation checks.
 *
 * Injected BEFORE rules-lib.js.
 */

// unsafeWindow: in userscript managers this gives access to the page's real
// window object. In our headless context, we are already in the page context.
window.unsafeWindow = window;

// Wrap window.fetch with automatic retry for failed requests.
// The novel-downloader getHtmlDOM -> getText calls fetch() and throws
// "Bad response!" on non-ok status. Transient 403/429/5xx errors are common
// on sites like SFACG under concurrent load.
(function() {
  var _realFetch = window.fetch.bind(window);
  var MAX_RETRIES = 3;
  var RETRY_DELAYS = [1000, 2000, 3000];

  window.fetch = function(input, init) {
    var attempt = 0;
    function tryFetch() {
      return _realFetch(input, init).then(function(resp) {
        if (resp.ok || attempt >= MAX_RETRIES) {
          return resp;
        }
        var status = resp.status;
        if (status === 429 || status === 403 || status >= 500) {
          attempt++;
          var delay = RETRY_DELAYS[attempt - 1] || 3000;
          console.log('[ND-Fetch] Retry ' + attempt + '/' + MAX_RETRIES +
                      ' for ' + (typeof input === 'string' ? input : input.url) +
                      ' (HTTP ' + status + ') in ' + delay + 'ms');
          return new Promise(function(resolve) {
            setTimeout(resolve, delay);
          }).then(tryFetch);
        }
        return resp;
      });
    }
    return tryFetch();
  };
})();

window.GM_info = {
  script: {
    name: "novel-downloader",
    version: "5.2.0",
    description: "novel-downloader bridge mode",
    author: "BGME",
    namespace: "novel-downloader",
  },
  scriptHandler: "NpiaDownloader",
  version: "1.0.0",
  scriptMetaStr: "",
};

window.GM = window.GM || {};
window.GM.info = window.GM_info;

function _ndBase64ToArrayBuffer(base64) {
  var binary = atob(base64 || "");
  var bytes = new Uint8Array(binary.length);
  for (var i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

window.GM_xmlhttpRequest = function(details) {
  var url = details.url;
  if (url && url.indexOf('http://') === 0 && document.location.protocol === 'https:') {
    url = url.replace('http://', 'https://');
  }

  var nativeUrl = "";
  try {
    nativeUrl = new URL(url, document.location.href).hostname.toLowerCase();
  } catch (e) {}
  var useNativeTransport =
    typeof window.__npia_gm_xmlhttp_request === "function" &&
    nativeUrl === "api.sfacg.com";

  if (useNativeTransport) {
    var payload = {
      url: url,
      method: details.method || "GET",
      headers: details.headers || {},
      data: details.data || null,
      responseType: details.responseType || "",
      timeout: details.timeout || 30000,
      cookie: details.cookie || "",
    };
    window.__npia_gm_xmlhttp_request(payload)
      .then(function(obj) {
        if (!obj || obj.error) {
          if (details.onerror) {
            details.onerror({ error: obj && obj.error ? obj.error : "empty response" });
          }
          return;
        }

        var response = obj.responseText || "";
        if (obj.responseBase64) {
          var buffer = _ndBase64ToArrayBuffer(obj.responseBase64);
          response = details.responseType === "blob" ? new Blob([buffer]) : buffer;
        }

        if (details.onload) {
          details.onload({
            status: obj.status || 0,
            statusText: obj.statusText || "",
            responseText: obj.responseText || "",
            response: response,
            readyState: 4,
            responseHeaders: obj.responseHeaders || "",
            finalUrl: obj.finalUrl || url,
          });
        }
      })
      .catch(function(e) {
        if (details.onerror) details.onerror({ error: e.message || String(e) });
      });
    return;
  }

  var init = {
    method: details.method || "GET",
    headers: details.headers || {},
  };
  if (details.data) {
    init.body = details.data;
  }
  if (details.responseType === "arraybuffer" || details.responseType === "blob") {
    fetch(url, init)
      .then(function(r) {
        return details.responseType === "arraybuffer" ? r.arrayBuffer() : r.blob();
      })
      .then(function(data) {
        if (details.onload) {
          details.onload({
            status: 200,
            statusText: "OK",
            responseText: "",
            response: data,
            readyState: 4,
            responseHeaders: "",
            finalUrl: url,
          });
        }
      })
      .catch(function(e) {
        if (details.onerror) details.onerror({ error: e.message });
      });
  } else {
    fetch(url, init)
      .then(function(r) { return r.text().then(function(t) { return { r: r, t: t }; }); })
      .then(function(obj) {
        if (details.onload) {
          details.onload({
            status: obj.r.status,
            statusText: obj.r.statusText,
            responseText: obj.t,
            response: obj.t,
            readyState: 4,
            responseHeaders: "",
            finalUrl: url,
          });
        }
      })
      .catch(function(e) {
        if (details.onerror) details.onerror({ error: e.message });
      });
  }
};
window.GM.xmlHttpRequest = window.GM_xmlhttpRequest;

window._gm_storage = {};
window.GM_getValue = function(key, defaultValue) {
  return key in window._gm_storage ? window._gm_storage[key] : defaultValue;
};
window.GM_setValue = function(key, value) {
  window._gm_storage[key] = value;
};
window.GM_deleteValue = function(key) {
  delete window._gm_storage[key];
};
window.GM.getValue = function(key, defaultValue) {
  return Promise.resolve(window.GM_getValue(key, defaultValue));
};
window.GM.setValue = function(key, value) {
  window.GM_setValue(key, value);
  return Promise.resolve();
};
window.GM.deleteValue = function(key) {
  window.GM_deleteValue(key);
  return Promise.resolve();
};

window.workerId = "nd-bridge-" + Date.now();
window._gm_download = function() {};
window.GM_download = function() {};

window.localStorageExpired = {
  get: function() { return undefined; },
  set: function() {},
};

window.stopController = new AbortController();
window.stopFlag = window.stopController.signal;
window.downloading = false;
window.failedCount = 0;
window.__ND_BRIDGE_MODE = true;

if (typeof CryptoJS === "undefined") {
  window.CryptoJS = {
    enc: {
      Utf8: { parse: function(s) { return s; }, stringify: function(s) { return s; } },
      Base64: { parse: function(s) { return atob(s); }, stringify: function(s) { return btoa(s); } },
      Hex: { parse: function(s) { return s; }, stringify: function(s) { return s; } },
      Latin1: { parse: function(s) { return s; }, stringify: function(s) { return s; } },
    },
    AES: { decrypt: function() { return { toString: function() { return ""; } }; } },
    DES: { decrypt: function() { return { toString: function() { return ""; } }; } },
    MD5: function(s) { return s; },
    SHA256: function(s) { return s; },
    lib: { WordArray: { create: function() { return {}; } } },
    pad: { Pkcs7: {}, ZeroPadding: {}, NoPadding: {} },
    mode: { ECB: {}, CBC: {} },
  };
}

if (typeof fflate === "undefined") {
  window.fflate = {
    zipSync: function() { return new Uint8Array(); },
    strToU8: function(s) { return new TextEncoder().encode(s); },
    unzipSync: function() { return {}; },
  };
}

if (typeof nunjucks === "undefined") {
  function _NunjucksEnv(loaders, opts) {
    this.opts = Object.assign({ autoescape: true }, opts || {});
    this.opts.compilerOptions = this.opts.compilerOptions || {};
    this.filters = {};
    this.globals = {};
    this.loaders = loaders || [];
    this.extensions = {};
  }
  _NunjucksEnv.prototype.addFilter = function(name, fn) { this.filters[name] = fn; return this; };
  _NunjucksEnv.prototype.addGlobal = function(name, val) { this.globals[name] = val; return this; };
  _NunjucksEnv.prototype.getFilter = function(name) { return this.filters[name] || function(x) { return x; }; };
  _NunjucksEnv.prototype.addExtension = function(name, ext) { this.extensions[name] = ext; return this; };
  _NunjucksEnv.prototype.render = function(src, ctx, cb) { if (cb) cb(null, src); return src; };
  _NunjucksEnv.prototype.renderString = function(src, ctx, cb) { if (cb) cb(null, src); return src; };
  _NunjucksEnv.prototype.getTemplate = function(name) { return new _NunjucksTemplate("", this); };

  function _NunjucksTemplate(src, env, path, eagerCompile) {
    this.tmplStr = src || "";
    this.env = env || new _NunjucksEnv();
    this.path = path;
  }
  _NunjucksTemplate.prototype.render = function(ctx, cb) {
    if (cb) { cb(null, this.tmplStr); }
    return this.tmplStr;
  };
  _NunjucksTemplate.prototype.compile = function() {};

  window.nunjucks = {
    Environment: _NunjucksEnv,
    Template: _NunjucksTemplate,
    renderString: function(tpl) { return tpl; },
    configure: function() {},
  };
}

if (typeof Vue === "undefined") {
  var _vueApp = {
    component: function() { return _vueApp; },
    directive: function() { return _vueApp; },
    mount: function() { return _vueApp; },
    use: function() { return _vueApp; },
    provide: function() { return _vueApp; },
    mixin: function() { return _vueApp; },
    config: { globalProperties: {}, compilerOptions: {} },
  };
  window.Vue = {
    createApp: function() { return _vueApp; },
    reactive: function(o) { return o; },
    ref: function(v) { return { value: v }; },
    computed: function() { return { value: undefined }; },
    watch: function() { return function() {}; },
    watchEffect: function() { return function() {}; },
    onMounted: function() {},
    onUnmounted: function() {},
    onBeforeMount: function() {},
    onBeforeUnmount: function() {},
    onUpdated: function() {},
    onBeforeUpdate: function() {},
    nextTick: function(fn) { if (fn) fn(); return Promise.resolve(); },
    defineComponent: function(opts) { return opts; },
    defineCustomElement: function() { return function() {}; },
    h: function() { return {}; },
    toRaw: function(o) { return o; },
    markRaw: function(o) { return o; },
    shallowRef: function(v) { return { value: v }; },
    shallowReactive: function(o) { return o; },
    inject: function() { return undefined; },
    provide: function() {},
    toRef: function(obj, key) { return { value: obj ? obj[key] : undefined }; },
    toRefs: function(obj) { var r = {}; for (var k in obj) r[k] = { value: obj[k] }; return r; },
    isRef: function() { return false; },
    unref: function(v) { return v && v.value !== undefined ? v.value : v; },
    triggerRef: function() {},
    customRef: function() { return { value: undefined }; },
    getCurrentInstance: function() { return null; },
  };
  if (typeof Proxy !== "undefined") {
    window.Vue = new Proxy(window.Vue, {
      get: function(target, prop) {
        if (prop in target) return target[prop];
        return function() { return {}; };
      }
    });
  }
}

if (typeof eSearchOCR === "undefined") {
  window.eSearchOCR = {};
}

if (typeof ort === "undefined") {
  window.ort = {};
}

console.log("[ND-Bridge] GM stubs loaded");
