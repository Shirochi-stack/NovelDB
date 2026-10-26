/**
 * reader_web.js - window, location, fetch, Blob, XMLHttpRequest, storage.
 *
 * Every request goes to Swift through __npia_native.fetch, which sends it
 * with the app's HTTPFetching client (shared cookies, pacing, retries).
 */
(function (global) {
  "use strict";

  var env = global.__npia_env;
  var dom = global.__npia_dom;
  var native = env.native;

  // ------------------------------------------------------------------
  // Location and page document
  // ------------------------------------------------------------------

  function Location(href) {
    this._url = new URL(href);
  }
  ["href", "protocol", "host", "hostname", "port", "pathname", "search", "hash", "origin"].forEach(function (key) {
    Object.defineProperty(Location.prototype, key, {
      get: function () {
        return this._url[key];
      },
      set: function (v) {
        // Navigation is not supported; keep the page where it is.
        console.warn("[Reader] Ignored navigation: location." + key + " = " + v);
      },
    });
  });
  Location.prototype.assign = Location.prototype.replace = function (url) {
    console.warn("[Reader] Ignored navigation to " + url);
  };
  Location.prototype.reload = function () {};
  Location.prototype.toString = function () {
    return this.href;
  };
  Location.prototype.ancestorOrigins = [];

  var pageLocation = new Location("about:blank");
  Object.defineProperty(global, "location", {
    get: function () {
      return pageLocation;
    },
    set: function (v) {
      console.warn("[Reader] Ignored navigation to " + v);
    },
    configurable: true,
  });

  /** Replaces the page document, as if the browser had navigated to `url`. */
  function setPage(url, html, charset) {
    pageLocation = new Location(url);
    var doc = dom.parseDocument(html, {
      url: pageLocation.href,
      rendered: true,
      charset: charset ? String(charset).toUpperCase().replace(/^UTF8$/, "UTF-8") : "UTF-8",
      window: global,
      location: pageLocation,
    });
    global.document = doc;
    history.length = 1;
    return doc;
  }

  var history = {
    length: 1,
    state: null,
    scrollRestoration: "auto",
    back: function () {},
    forward: function () {},
    go: function () {},
    pushState: function (state) {
      this.state = state;
    },
    replaceState: function (state) {
      this.state = state;
    },
  };
  global.history = history;
  global.document = dom.createHTMLDocument("about:blank", { rendered: true, window: global, location: pageLocation });

  // ------------------------------------------------------------------
  // navigator, screen, misc window members
  // ------------------------------------------------------------------

  global.navigator = {
    userAgent: env.DESKTOP_UA,
    appVersion: env.DESKTOP_UA.replace(/^Mozilla\//, ""),
    appName: "Netscape",
    appCodeName: "Mozilla",
    product: "Gecko",
    vendor: "Google Inc.",
    platform: "Win32",
    language: "zh-CN",
    languages: ["zh-CN", "zh", "en-US", "en"],
    cookieEnabled: true,
    onLine: true,
    doNotTrack: null,
    hardwareConcurrency: 4,
    maxTouchPoints: 0,
    webdriver: false,
    plugins: [],
    mimeTypes: [],
    userAgentData: { brands: [{ brand: "Chromium", version: "121" }], mobile: false, platform: "Windows" },
    sendBeacon: function () {
      return true;
    },
    javaEnabled: function () {
      return false;
    },
    clipboard: { writeText: function () {
      return Promise.resolve();
    }, readText: function () {
      return Promise.resolve("");
    } },
    permissions: { query: function () {
      return Promise.resolve({ state: "denied" });
    } },
    storage: { estimate: function () {
      return Promise.resolve({ quota: 0, usage: 0 });
    }, persist: function () {
      return Promise.resolve(false);
    } },
  };
  global.screen = { width: 1920, height: 1080, availWidth: 1920, availHeight: 1040, colorDepth: 24, pixelDepth: 24, orientation: { type: "landscape-primary", angle: 0 } };
  global.innerWidth = 1280;
  global.innerHeight = 800;
  global.outerWidth = 1280;
  global.outerHeight = 880;
  global.devicePixelRatio = 1;
  global.scrollX = global.scrollY = global.pageXOffset = global.pageYOffset = 0;
  global.name = "";
  global.closed = false;
  global.isSecureContext = true;
  global.origin = "null";
  Object.defineProperty(global, "origin", {
    get: function () {
      return pageLocation.origin;
    },
    configurable: true,
  });
  global.frameElement = null;
  global.opener = null;
  global.length = 0;
  global.scrollTo = global.scroll = global.scrollBy = function () {};
  global.focus = global.blur = global.print = global.stop = function () {};
  global.alert = function (message) {
    console.warn("[Reader] alert: " + message);
  };
  global.confirm = function () {
    return false;
  };
  global.prompt = function () {
    return null;
  };
  global.open = function () {
    return null;
  };
  global.close = function () {};
  global.postMessage = function () {};
  global.matchMedia = function (query) {
    return { matches: false, media: String(query), onchange: null, addListener: function () {}, removeListener: function () {}, addEventListener: function () {}, removeEventListener: function () {} };
  };
  global.getSelection = function () {
    return null;
  };
  global.performance = {
    timeOrigin: Date.now(),
    now: function () {
      return Date.now() - this.timeOrigin;
    },
    mark: function () {},
    measure: function () {},
    getEntriesByType: function () {
      return [];
    },
    getEntriesByName: function () {
      return [];
    },
  };
  global.structuredClone = function (value) {
    return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  };

  function NoopObserver(callback) {
    this._callback = callback;
  }
  NoopObserver.prototype.observe = function () {};
  NoopObserver.prototype.unobserve = function () {};
  NoopObserver.prototype.disconnect = function () {};
  NoopObserver.prototype.takeRecords = function () {
    return [];
  };
  global.MutationObserver = global.WebKitMutationObserver = NoopObserver;
  global.IntersectionObserver = NoopObserver;
  global.ResizeObserver = NoopObserver;
  global.PerformanceObserver = NoopObserver;
  global.CSS = { supports: function () {
    return false;
  }, escape: function (s) {
    return String(s).replace(/([^\w-])/g, "\\$1");
  } };
  global.customElements = {
    define: function () {},
    get: function () {
      return undefined;
    },
    whenDefined: function () {
      return Promise.resolve();
    },
  };
  global.BroadcastChannel = function () {
    this.postMessage = function () {};
    this.close = function () {};
    this.addEventListener = function () {};
  };

  // ------------------------------------------------------------------
  // Storage
  // ------------------------------------------------------------------

  function Storage() {
    Object.defineProperty(this, "_data", { value: Object.create(null), writable: true });
  }
  Storage.prototype = {
    get length() {
      return Object.keys(this._data).length;
    },
    key: function (i) {
      return Object.keys(this._data)[i] || null;
    },
    getItem: function (key) {
      key = String(key);
      return key in this._data ? this._data[key] : null;
    },
    setItem: function (key, value) {
      this._data[String(key)] = String(value);
    },
    removeItem: function (key) {
      delete this._data[String(key)];
    },
    clear: function () {
      this._data = Object.create(null);
    },
  };
  global.Storage = Storage;
  global.localStorage = new Storage();
  global.sessionStorage = new Storage();

  // ------------------------------------------------------------------
  // Crypto
  // ------------------------------------------------------------------

  global.crypto = {
    getRandomValues: function (array) {
      for (var i = 0; i < array.length; i++) array[i] = Math.floor(Math.random() * 4294967296);
      return array;
    },
    randomUUID: function () {
      return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
        var r = Math.floor(Math.random() * 16);
        return (c === "x" ? r : (r & 3) | 8).toString(16);
      });
    },
    subtle: {
      digest: function (algorithm, data) {
        var name = typeof algorithm === "string" ? algorithm : algorithm && algorithm.name;
        try {
          var out = native.digest(String(name).toUpperCase(), env.base64Of(env.toBytes(data)));
          if (out == null) return Promise.reject(new Error("Unsupported digest: " + name));
          return Promise.resolve(env.base64ToBytes(String(out)).buffer);
        } catch (e) {
          return Promise.reject(e);
        }
      },
    },
  };

  // ------------------------------------------------------------------
  // Blob, File, FileReader, FormData
  // ------------------------------------------------------------------

  function Blob(parts, options) {
    var chunks = [];
    var total = 0;
    (parts || []).forEach(function (part) {
      var bytes = part instanceof Blob ? part._bytes : typeof part === "string" ? env.utf8Encode(part) : env.toBytes(part);
      chunks.push(bytes);
      total += bytes.length;
    });
    var bytes;
    if (chunks.length === 1) bytes = chunks[0];
    else {
      bytes = new Uint8Array(total);
      var offset = 0;
      chunks.forEach(function (c) {
        bytes.set(c, offset);
        offset += c.length;
      });
    }
    this._bytes = bytes;
    this.type = options && options.type ? String(options.type).toLowerCase() : "";
  }
  Blob.prototype = {
    constructor: Blob,
    get size() {
      return this._bytes.length;
    },
    arrayBuffer: function () {
      var b = this._bytes;
      return Promise.resolve(b.byteOffset === 0 && b.byteLength === b.buffer.byteLength ? b.buffer : b.slice().buffer);
    },
    bytes: function () {
      return Promise.resolve(new Uint8Array(this._bytes));
    },
    text: function () {
      return Promise.resolve(env.decodeBytes(this._bytes, "utf-8"));
    },
    slice: function (start, end, type) {
      var blob = new Blob([], { type: type || "" });
      blob._bytes = this._bytes.slice(start || 0, end === undefined ? this._bytes.length : end);
      return blob;
    },
    stream: function () {
      throw new Error("Blob streams are not supported");
    },
  };
  function File(parts, name, options) {
    Blob.call(this, parts, options);
    this.name = String(name);
    this.lastModified = (options && options.lastModified) || Date.now();
  }
  File.prototype = Object.create(Blob.prototype);
  File.prototype.constructor = File;
  global.Blob = Blob;
  global.File = File;

  function FileReader() {
    this.readyState = 0;
    this.result = null;
    this.error = null;
    this.onload = this.onloadend = this.onerror = this.onloadstart = this.onprogress = this.onabort = null;
  }
  FileReader.EMPTY = 0;
  FileReader.LOADING = 1;
  FileReader.DONE = 2;
  FileReader.prototype = Object.create(EventTarget.prototype);
  FileReader.prototype.constructor = FileReader;
  FileReader.prototype._read = function (blob, convert) {
    var self = this;
    this.readyState = 1;
    Promise.resolve().then(function () {
      try {
        self.result = convert(blob);
        self.readyState = 2;
        self.dispatchEvent(new Event("load"));
      } catch (e) {
        self.error = e;
        self.readyState = 2;
        self.dispatchEvent(new Event("error"));
      }
      self.dispatchEvent(new Event("loadend"));
    });
  };
  FileReader.prototype.readAsArrayBuffer = function (blob) {
    this._read(blob, function (b) {
      return b._bytes.slice().buffer;
    });
  };
  FileReader.prototype.readAsText = function (blob, charset) {
    this._read(blob, function (b) {
      return env.decodeBytes(b._bytes, charset || "utf-8");
    });
  };
  FileReader.prototype.readAsDataURL = function (blob) {
    this._read(blob, function (b) {
      return "data:" + (b.type || "application/octet-stream") + ";base64," + env.bytesToBase64(b._bytes);
    });
  };
  FileReader.prototype.readAsBinaryString = function (blob) {
    this._read(blob, function (b) {
      return env.bytesToLatin1(b._bytes);
    });
  };
  FileReader.prototype.abort = function () {};
  global.FileReader = FileReader;

  function FormData(form) {
    this._list = [];
    if (form && form.querySelectorAll) {
      var self = this;
      form.querySelectorAll("input[name], select[name], textarea[name]").forEach(function (el) {
        var type = (el.getAttribute("type") || "").toLowerCase();
        if ((type === "checkbox" || type === "radio") && !el.hasAttribute("checked")) return;
        self._list.push([el.getAttribute("name"), el.value]);
      });
    }
  }
  FormData.prototype = {
    append: function (name, value, filename) {
      this._list.push([String(name), value instanceof Blob ? value : String(value), filename]);
    },
    set: function (name, value, filename) {
      this.delete(name);
      this.append(name, value, filename);
    },
    delete: function (name) {
      this._list = this._list.filter(function (p) {
        return p[0] !== String(name);
      });
    },
    get: function (name) {
      for (var i = 0; i < this._list.length; i++) if (this._list[i][0] === String(name)) return this._list[i][1];
      return null;
    },
    getAll: function (name) {
      return this._list
        .filter(function (p) {
          return p[0] === String(name);
        })
        .map(function (p) {
          return p[1];
        });
    },
    has: function (name) {
      return this.get(name) !== null;
    },
    forEach: function (fn, thisArg) {
      var self = this;
      this._list.forEach(function (p) {
        fn.call(thisArg, p[1], p[0], self);
      });
    },
    entries: function () {
      return this._list.map(function (p) {
        return [p[0], p[1]];
      })[Symbol.iterator]();
    },
    keys: function () {
      return this._list.map(function (p) {
        return p[0];
      })[Symbol.iterator]();
    },
    values: function () {
      return this._list.map(function (p) {
        return p[1];
      })[Symbol.iterator]();
    },
  };
  FormData.prototype[Symbol.iterator] = FormData.prototype.entries;
  global.FormData = FormData;

  // ------------------------------------------------------------------
  // Headers, Request, Response
  // ------------------------------------------------------------------

  function Headers(init) {
    this._map = Object.create(null);
    if (!init) return;
    var self = this;
    if (init instanceof Headers) init.forEach(function (v, k) {
      self.append(k, v);
    });
    else if (Array.isArray(init)) init.forEach(function (p) {
      self.append(p[0], p[1]);
    });
    else if (typeof init[Symbol.iterator] === "function") Array.from(init).forEach(function (p) {
      self.append(p[0], p[1]);
    });
    else for (var key in init) if (Object.prototype.hasOwnProperty.call(init, key)) this.append(key, init[key]);
  }
  Headers.prototype = {
    append: function (name, value) {
      name = String(name).toLowerCase();
      value = String(value);
      this._map[name] = name in this._map ? this._map[name] + ", " + value : value;
    },
    set: function (name, value) {
      this._map[String(name).toLowerCase()] = String(value);
    },
    get: function (name) {
      var v = this._map[String(name).toLowerCase()];
      return v === undefined ? null : v;
    },
    has: function (name) {
      return String(name).toLowerCase() in this._map;
    },
    delete: function (name) {
      delete this._map[String(name).toLowerCase()];
    },
    forEach: function (fn, thisArg) {
      var self = this;
      Object.keys(this._map)
        .sort()
        .forEach(function (k) {
          fn.call(thisArg, self._map[k], k, self);
        });
    },
    entries: function () {
      var self = this;
      return Object.keys(this._map)
        .sort()
        .map(function (k) {
          return [k, self._map[k]];
        })[Symbol.iterator]();
    },
    keys: function () {
      return Object.keys(this._map).sort()[Symbol.iterator]();
    },
    values: function () {
      var self = this;
      return Object.keys(this._map)
        .sort()
        .map(function (k) {
          return self._map[k];
        })[Symbol.iterator]();
    },
    getSetCookie: function () {
      return [];
    },
    _object: function () {
      var out = {};
      for (var k in this._map) out[k] = this._map[k];
      return out;
    },
  };
  Headers.prototype[Symbol.iterator] = Headers.prototype.entries;
  global.Headers = Headers;

  var STATUS_TEXT = { 200: "OK", 201: "Created", 204: "No Content", 301: "Moved Permanently", 302: "Found", 304: "Not Modified", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 429: "Too Many Requests", 500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable" };

  function bodyBytes(body) {
    if (body == null) return null;
    if (typeof body === "string") return env.utf8Encode(body);
    if (body instanceof URLSearchParams) return env.utf8Encode(body.toString());
    if (body instanceof Blob) return body._bytes;
    if (body instanceof FormData) return null;
    return env.toBytes(body);
  }

  function Response(body, init) {
    init = init || {};
    this.status = init.status === undefined ? 200 : init.status;
    this.statusText = init.statusText !== undefined ? String(init.statusText) : STATUS_TEXT[this.status] || "";
    this.headers = init.headers instanceof Headers ? init.headers : new Headers(init.headers);
    this.url = init.url || "";
    this.redirected = !!init.redirected;
    this.type = init.type || "basic";
    this.bodyUsed = false;
    this._b64 = init._b64 || null;
    this._bytes = this._b64 ? null : bodyBytes(body) || new Uint8Array(0);
    if (body instanceof Blob && body.type && !this.headers.has("content-type")) this.headers.set("content-type", body.type);
    if (typeof body === "string" && !this.headers.has("content-type")) this.headers.set("content-type", "text/plain;charset=UTF-8");
  }
  Response.prototype = {
    get ok() {
      return this.status >= 200 && this.status < 300;
    },
    get body() {
      return null;
    },
    _consume: function () {
      if (this.bodyUsed) return Promise.reject(new TypeError("Failed to execute: body stream already read"));
      this.bodyUsed = true;
      return Promise.resolve();
    },
    _getBytes: function () {
      if (!this._bytes) this._bytes = env.bytesFromBase64Cached(this._b64);
      return this._bytes;
    },
    text: function () {
      var self = this;
      return this._consume().then(function () {
        if (self._b64 && !self._bytes) {
          var text = native.decode(self._b64, "utf-8");
          return text == null ? "" : String(text).replace(/^﻿/, "");
        }
        return env.decodeBytes(self._getBytes(), "utf-8").replace(/^﻿/, "");
      });
    },
    json: function () {
      return this.text().then(JSON.parse);
    },
    arrayBuffer: function () {
      var self = this;
      return this._consume().then(function () {
        var b = self._getBytes();
        return b.byteOffset === 0 && b.byteLength === b.buffer.byteLength ? b.buffer : b.slice().buffer;
      });
    },
    bytes: function () {
      var self = this;
      return this._consume().then(function () {
        return self._getBytes();
      });
    },
    blob: function () {
      var self = this;
      return this._consume().then(function () {
        var blob = new Blob([], { type: (self.headers.get("content-type") || "").split(";")[0] });
        blob._bytes = self._getBytes();
        return blob;
      });
    },
    formData: function () {
      return this.text().then(function (text) {
        var fd = new FormData();
        new URLSearchParams(text).forEach(function (v, k) {
          fd.append(k, v);
        });
        return fd;
      });
    },
    clone: function () {
      if (this.bodyUsed) throw new TypeError("Response body is already used");
      var copy = new Response(null, { status: this.status, statusText: this.statusText, headers: new Headers(this.headers), url: this.url, redirected: this.redirected, _b64: this._b64 });
      if (!this._b64) copy._bytes = this._bytes;
      return copy;
    },
  };
  Response.error = function () {
    return new Response(null, { status: 0, type: "error" });
  };
  Response.json = function (data, init) {
    init = init || {};
    var headers = new Headers(init.headers);
    headers.set("content-type", "application/json");
    return new Response(JSON.stringify(data), { status: init.status, headers: headers });
  };
  Response.redirect = function (url, status) {
    return new Response(null, { status: status || 302, headers: { location: url } });
  };
  global.Response = Response;

  function Request(input, init) {
    init = init || {};
    if (input instanceof Request) {
      this.url = input.url;
      this.method = input.method;
      this.headers = new Headers(input.headers);
      this._body = input._body;
      this.signal = input.signal;
      this.referrer = input.referrer;
      this.referrerPolicy = input.referrerPolicy;
    } else {
      this.url = env.resolveURL(String(input), document.baseURI) || String(input);
      this.method = "GET";
      this.headers = new Headers();
      this._body = null;
      this.signal = null;
      this.referrer = "about:client";
      this.referrerPolicy = "";
    }
    if (init.method) this.method = String(init.method).toUpperCase();
    if (init.headers) this.headers = new Headers(init.headers);
    if (init.body !== undefined) this._body = init.body;
    if (init.signal) this.signal = init.signal;
    if (init.referrer !== undefined) this.referrer = init.referrer;
    if (init.referrerPolicy !== undefined) this.referrerPolicy = init.referrerPolicy;
    this.credentials = init.credentials || "same-origin";
    this.mode = init.mode || "cors";
    this.cache = init.cache || "default";
    this.redirect = init.redirect || "follow";
  }
  Request.prototype.clone = function () {
    return new Request(this);
  };
  Request.prototype.text = function () {
    return Promise.resolve(typeof this._body === "string" ? this._body : "");
  };
  global.Request = Request;

  // ------------------------------------------------------------------
  // fetch
  // ------------------------------------------------------------------

  var pendingFetches = {};
  var fetchSeq = 0;

  function multipart(formData) {
    var boundary = "----NpiaFormBoundary" + Math.random().toString(16).slice(2);
    var parts = [];
    formData._list.forEach(function (p) {
      var head = "--" + boundary + '\r\nContent-Disposition: form-data; name="' + p[0] + '"';
      if (p[1] instanceof Blob) {
        head += '; filename="' + (p[2] || p[1].name || "blob") + '"\r\nContent-Type: ' + (p[1].type || "application/octet-stream");
        parts.push(head + "\r\n\r\n", p[1], "\r\n");
      } else parts.push(head + "\r\n\r\n" + p[1] + "\r\n");
    });
    parts.push("--" + boundary + "--\r\n");
    return { blob: new Blob(parts), type: "multipart/form-data; boundary=" + boundary };
  }

  function dataURLResponse(url) {
    var m = /^data:([^,]*?)(;base64)?,(.*)$/s.exec(url);
    if (!m) return Promise.reject(new TypeError("Failed to fetch"));
    var bytes = m[2] ? env.base64ToBytes(m[3]) : env.utf8Encode(decodeURIComponent(m[3]));
    var blob = new Blob([bytes], { type: m[1] || "text/plain" });
    return Promise.resolve(new Response(blob, { status: 200, url: url, headers: { "content-type": m[1] || "text/plain" } }));
  }

  /** Sends a request through Swift; resolves {status, url, headers, body base64}. */
  function nativeRequest(url, method, headers, bodyB64, timeoutMs) {
    return new Promise(function (resolve, reject) {
      var id = ++fetchSeq;
      pendingFetches[id] = { resolve: resolve, reject: reject };
      native.fetch(
        JSON.stringify({ id: id, url: url, method: method, headers: headers, body: bodyB64, timeout: timeoutMs || 30000 })
      );
    });
  }

  global.__npia_fetchDone = function (id, json) {
    var pending = pendingFetches[id];
    if (!pending) return;
    delete pendingFetches[id];
    var result;
    try {
      result = JSON.parse(json);
    } catch (e) {
      pending.reject(new TypeError("Failed to fetch"));
      return;
    }
    if (result.error) pending.reject(new TypeError("Failed to fetch: " + result.error));
    else pending.resolve(result);
  };

  function fetch(input, init) {
    var request;
    try {
      request = new Request(input, init);
    } catch (e) {
      return Promise.reject(e);
    }
    var url = request.url;
    if (/^data:/i.test(url)) return dataURLResponse(url);
    if (/^blob:/i.test(url)) {
      var stored = env.blobStore[url];
      return stored ? Promise.resolve(new Response(stored, { status: 200, url: url })) : Promise.reject(new TypeError("Failed to fetch"));
    }
    if (!/^https?:/i.test(url)) return Promise.reject(new TypeError("Failed to fetch: unsupported URL " + url));
    var signal = request.signal;
    if (signal && signal.aborted) return Promise.reject(signal.reason || DOMException("The user aborted a request.", "AbortError"));

    var headers = request.headers._object();
    var body = request._body;
    if (body instanceof FormData) {
      var mp = multipart(body);
      body = mp.blob;
      if (!headers["content-type"]) headers["content-type"] = mp.type;
    } else if (body instanceof URLSearchParams && !headers["content-type"]) {
      headers["content-type"] = "application/x-www-form-urlencoded;charset=UTF-8";
    } else if (typeof body === "string" && !headers["content-type"]) {
      headers["content-type"] = "text/plain;charset=UTF-8";
    }
    var bytes = bodyBytes(body);
    if (!headers["user-agent"]) headers["user-agent"] = env.DESKTOP_UA;
    if (!headers["accept"]) headers["accept"] = "*/*";
    if (!headers["accept-language"]) headers["accept-language"] = "zh-CN,zh;q=0.9,en;q=0.8";
    if (!headers["referer"] && request.referrerPolicy !== "no-referrer" && request.referrer !== "") {
      var ref = request.referrer && request.referrer !== "about:client" ? request.referrer : pageLocation.href;
      if (/^https?:/.test(ref)) headers["referer"] = ref;
    }

    var promise = nativeRequest(url, request.method, headers, bytes && bytes.length ? env.base64Of(bytes) : null).then(function (result) {
      return new Response(null, {
        status: result.status,
        statusText: result.statusText || STATUS_TEXT[result.status] || "",
        headers: result.headers || {},
        url: result.url || url,
        redirected: !!result.url && result.url !== url,
        _b64: result.body || "",
      });
    });
    if (!signal) return promise;
    return new Promise(function (resolve, reject) {
      signal.addEventListener("abort", function () {
        reject(signal.reason || DOMException("The user aborted a request.", "AbortError"));
      });
      promise.then(resolve, reject);
    });
  }
  global.fetch = fetch;

  // ------------------------------------------------------------------
  // XMLHttpRequest
  // ------------------------------------------------------------------

  function XMLHttpRequest() {
    this.readyState = 0;
    this.status = 0;
    this.statusText = "";
    this.response = null;
    this.responseText = "";
    this.responseType = "";
    this.responseURL = "";
    this.responseXML = null;
    this.timeout = 0;
    this.withCredentials = false;
    this.upload = new EventTarget();
    this._headers = {};
    this._respHeaders = null;
  }
  XMLHttpRequest.UNSENT = 0;
  XMLHttpRequest.OPENED = 1;
  XMLHttpRequest.HEADERS_RECEIVED = 2;
  XMLHttpRequest.LOADING = 3;
  XMLHttpRequest.DONE = 4;
  XMLHttpRequest.prototype = Object.create(EventTarget.prototype);
  XMLHttpRequest.prototype.constructor = XMLHttpRequest;
  XMLHttpRequest.prototype.open = function (method, url) {
    this._method = String(method).toUpperCase();
    this._url = url;
    this.readyState = 1;
  };
  XMLHttpRequest.prototype.setRequestHeader = function (name, value) {
    this._headers[name] = value;
  };
  XMLHttpRequest.prototype.overrideMimeType = function (type) {
    this._mime = type;
  };
  XMLHttpRequest.prototype.getResponseHeader = function (name) {
    return this._respHeaders ? this._respHeaders.get(name) : null;
  };
  XMLHttpRequest.prototype.getAllResponseHeaders = function () {
    if (!this._respHeaders) return "";
    var out = "";
    this._respHeaders.forEach(function (v, k) {
      out += k + ": " + v + "\r\n";
    });
    return out;
  };
  XMLHttpRequest.prototype.abort = function () {
    this._aborted = true;
  };
  XMLHttpRequest.prototype._fire = function (type) {
    this.dispatchEvent(new Event(type));
  };
  XMLHttpRequest.prototype.send = function (body) {
    var self = this;
    var mime = this._mime || "";
    var charsetMatch = /charset=([^;]+)/i.exec(mime);
    fetch(this._url, { method: this._method, headers: this._headers, body: body === undefined ? null : body })
      .then(function (resp) {
        if (self._aborted) return;
        self.status = resp.status;
        self.statusText = resp.statusText;
        self.responseURL = resp.url;
        self._respHeaders = resp.headers;
        self.readyState = 2;
        self._fire("readystatechange");
        var type = self.responseType;
        var reader =
          type === "arraybuffer"
            ? resp.arrayBuffer()
            : type === "blob"
            ? resp.blob()
            : charsetMatch
            ? resp.arrayBuffer().then(function (buf) {
                return new TextDecoder(charsetMatch[1]).decode(buf);
              })
            : resp.text();
        return reader.then(function (data) {
          if (type === "json") {
            try {
              self.response = JSON.parse(data);
            } catch (e) {
              self.response = null;
            }
          } else if (type === "document") {
            self.response = self.responseXML = new DOMParser().parseFromString(data, "text/html");
          } else {
            self.response = data;
          }
          if (type === "" || type === "text") self.responseText = data;
          self.readyState = 4;
          self._fire("readystatechange");
          self._fire("load");
          self._fire("loadend");
        });
      })
      .catch(function (e) {
        self.readyState = 4;
        self._fire("readystatechange");
        self._fire("error");
        self._fire("loadend");
      });
  };
  global.XMLHttpRequest = XMLHttpRequest;

  global.__npia_web = {
    setPage: setPage,
    nativeRequest: nativeRequest,
    Location: Location,
  };
})(this);
