/**
 * reader_env.js - browser globals for running rules-lib.js in JavaScriptCore.
 *
 * JavaScriptCore has no window, DOM, fetch, URL, or timers. This file builds
 * them in plain JavaScript on top of a few Swift functions on
 * `__npia_native`:
 *
 *   fetch(requestJson)          start a request; Swift answers with
 *                               __npia_fetchDone(id, responseJson)
 *   timer(id, ms)               Swift calls __npia_fireTimer(id) after ms
 *   decode(base64, charset)     bytes -> string in any IANA charset
 *   digest(algorithm, base64)   SHA digests for crypto.subtle
 *   cookies(url) / setCookie(url, value)
 *   log(level, message)
 *
 * Load order: reader_env.js, gm_stubs.js, rules-lib.js, bridge.js,
 * reader_host.js.
 */
(function (global) {
  "use strict";

  var native = global.__npia_native;
  var DESKTOP_UA =
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36";

  global.window = global;
  global.self = global;
  global.top = global;
  global.parent = global;
  global.frames = global;
  global.globalThis = global;

  // ------------------------------------------------------------------
  // Console
  // ------------------------------------------------------------------

  function formatArgs(args) {
    var parts = [];
    for (var i = 0; i < args.length; i++) {
      var value = args[i];
      if (typeof value === "string") parts.push(value);
      else if (value instanceof Error) parts.push(value.name + ": " + value.message);
      else {
        try {
          parts.push(JSON.stringify(value));
        } catch (e) {
          parts.push(String(value));
        }
      }
    }
    return parts.join(" ");
  }

  function logger(level) {
    return function () {
      try {
        native.log(level, formatArgs(arguments));
      } catch (e) {}
    };
  }

  global.console = {
    log: logger("debug"),
    debug: logger("debug"),
    trace: logger("debug"),
    info: logger("info"),
    warn: logger("warning"),
    error: logger("error"),
    dir: logger("debug"),
    table: logger("debug"),
    group: function () {},
    groupCollapsed: function () {},
    groupEnd: function () {},
    time: function () {},
    timeEnd: function () {},
    timeLog: function () {},
    count: function () {},
    assert: function (ok) {
      if (!ok) logger("error")(["Assertion failed"].concat([].slice.call(arguments, 1)));
    },
    clear: function () {},
  };

  // ------------------------------------------------------------------
  // Errors
  // ------------------------------------------------------------------

  var DOMEXCEPTION_CODES = { IndexSizeError: 1, HierarchyRequestError: 3, NotFoundError: 8, NotSupportedError: 9, InvalidStateError: 11, SyntaxError: 12, NetworkError: 19, AbortError: 20, TimeoutError: 23 };

  function DOMException(message, name) {
    var error = new Error(message);
    Object.setPrototypeOf(error, DOMException.prototype);
    error.name = name || "Error";
    error.code = DOMEXCEPTION_CODES[error.name] || 0;
    return error;
  }
  DOMException.prototype = Object.create(Error.prototype);
  DOMException.prototype.constructor = DOMException;
  global.DOMException = DOMException;

  // ------------------------------------------------------------------
  // Bytes, base64, text codecs
  // ------------------------------------------------------------------

  var B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  var B64_LOOKUP = new Int16Array(256).fill(-1);
  for (var b = 0; b < 64; b++) B64_LOOKUP[B64.charCodeAt(b)] = b;
  B64_LOOKUP[45] = 62; // "-" (base64url)
  B64_LOOKUP[95] = 63; // "_"

  function bytesToBase64(bytes) {
    var out = "";
    var chunk = [];
    var i = 0;
    for (; i + 2 < bytes.length; i += 3) {
      var n = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
      chunk.push(B64[n >> 18], B64[(n >> 12) & 63], B64[(n >> 6) & 63], B64[n & 63]);
      if (chunk.length >= 8192) {
        out += chunk.join("");
        chunk = [];
      }
    }
    var rest = bytes.length - i;
    if (rest === 1) {
      var one = bytes[i] << 16;
      chunk.push(B64[one >> 18], B64[(one >> 12) & 63], "==");
    } else if (rest === 2) {
      var two = (bytes[i] << 16) | (bytes[i + 1] << 8);
      chunk.push(B64[two >> 18], B64[(two >> 12) & 63], B64[(two >> 6) & 63], "=");
    }
    return out + chunk.join("");
  }

  function base64ToBytes(text) {
    text = String(text || "");
    var clean = [];
    for (var i = 0; i < text.length; i++) {
      var code = text.charCodeAt(i);
      if (code < 256 && B64_LOOKUP[code] >= 0) clean.push(B64_LOOKUP[code]);
    }
    var length = Math.floor((clean.length * 3) / 4);
    var bytes = new Uint8Array(length);
    var j = 0;
    for (var k = 0; k + 3 < clean.length; k += 4) {
      var n = (clean[k] << 18) | (clean[k + 1] << 12) | (clean[k + 2] << 6) | clean[k + 3];
      bytes[j++] = n >> 16;
      bytes[j++] = (n >> 8) & 255;
      bytes[j++] = n & 255;
    }
    var left = clean.length - k;
    if (left >= 2) {
      var m = (clean[k] << 18) | (clean[k + 1] << 12) | (left > 2 ? clean[k + 2] << 6 : 0);
      bytes[j++] = m >> 16;
      if (left > 2) bytes[j++] = (m >> 8) & 255;
    }
    return j === bytes.length ? bytes : bytes.subarray(0, j);
  }

  function latin1ToBytes(text) {
    var bytes = new Uint8Array(text.length);
    for (var i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i) & 255;
    return bytes;
  }

  function bytesToLatin1(bytes) {
    var out = "";
    for (var i = 0; i < bytes.length; i += 8192) {
      out += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
    }
    return out;
  }

  global.btoa = function (text) {
    text = String(text);
    for (var i = 0; i < text.length; i++) {
      if (text.charCodeAt(i) > 255) throw DOMException("The string contains characters outside of the Latin1 range.", "InvalidCharacterError");
    }
    return bytesToBase64(latin1ToBytes(text));
  };
  global.atob = function (text) {
    return bytesToLatin1(base64ToBytes(String(text).replace(/[\t\n\f\r ]/g, "")));
  };

  function utf8Encode(text) {
    text = String(text);
    var out = [];
    for (var i = 0; i < text.length; i++) {
      var c = text.charCodeAt(i);
      if (c >= 0xd800 && c < 0xdc00 && i + 1 < text.length) {
        var d = text.charCodeAt(i + 1);
        if (d >= 0xdc00 && d < 0xe000) {
          c = 0x10000 + ((c - 0xd800) << 10) + (d - 0xdc00);
          i++;
        } else c = 0xfffd;
      } else if (c >= 0xd800 && c < 0xe000) c = 0xfffd;
      if (c < 0x80) out.push(c);
      else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63));
      else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
      else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
    }
    return new Uint8Array(out);
  }

  function toBytes(data) {
    if (data == null) return new Uint8Array(0);
    if (data instanceof Uint8Array) return data;
    if (data instanceof ArrayBuffer) return new Uint8Array(data);
    if (ArrayBuffer.isView(data)) return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
    if (data instanceof Blob) return data._bytes;
    return utf8Encode(String(data));
  }

  // Remember the base64 that produced a buffer so decoding it again does not
  // re-encode megabytes in JavaScript.
  var bufferBase64 = new WeakMap();

  function bytesFromBase64Cached(b64) {
    var bytes = base64ToBytes(b64);
    bufferBase64.set(bytes.buffer, b64);
    return bytes;
  }

  function base64Of(bytes) {
    var cached = bytes.byteOffset === 0 && bytes.byteLength === bytes.buffer.byteLength ? bufferBase64.get(bytes.buffer) : null;
    return cached || bytesToBase64(bytes);
  }

  function normalizeCharset(label) {
    var name = String(label || "utf-8").trim().toLowerCase();
    if (name === "utf8" || name === "unicode-1-1-utf-8") return "utf-8";
    return name;
  }

  function decodeBytes(bytes, charset) {
    charset = normalizeCharset(charset);
    if (!bytes.length) return "";
    var text = native.decode(base64Of(bytes), charset);
    return text == null ? "" : String(text);
  }

  function TextDecoder(label, options) {
    this.encoding = normalizeCharset(label);
    this.fatal = !!(options && options.fatal);
    this.ignoreBOM = !!(options && options.ignoreBOM);
  }
  TextDecoder.prototype.decode = function (data) {
    var text = decodeBytes(toBytes(data), this.encoding);
    if (!this.ignoreBOM && text.charCodeAt(0) === 0xfeff) text = text.slice(1);
    return text;
  };
  function TextEncoder() {
    this.encoding = "utf-8";
  }
  TextEncoder.prototype.encode = function (text) {
    return utf8Encode(text === undefined ? "" : text);
  };
  TextEncoder.prototype.encodeInto = function (text, dest) {
    var bytes = utf8Encode(text);
    var n = Math.min(bytes.length, dest.length);
    dest.set(bytes.subarray(0, n));
    return { read: text.length, written: n };
  };
  global.TextDecoder = TextDecoder;
  global.TextEncoder = TextEncoder;

  // ------------------------------------------------------------------
  // URL and URLSearchParams
  // ------------------------------------------------------------------

  var SPECIAL_PORTS = { "http:": "80", "https:": "443", "ws:": "80", "wss:": "443", "ftp:": "21", "file:": "" };

  function percentEncode(text, keep) {
    var out = "";
    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      var code = text.charCodeAt(i);
      if (ch === "%" && /^[0-9A-Fa-f]{2}$/.test(text.substr(i + 1, 2))) out += ch;
      else if (code > 32 && code < 127 && keep.indexOf(ch) < 0) out += ch;
      else {
        var piece = ch;
        if (code >= 0xd800 && code < 0xdc00 && i + 1 < text.length) piece += text[++i];
        var bytes = utf8Encode(piece);
        for (var j = 0; j < bytes.length; j++) out += "%" + (bytes[j] < 16 ? "0" : "") + bytes[j].toString(16).toUpperCase();
      }
    }
    return out;
  }

  function removeDotSegments(path) {
    var input = path.split("/");
    var output = [];
    for (var i = 0; i < input.length; i++) {
      var seg = input[i];
      var lower = seg.toLowerCase();
      if (seg === "." || lower === "%2e") {
        if (i === input.length - 1) output.push("");
      } else if (seg === ".." || lower === ".%2e" || lower === "%2e." || lower === "%2e%2e") {
        if (output.length > 1) output.pop();
        if (i === input.length - 1) output.push("");
      } else output.push(seg);
    }
    var result = output.join("/");
    return result.charAt(0) === "/" ? result : "/" + result;
  }

  function parseAbsolute(text) {
    var m = /^([a-zA-Z][a-zA-Z0-9+.\-]*):(.*)$/.exec(text);
    if (!m) return null;
    var protocol = m[1].toLowerCase() + ":";
    var rest = m[2];
    var parts = { protocol: protocol, username: "", password: "", hostname: "", port: "", pathname: "", search: "", hash: "", opaque: false };
    var hashAt = rest.indexOf("#");
    if (hashAt >= 0) {
      parts.hash = rest.slice(hashAt);
      rest = rest.slice(0, hashAt);
    }
    var special = protocol in SPECIAL_PORTS;
    if (special) rest = rest.replace(/\\/g, "/");
    var queryAt = rest.indexOf("?");
    if (queryAt >= 0) {
      parts.search = rest.slice(queryAt);
      rest = rest.slice(0, queryAt);
    }
    if (rest.slice(0, 2) === "//" || (special && protocol !== "file:")) {
      rest = rest.replace(/^\/*/, "");
      var slash = rest.indexOf("/");
      var authority = slash >= 0 ? rest.slice(0, slash) : rest;
      parts.pathname = slash >= 0 ? rest.slice(slash) : "/";
      var at = authority.lastIndexOf("@");
      if (at >= 0) {
        var creds = authority.slice(0, at).split(":");
        parts.username = creds[0];
        parts.password = creds.slice(1).join(":");
        authority = authority.slice(at + 1);
      }
      var hp = /^(\[[^\]]*\]|[^:]*)(?::(\d*))?$/.exec(authority);
      if (!hp) throw new TypeError("Invalid URL: " + text);
      parts.hostname = hp[1].toLowerCase();
      parts.port = hp[2] || "";
      if (special && protocol !== "file:" && !parts.hostname) throw new TypeError("Invalid URL: " + text);
      if (/[\s<>^|%]/.test(parts.hostname.replace(/%[0-9a-f]{2}/gi, ""))) throw new TypeError("Invalid URL: " + text);
      if (parts.port === SPECIAL_PORTS[protocol]) parts.port = "";
      parts.pathname = special ? removeDotSegments(parts.pathname) : parts.pathname;
    } else {
      parts.pathname = rest;
      parts.opaque = rest.charAt(0) !== "/";
    }
    return parts;
  }

  function resolveParts(input, base) {
    var abs = parseAbsolute(input);
    if (abs) return abs;
    if (!base) throw new TypeError("Invalid URL: " + input);
    if (base.opaque) {
      if (input.charAt(0) === "#") {
        var copy = Object.assign({}, base);
        copy.hash = input;
        return copy;
      }
      throw new TypeError("Invalid URL: " + input);
    }
    var special = base.protocol in SPECIAL_PORTS;
    if (special) input = input.replace(/\\/g, "/");
    var result = Object.assign({}, base);
    result.hash = "";
    if (input === "") {
      return result;
    }
    if (input.slice(0, 2) === "//") return parseAbsolute(base.protocol + input);
    var hashAt = input.indexOf("#");
    var hash = "";
    if (hashAt >= 0) {
      hash = input.slice(hashAt);
      input = input.slice(0, hashAt);
    }
    var queryAt = input.indexOf("?");
    var search = null;
    if (queryAt >= 0) {
      search = input.slice(queryAt);
      input = input.slice(0, queryAt);
    }
    if (input === "") {
      if (search !== null) result.search = search;
    } else if (input.charAt(0) === "/") {
      result.pathname = removeDotSegments(input);
      result.search = search || "";
    } else {
      var dir = base.pathname.slice(0, base.pathname.lastIndexOf("/") + 1);
      result.pathname = removeDotSegments(dir + input);
      result.search = search || "";
    }
    result.hash = hash;
    return result;
  }

  function URLSearchParams(init) {
    this._list = [];
    this._url = null;
    if (init == null) return;
    if (init instanceof URLSearchParams) {
      this._list = init._list.map(function (p) {
        return [p[0], p[1]];
      });
    } else if (typeof init === "object") {
      if (typeof init[Symbol.iterator] === "function") {
        var self = this;
        Array.from(init).forEach(function (pair) {
          self._list.push([String(pair[0]), String(pair[1])]);
        });
      } else {
        for (var key in init) if (Object.prototype.hasOwnProperty.call(init, key)) this._list.push([key, String(init[key])]);
      }
    } else {
      this._parse(String(init));
    }
  }
  function formDecode(text) {
    text = text.replace(/\+/g, " ");
    try {
      return decodeURIComponent(text);
    } catch (e) {
      return text.replace(/%([0-9A-Fa-f]{2})/g, function (m, hex) {
        return String.fromCharCode(parseInt(hex, 16));
      });
    }
  }
  function formEncode(text) {
    return encodeURIComponent(text)
      .replace(/%20/g, "+")
      .replace(/[!'()~]/g, function (c) {
        return "%" + c.charCodeAt(0).toString(16).toUpperCase();
      });
  }
  URLSearchParams.prototype = {
    constructor: URLSearchParams,
    _parse: function (text) {
      this._list = [];
      if (text.charAt(0) === "?") text = text.slice(1);
      var self = this;
      text.split("&").forEach(function (piece) {
        if (!piece) return;
        var eq = piece.indexOf("=");
        var name = eq >= 0 ? piece.slice(0, eq) : piece;
        var value = eq >= 0 ? piece.slice(eq + 1) : "";
        self._list.push([formDecode(name), formDecode(value)]);
      });
    },
    _update: function () {
      if (this._url) {
        var query = this.toString();
        this._url._parts.search = query ? "?" + query : "";
      }
    },
    append: function (name, value) {
      this._list.push([String(name), String(value)]);
      this._update();
    },
    delete: function (name) {
      name = String(name);
      this._list = this._list.filter(function (p) {
        return p[0] !== name;
      });
      this._update();
    },
    get: function (name) {
      name = String(name);
      for (var i = 0; i < this._list.length; i++) if (this._list[i][0] === name) return this._list[i][1];
      return null;
    },
    getAll: function (name) {
      name = String(name);
      return this._list
        .filter(function (p) {
          return p[0] === name;
        })
        .map(function (p) {
          return p[1];
        });
    },
    has: function (name) {
      return this.get(name) !== null;
    },
    set: function (name, value) {
      name = String(name);
      value = String(value);
      var found = false;
      this._list = this._list.filter(function (p) {
        if (p[0] !== name) return true;
        if (found) return false;
        found = true;
        p[1] = value;
        return true;
      });
      if (!found) this._list.push([name, value]);
      this._update();
    },
    sort: function () {
      this._list.sort(function (a, b) {
        return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0;
      });
      this._update();
    },
    forEach: function (fn, thisArg) {
      var self = this;
      this._list.forEach(function (p) {
        fn.call(thisArg, p[1], p[0], self);
      });
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
    entries: function () {
      return this._list.map(function (p) {
        return [p[0], p[1]];
      })[Symbol.iterator]();
    },
    toString: function () {
      return this._list
        .map(function (p) {
          return formEncode(p[0]) + "=" + formEncode(p[1]);
        })
        .join("&");
    },
    get size() {
      return this._list.length;
    },
  };
  URLSearchParams.prototype[Symbol.iterator] = URLSearchParams.prototype.entries;
  global.URLSearchParams = URLSearchParams;

  function URL(input, base) {
    if (!(this instanceof URL)) throw new TypeError("Constructor URL requires 'new'");
    var baseParts = null;
    if (base !== undefined && base !== null) baseParts = base instanceof URL ? base._parts : new URL(String(base))._parts;
    this._parts = resolveParts(String(input).replace(/^[\u0000- ]+|[\u0000- ]+$/g, "").replace(/[\t\n\r]/g, ""), baseParts);
    this._params = null;
  }
  URL.canParse = function (input, base) {
    try {
      new URL(input, base);
      return true;
    } catch (e) {
      return false;
    }
  };
  URL.parse = function (input, base) {
    try {
      return new URL(input, base);
    } catch (e) {
      return null;
    }
  };
  var blobStore = {};
  var blobCounter = 0;
  URL.createObjectURL = function (blob) {
    var id = "blob:npia/" + ++blobCounter;
    blobStore[id] = blob;
    return id;
  };
  URL.revokeObjectURL = function (id) {
    delete blobStore[id];
  };
  function urlHost(p) {
    return p.hostname + (p.port ? ":" + p.port : "");
  }
  URL.prototype = {
    constructor: URL,
    get href() {
      var p = this._parts;
      if (p.opaque) return p.protocol + percentEncode(p.pathname, "") + percentEncode(p.search, "") + p.hash;
      var auth = p.username ? p.username + (p.password ? ":" + p.password : "") + "@" : "";
      var hasAuthority = p.hostname || p.protocol in SPECIAL_PORTS;
      return p.protocol + (hasAuthority ? "//" + auth + urlHost(p) : "") + this.pathname + this.search + this.hash;
    },
    set href(value) {
      this._parts = new URL(value)._parts;
      if (this._params) this._params._parse(this._parts.search);
    },
    get protocol() {
      return this._parts.protocol;
    },
    set protocol(value) {
      value = String(value).replace(/:.*$/, "").toLowerCase();
      if (/^[a-z][a-z0-9+.\-]*$/.test(value)) {
        this._parts.protocol = value + ":";
        if (this._parts.port === SPECIAL_PORTS[this._parts.protocol]) this._parts.port = "";
      }
    },
    get host() {
      return urlHost(this._parts);
    },
    set host(value) {
      var m = /^([^:\/]*)(?::(\d*))?/.exec(String(value));
      this._parts.hostname = m[1].toLowerCase();
      this._parts.port = m[2] || "";
    },
    get hostname() {
      return this._parts.hostname;
    },
    set hostname(value) {
      this._parts.hostname = String(value).toLowerCase();
    },
    get port() {
      return this._parts.port;
    },
    set port(value) {
      value = String(value);
      this._parts.port = value === SPECIAL_PORTS[this._parts.protocol] ? "" : value.replace(/\D.*$/, "");
    },
    get pathname() {
      return this._parts.opaque ? this._parts.pathname : percentEncode(this._parts.pathname, "\"<>`{} ");
    },
    set pathname(value) {
      value = String(value);
      this._parts.pathname = value.charAt(0) === "/" ? value : "/" + value;
    },
    get search() {
      var s = this._parts.search;
      return s === "?" ? "" : percentEncode(s, "\"<>#' ");
    },
    set search(value) {
      value = String(value);
      this._parts.search = value ? (value.charAt(0) === "?" ? value : "?" + value) : "";
      if (this._params) this._params._parse(this._parts.search);
    },
    get hash() {
      var h = this._parts.hash;
      return h === "#" ? "" : percentEncode(h, "\"<>` ");
    },
    set hash(value) {
      value = String(value);
      this._parts.hash = value ? (value.charAt(0) === "#" ? value : "#" + value) : "";
    },
    get origin() {
      var p = this._parts;
      if (p.protocol === "blob:") {
        try {
          return new URL(p.pathname).origin;
        } catch (e) {
          return "null";
        }
      }
      return p.protocol in SPECIAL_PORTS && p.protocol !== "file:" ? p.protocol + "//" + urlHost(p) : "null";
    },
    get username() {
      return this._parts.username;
    },
    set username(v) {
      this._parts.username = String(v);
    },
    get password() {
      return this._parts.password;
    },
    set password(v) {
      this._parts.password = String(v);
    },
    get searchParams() {
      if (!this._params) {
        this._params = new URLSearchParams(this._parts.search);
        this._params._url = this;
      }
      return this._params;
    },
    toString: function () {
      return this.href;
    },
    toJSON: function () {
      return this.href;
    },
  };
  global.URL = URL;
  global.webkitURL = URL;

  function resolveURL(value, base) {
    try {
      return new URL(value, base).href;
    } catch (e) {
      return null;
    }
  }

  // ------------------------------------------------------------------
  // Timers
  // ------------------------------------------------------------------

  var timers = {};
  var timerSeq = 0;
  var dropTimers = false;

  function addTimer(fn, ms, args, repeat) {
    if (dropTimers) return 0;
    var id = ++timerSeq;
    var delay = Math.max(repeat ? 4 : 0, Number(ms) || 0);
    timers[id] = { fn: fn, args: args, delay: delay, repeat: repeat };
    native.timer(id, delay);
    return id;
  }

  global.__npia_fireTimer = function (id) {
    var timer = timers[id];
    if (!timer) return;
    if (timer.repeat) native.timer(id, timer.delay);
    else delete timers[id];
    try {
      if (typeof timer.fn === "function") timer.fn.apply(global, timer.args);
      else (0, eval)(String(timer.fn));
    } catch (e) {
      console.error("Timer callback failed:", e && e.stack ? e.stack : String(e));
    }
  };
  global.__npia_dropTimers = function (value) {
    dropTimers = !!value;
  };

  global.setTimeout = function (fn, ms) {
    return addTimer(fn, ms, [].slice.call(arguments, 2), false);
  };
  global.setInterval = function (fn, ms) {
    return addTimer(fn, ms, [].slice.call(arguments, 2), true);
  };
  global.clearTimeout = global.clearInterval = function (id) {
    delete timers[id];
  };
  global.requestAnimationFrame = function (fn) {
    return addTimer(function () {
      fn(Date.now());
    }, 16, [], false);
  };
  global.cancelAnimationFrame = global.clearTimeout;
  global.requestIdleCallback = function (fn) {
    return addTimer(function () {
      fn({ didTimeout: false, timeRemaining: function () {
        return 50;
      } });
    }, 1, [], false);
  };
  global.cancelIdleCallback = global.clearTimeout;
  if (typeof global.queueMicrotask !== "function") {
    global.queueMicrotask = function (fn) {
      Promise.resolve().then(fn);
    };
  }

  // ------------------------------------------------------------------
  // Events
  // ------------------------------------------------------------------

  function Event(type, init) {
    init = init || {};
    this.type = String(type);
    this.bubbles = !!init.bubbles;
    this.cancelable = !!init.cancelable;
    this.composed = !!init.composed;
    this.defaultPrevented = false;
    this.target = null;
    this.currentTarget = null;
    this.timeStamp = Date.now();
    this.isTrusted = false;
    this._stop = false;
  }
  Event.prototype.preventDefault = function () {
    if (this.cancelable) this.defaultPrevented = true;
  };
  Event.prototype.stopPropagation = function () {
    this._stop = true;
  };
  Event.prototype.stopImmediatePropagation = function () {
    this._stop = true;
    this._stopNow = true;
  };
  function CustomEvent(type, init) {
    Event.call(this, type, init);
    this.detail = init && init.detail !== undefined ? init.detail : null;
  }
  CustomEvent.prototype = Object.create(Event.prototype);
  CustomEvent.prototype.constructor = CustomEvent;
  global.Event = Event;
  global.CustomEvent = CustomEvent;
  ["MouseEvent", "KeyboardEvent", "UIEvent", "FocusEvent", "InputEvent", "ProgressEvent", "MessageEvent", "ErrorEvent", "PopStateEvent", "StorageEvent"].forEach(function (name) {
    var ctor = function (type, init) {
      Event.call(this, type, init);
      if (init) for (var k in init) if (!(k in this)) this[k] = init[k];
    };
    ctor.prototype = Object.create(Event.prototype);
    ctor.prototype.constructor = ctor;
    global[name] = ctor;
  });

  var eventTargetMethods = {
    addEventListener: function (type, fn, options) {
      if (!fn) return;
      var map = this.__listeners || (Object.defineProperty(this, "__listeners", { value: {}, writable: true }), this.__listeners);
      var list = map[type] || (map[type] = []);
      for (var i = 0; i < list.length; i++) if (list[i].fn === fn) return;
      list.push({ fn: fn, once: !!(options && options.once) });
    },
    removeEventListener: function (type, fn) {
      var list = this.__listeners && this.__listeners[type];
      if (!list) return;
      for (var i = 0; i < list.length; i++)
        if (list[i].fn === fn) {
          list.splice(i, 1);
          return;
        }
    },
    dispatchEvent: function (event) {
      if (!event.target) event.target = this;
      var node = this;
      while (node) {
        event.currentTarget = node;
        var list = node.__listeners && node.__listeners[event.type];
        if (list) {
          list = list.slice();
          for (var i = 0; i < list.length; i++) {
            var entry = list[i];
            if (entry.once) node.removeEventListener(event.type, entry.fn);
            try {
              if (typeof entry.fn === "function") entry.fn.call(node, event);
              else if (entry.fn && typeof entry.fn.handleEvent === "function") entry.fn.handleEvent(event);
            } catch (e) {
              console.error("Event listener failed:", e && e.message);
            }
            if (event._stopNow) break;
          }
        }
        var handler = node["on" + event.type];
        if (typeof handler === "function" && node !== global) {
          try {
            handler.call(node, event);
          } catch (e) {
            console.error("Event handler failed:", e && e.message);
          }
        }
        if (!event.bubbles || event._stop) break;
        node = node.parentNode || (node.nodeType === 9 ? node.defaultView : null);
      }
      return !event.defaultPrevented;
    },
  };
  function EventTarget() {}
  EventTarget.prototype = Object.assign({ constructor: EventTarget }, eventTargetMethods);
  global.EventTarget = EventTarget;
  global.addEventListener = eventTargetMethods.addEventListener;
  global.removeEventListener = eventTargetMethods.removeEventListener;
  global.dispatchEvent = eventTargetMethods.dispatchEvent;

  // ------------------------------------------------------------------
  // Abort
  // ------------------------------------------------------------------

  function AbortSignal() {
    this.aborted = false;
    this.reason = undefined;
    this.onabort = null;
  }
  AbortSignal.prototype = Object.create(EventTarget.prototype);
  AbortSignal.prototype.constructor = AbortSignal;
  AbortSignal.prototype.throwIfAborted = function () {
    if (this.aborted) throw this.reason;
  };
  AbortSignal.prototype._abort = function (reason) {
    if (this.aborted) return;
    this.aborted = true;
    this.reason = reason === undefined ? DOMException("signal is aborted without reason", "AbortError") : reason;
    this.dispatchEvent(new Event("abort"));
  };
  AbortSignal.abort = function (reason) {
    var signal = new AbortSignal();
    signal._abort(reason);
    return signal;
  };
  AbortSignal.timeout = function (ms) {
    var signal = new AbortSignal();
    setTimeout(function () {
      signal._abort(DOMException("signal timed out", "TimeoutError"));
    }, ms);
    return signal;
  };
  AbortSignal.any = function (signals) {
    var signal = new AbortSignal();
    signals.forEach(function (s) {
      if (s.aborted) signal._abort(s.reason);
      else
        s.addEventListener("abort", function () {
          signal._abort(s.reason);
        });
    });
    return signal;
  };
  function AbortController() {
    this.signal = new AbortSignal();
  }
  AbortController.prototype.abort = function (reason) {
    this.signal._abort(reason);
  };
  global.AbortSignal = AbortSignal;
  global.AbortController = AbortController;

  // Exported for the other sections of this file.
  global.__npia_env = {
    native: native,
    DESKTOP_UA: DESKTOP_UA,
    bytesToBase64: bytesToBase64,
    base64ToBytes: base64ToBytes,
    bytesFromBase64Cached: bytesFromBase64Cached,
    base64Of: base64Of,
    utf8Encode: utf8Encode,
    toBytes: toBytes,
    decodeBytes: decodeBytes,
    bytesToLatin1: bytesToLatin1,
    resolveURL: resolveURL,
    blobStore: blobStore,
  };
})(this);
