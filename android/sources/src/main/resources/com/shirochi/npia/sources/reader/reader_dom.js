/**
 * reader_dom.js - a small HTML DOM for JavaScriptCore.
 *
 * Covers what the rules bundle uses: an HTML parser with the browser's
 * implied tags (html/head/body, tbody, p and li closing), the Node/Element
 * tree API, CSS selectors (combinators, attribute operators, :nth-*, :not,
 * :is, :has, :scope), innerHTML/outerHTML/XMLSerializer, and innerText.
 *
 * innerText follows the browser: on nodes inside the page document it is
 * the rendered text (block breaks, collapsed spaces); on DOMParser
 * documents and detached nodes, which are never rendered, it equals
 * textContent.
 */
(function (global) {
  "use strict";

  var env = global.__npia_env;

  // ------------------------------------------------------------------
  // Tag tables
  // ------------------------------------------------------------------

  function set(list) {
    var out = Object.create(null);
    list.split(" ").forEach(function (name) {
      out[name] = true;
    });
    return out;
  }

  var VOID = set("area base basefont bgsound br col embed frame hr image img input keygen link meta param source track wbr");
  var RAW_TEXT = set("script style xmp iframe noembed noframes");
  var ESCAPABLE_RAW = set("title textarea");
  var HEAD_TAGS = set("base basefont bgsound link meta noframes script style template title");
  var CLOSES_P = set("address article aside blockquote center details dialog dir div dl fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr li dd dt listing main menu nav ol p pre search section summary table ul xmp plaintext");
  var HEADINGS = set("h1 h2 h3 h4 h5 h6");
  var SCOPE_BOUNDARY = set("applet caption html table td th marquee object template");
  var TABLE_SCOPE = set("html table template");
  var LIST_SCOPE = set("ol ul");
  var TABLE_PARTS = set("table tbody thead tfoot tr td th caption colgroup");
  var FORMATTING = set("a b big code em font i nobr s small strike strong tt u");
  var SPECIAL = set("address applet area article aside base basefont bgsound blockquote body br button caption center col colgroup dd details dir div dl dt embed fieldset figcaption figure footer form frame frameset h1 h2 h3 h4 h5 h6 head header hgroup hr html iframe img input keygen li link listing main marquee menu meta nav noembed noframes noscript object ol p param plaintext pre script search section select source style summary table tbody td template textarea tfoot th thead title tr track ul wbr xmp");
  var BLOCK = set("address article aside blockquote body center dd details dialog dir div dl dt fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr html legend li listing main menu nav ol optgroup option p pre search section summary table tbody thead tfoot tr caption ul xmp");
  var HIDDEN = set("script style noscript template head title meta link base datalist noframes param source track area map");

  // ------------------------------------------------------------------
  // Character references
  // ------------------------------------------------------------------

  var ENTITIES = Object.create(null);
  var LEGACY = Object.create(null);
  (function () {
    var latin1 = "nbsp iexcl cent pound curren yen brvbar sect uml copy ordf laquo not shy reg macr deg plusmn sup2 sup3 acute micro para middot cedil sup1 ordm raquo frac14 frac12 frac34 iquest Agrave Aacute Acirc Atilde Auml Aring AElig Ccedil Egrave Eacute Ecirc Euml Igrave Iacute Icirc Iuml ETH Ntilde Ograve Oacute Ocirc Otilde Ouml times Oslash Ugrave Uacute Ucirc Uuml Yacute THORN szlig agrave aacute acirc atilde auml aring aelig ccedil egrave eacute ecirc euml igrave iacute icirc iuml eth ntilde ograve oacute ocirc otilde ouml divide oslash ugrave uacute ucirc uuml yacute thorn yuml".split(" ");
    latin1.forEach(function (name, i) {
      ENTITIES[name] = String.fromCharCode(160 + i);
      LEGACY[name] = true;
    });
    var greek = "Alpha Beta Gamma Delta Epsilon Zeta Eta Theta Iota Kappa Lambda Mu Nu Xi Omicron Pi Rho - Sigma Tau Upsilon Phi Chi Psi Omega".split(" ");
    greek.forEach(function (name, i) {
      if (name === "-") return;
      ENTITIES[name] = String.fromCharCode(913 + i);
      ENTITIES[name.toLowerCase()] = String.fromCharCode(945 + i);
    });
    ENTITIES.sigmaf = "ς";
    var named = {
      quot: 34, amp: 38, apos: 39, lt: 60, gt: 62, QUOT: 34, AMP: 38, LT: 60, GT: 62, COPY: 169, REG: 174, TRADE: 8482,
      OElig: 338, oelig: 339, Scaron: 352, scaron: 353, Yuml: 376, fnof: 402, circ: 710, tilde: 732,
      thetasym: 977, upsih: 978, piv: 982, ensp: 8194, emsp: 8195, thinsp: 8201, zwnj: 8204, zwj: 8205, lrm: 8206, rlm: 8207,
      ndash: 8211, mdash: 8212, horbar: 8213, lsquo: 8216, rsquo: 8217, sbquo: 8218, ldquo: 8220, rdquo: 8221, bdquo: 8222,
      dagger: 8224, Dagger: 8225, bull: 8226, bullet: 8226, hellip: 8230, mldr: 8230, nldr: 8229, permil: 8240, prime: 8242, Prime: 8243,
      lsaquo: 8249, rsaquo: 8250, oline: 8254, frasl: 8260, euro: 8364, image: 8465, weierp: 8472, real: 8476, trade: 8482,
      alefsym: 8501, larr: 8592, uarr: 8593, rarr: 8594, darr: 8595, harr: 8596, crarr: 8629, lArr: 8656, uArr: 8657, rArr: 8658,
      dArr: 8659, hArr: 8660, forall: 8704, part: 8706, exist: 8707, empty: 8709, nabla: 8711, isin: 8712, notin: 8713, ni: 8715,
      prod: 8719, sum: 8721, minus: 8722, lowast: 8727, radic: 8730, prop: 8733, infin: 8734, ang: 8736, and: 8743, or: 8744,
      cap: 8745, cup: 8746, int: 8747, there4: 8756, sim: 8764, cong: 8773, asymp: 8776, ne: 8800, equiv: 8801, le: 8804, ge: 8805,
      sub: 8834, sup: 8835, nsub: 8836, sube: 8838, supe: 8839, oplus: 8853, otimes: 8855, perp: 8869, sdot: 8901,
      lceil: 8968, rceil: 8969, lfloor: 8970, rfloor: 8971, lang: 10216, rang: 10217, loz: 9674, spades: 9824, clubs: 9827,
      hearts: 9829, diams: 9830, Tab: 9, NewLine: 10, excl: 33, num: 35, dollar: 36, percnt: 37, lpar: 40, rpar: 41, ast: 42,
      midast: 42, plus: 43, comma: 44, period: 46, sol: 47, colon: 58, semi: 59, equals: 61, quest: 63, commat: 64, lsqb: 91,
      lbrack: 91, bsol: 92, rsqb: 93, rbrack: 93, Hat: 94, lowbar: 95, grave: 96, lcub: 123, lbrace: 123, verbar: 124, vert: 124,
      VerticalLine: 124, rcub: 125, rbrace: 125, hyphen: 8208, dash: 8208, half: 189, emsp13: 8196, emsp14: 8197, numsp: 8199,
      puncsp: 8200, hairsp: 8202, ZeroWidthSpace: 8203, NoBreak: 8288, NonBreakingSpace: 160, ThinSpace: 8201, star: 9734,
      starf: 9733, check: 10003, cross: 10007, frac13: 8531, frac23: 8532, frac15: 8533, ldquor: 8222, rdquor: 8221,
      lsquor: 8218, rsquor: 8217, centerdot: 183, middot: 183, deg: 176, times: 215, divide: 247, laquo: 171, raquo: 187,
      langle: 10216, rangle: 10217, LeftAngleBracket: 10216, RightAngleBracket: 10217, squ: 9633, square: 9633, blacksquare: 9642,
      cir: 9675, xcirc: 9711, female: 9792, male: 9794, sharp: 9839, flat: 9837, natur: 9838, phone: 9742, copysr: 8471,
    };
    for (var key in named) ENTITIES[key] = String.fromCodePoint(named[key]);
    "amp lt gt quot AMP LT GT QUOT COPY REG".split(" ").forEach(function (name) {
      LEGACY[name] = true;
    });
  })();

  var WIN1252 = { 128: 8364, 130: 8218, 131: 402, 132: 8222, 133: 8230, 134: 8224, 135: 8225, 136: 710, 137: 8240, 138: 352, 139: 8249, 140: 338, 142: 381, 145: 8216, 146: 8217, 147: 8220, 148: 8221, 149: 8226, 150: 8211, 151: 8212, 152: 732, 153: 8482, 154: 353, 155: 8250, 156: 339, 158: 382, 159: 376 };

  function decodeEntities(text, inAttribute) {
    if (text.indexOf("&") < 0) return text;
    return text.replace(/&(#[xX][0-9a-fA-F]+;?|#[0-9]+;?|[A-Za-z][A-Za-z0-9]*;?)/g, function (match, body, offset, whole) {
      if (body.charAt(0) === "#") {
        var hex = body.charAt(1) === "x" || body.charAt(1) === "X";
        var code = parseInt(body.slice(hex ? 2 : 1), hex ? 16 : 10);
        if (WIN1252[code]) code = WIN1252[code];
        if (!code || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) return "�";
        return String.fromCodePoint(code);
      }
      var semicolon = body.charAt(body.length - 1) === ";";
      var name = semicolon ? body.slice(0, -1) : body;
      if (semicolon && ENTITIES[name] !== undefined) return ENTITIES[name];
      for (var k = name.length; k >= 2; k--) {
        var prefix = name.slice(0, k);
        if (LEGACY[prefix] && ENTITIES[prefix] !== undefined) {
          var rest = name.slice(k) + (semicolon ? ";" : "");
          if (inAttribute && (rest ? /^[A-Za-z0-9=]/.test(rest) : whole.charAt(offset + match.length) === "=")) return match;
          return ENTITIES[prefix] + rest;
        }
      }
      return match;
    });
  }

  // ------------------------------------------------------------------
  // Nodes
  // ------------------------------------------------------------------

  var ELEMENT_NODE = 1, ATTRIBUTE_NODE = 2, TEXT_NODE = 3, CDATA_SECTION_NODE = 4, PROCESSING_INSTRUCTION_NODE = 7, COMMENT_NODE = 8, DOCUMENT_NODE = 9, DOCUMENT_TYPE_NODE = 10, DOCUMENT_FRAGMENT_NODE = 11;
  var NODE_CONSTANTS = {
    ELEMENT_NODE: 1, ATTRIBUTE_NODE: 2, TEXT_NODE: 3, CDATA_SECTION_NODE: 4, ENTITY_REFERENCE_NODE: 5, ENTITY_NODE: 6,
    PROCESSING_INSTRUCTION_NODE: 7, COMMENT_NODE: 8, DOCUMENT_NODE: 9, DOCUMENT_TYPE_NODE: 10, DOCUMENT_FRAGMENT_NODE: 11, NOTATION_NODE: 12,
    DOCUMENT_POSITION_DISCONNECTED: 1, DOCUMENT_POSITION_PRECEDING: 2, DOCUMENT_POSITION_FOLLOWING: 4, DOCUMENT_POSITION_CONTAINS: 8,
    DOCUMENT_POSITION_CONTAINED_BY: 16, DOCUMENT_POSITION_IMPLEMENTATION_SPECIFIC: 32,
  };

  var currentOwner = null;

  class Node extends EventTarget {
    constructor() {
      super();
      this.parentNode = null;
      this.childNodes = [];
      this.ownerDocument = currentOwner || global.document || null;
    }
    get parentElement() {
      var p = this.parentNode;
      return p && p.nodeType === ELEMENT_NODE ? p : null;
    }
    get firstChild() {
      return this.childNodes[0] || null;
    }
    get lastChild() {
      return this.childNodes[this.childNodes.length - 1] || null;
    }
    get nextSibling() {
      var p = this.parentNode;
      if (!p) return null;
      var list = p.childNodes;
      return list[list.indexOf(this) + 1] || null;
    }
    get previousSibling() {
      var p = this.parentNode;
      if (!p) return null;
      var list = p.childNodes;
      var i = list.indexOf(this);
      return i > 0 ? list[i - 1] : null;
    }
    get nextElementSibling() {
      var p = this.parentNode;
      if (!p) return null;
      var list = p.childNodes;
      for (var i = list.indexOf(this) + 1; i < list.length; i++) if (list[i].nodeType === ELEMENT_NODE) return list[i];
      return null;
    }
    get previousElementSibling() {
      var p = this.parentNode;
      if (!p) return null;
      var list = p.childNodes;
      for (var i = list.indexOf(this) - 1; i >= 0; i--) if (list[i].nodeType === ELEMENT_NODE) return list[i];
      return null;
    }
    get isConnected() {
      var node = this;
      while (node.parentNode) node = node.parentNode;
      return node.nodeType === DOCUMENT_NODE;
    }
    get baseURI() {
      var doc = this.nodeType === DOCUMENT_NODE ? this : this.ownerDocument;
      return doc ? doc._baseURL() : "about:blank";
    }
    get nodeValue() {
      return null;
    }
    set nodeValue(v) {}
    getRootNode() {
      var node = this;
      while (node.parentNode) node = node.parentNode;
      return node;
    }
    hasChildNodes() {
      return this.childNodes.length > 0;
    }
    contains(other) {
      for (var node = other; node; node = node.parentNode) if (node === this) return true;
      return false;
    }
    _checkChild(node) {
      if (!node || !(node instanceof Node)) throw new TypeError("Failed to execute on 'Node': parameter 1 is not of type 'Node'.");
      if (node.contains(this)) throw DOMException("The new child element contains the parent.", "HierarchyRequestError");
    }
    insertBefore(node, ref) {
      this._checkChild(node);
      if (ref && ref.parentNode !== this) throw DOMException("The node before which the new node is to be inserted is not a child of this node.", "NotFoundError");
      var nodes = node.nodeType === DOCUMENT_FRAGMENT_NODE ? node.childNodes.slice() : [node];
      for (var i = 0; i < nodes.length; i++) {
        var child = nodes[i];
        if (child.parentNode) child.parentNode._detach(child);
        var at = ref ? this.childNodes.indexOf(ref) : this.childNodes.length;
        this.childNodes.splice(at, 0, child);
        child.parentNode = this;
        adopt(child, this.nodeType === DOCUMENT_NODE ? this : this.ownerDocument);
      }
      return node;
    }
    appendChild(node) {
      return this.insertBefore(node, null);
    }
    _detach(child) {
      var i = this.childNodes.indexOf(child);
      if (i >= 0) this.childNodes.splice(i, 1);
      child.parentNode = null;
    }
    removeChild(child) {
      if (!child || child.parentNode !== this) throw DOMException("The node to be removed is not a child of this node.", "NotFoundError");
      this._detach(child);
      return child;
    }
    replaceChild(node, old) {
      if (!old || old.parentNode !== this) throw DOMException("The node to be replaced is not a child of this node.", "NotFoundError");
      if (node === old) return old;
      this.insertBefore(node, old);
      this._detach(old);
      return old;
    }
    cloneNode(deep) {
      var copy = this._cloneShallow();
      if (deep) {
        for (var i = 0; i < this.childNodes.length; i++) {
          var child = this.childNodes[i].cloneNode(true);
          child.parentNode = copy;
          copy.childNodes.push(child);
        }
      }
      return copy;
    }
    isEqualNode(other) {
      return !!other && serializeNode(this, false) === serializeNode(other, false);
    }
    isSameNode(other) {
      return this === other;
    }
    normalize() {
      var list = this.childNodes;
      for (var i = 0; i < list.length; i++) {
        var node = list[i];
        if (node.nodeType === TEXT_NODE) {
          while (list[i + 1] && list[i + 1].nodeType === TEXT_NODE) {
            node.data += list[i + 1].data;
            this._detach(list[i + 1]);
          }
          if (!node.data) {
            this._detach(node);
            i--;
          }
        } else if (node.normalize) node.normalize();
      }
    }
    compareDocumentPosition(other) {
      if (other === this) return 0;
      var a = ancestry(this), b = ancestry(other);
      if (a[0] !== b[0]) return NODE_CONSTANTS.DOCUMENT_POSITION_DISCONNECTED | NODE_CONSTANTS.DOCUMENT_POSITION_IMPLEMENTATION_SPECIFIC | NODE_CONSTANTS.DOCUMENT_POSITION_FOLLOWING;
      var i = 0;
      while (i < a.length && i < b.length && a[i] === b[i]) i++;
      if (i === a.length) return NODE_CONSTANTS.DOCUMENT_POSITION_CONTAINED_BY | NODE_CONSTANTS.DOCUMENT_POSITION_FOLLOWING;
      if (i === b.length) return NODE_CONSTANTS.DOCUMENT_POSITION_CONTAINS | NODE_CONSTANTS.DOCUMENT_POSITION_PRECEDING;
      var siblings = a[i - 1].childNodes;
      return siblings.indexOf(a[i]) < siblings.indexOf(b[i]) ? NODE_CONSTANTS.DOCUMENT_POSITION_FOLLOWING : NODE_CONSTANTS.DOCUMENT_POSITION_PRECEDING;
    }
    get textContent() {
      return null;
    }
    set textContent(v) {}
    // ChildNode / ParentNode mixins, shared by elements, text, and fragments.
    remove() {
      if (this.parentNode) this.parentNode._detach(this);
    }
    before() {
      if (!this.parentNode) return;
      this.parentNode.insertBefore(nodesFrom(arguments, this), this);
    }
    after() {
      if (!this.parentNode) return;
      this.parentNode.insertBefore(nodesFrom(arguments, this), this.nextSibling);
    }
    replaceWith() {
      var parent = this.parentNode;
      if (!parent) return;
      var next = this.nextSibling;
      var frag = nodesFrom(arguments, this);
      parent._detach(this);
      parent.insertBefore(frag, next && next.parentNode === parent ? next : null);
    }
  }
  Object.keys(NODE_CONSTANTS).forEach(function (key) {
    Node[key] = NODE_CONSTANTS[key];
    Node.prototype[key] = NODE_CONSTANTS[key];
  });

  function ancestry(node) {
    var chain = [];
    for (; node; node = node.parentNode) chain.unshift(node);
    return chain;
  }

  function adopt(node, doc) {
    if (!doc || node.ownerDocument === doc) return;
    node.ownerDocument = doc;
    for (var i = 0; i < node.childNodes.length; i++) adopt(node.childNodes[i], doc);
  }

  function nodesFrom(args, context) {
    var doc = context.ownerDocument || global.document;
    var frag = new DocumentFragment();
    frag.ownerDocument = doc;
    for (var i = 0; i < args.length; i++) {
      var item = args[i];
      frag.appendChild(item instanceof Node ? item : new Text(String(item)));
    }
    return frag;
  }

  var parentMixin = {
    get children() {
      return this.childNodes.filter(function (n) {
        return n.nodeType === ELEMENT_NODE;
      });
    },
    get childElementCount() {
      return this.children.length;
    },
    get firstElementChild() {
      for (var i = 0; i < this.childNodes.length; i++) if (this.childNodes[i].nodeType === ELEMENT_NODE) return this.childNodes[i];
      return null;
    },
    get lastElementChild() {
      for (var i = this.childNodes.length - 1; i >= 0; i--) if (this.childNodes[i].nodeType === ELEMENT_NODE) return this.childNodes[i];
      return null;
    },
    append: function () {
      this.appendChild(nodesFrom(arguments, this));
    },
    prepend: function () {
      this.insertBefore(nodesFrom(arguments, this), this.firstChild);
    },
    replaceChildren: function () {
      var frag = nodesFrom(arguments, this);
      while (this.childNodes.length) this._detach(this.childNodes[0]);
      this.appendChild(frag);
    },
    querySelector: function (selector) {
      return querySelector(this, selector);
    },
    querySelectorAll: function (selector) {
      return toNodeList(querySelectorAll(this, selector));
    },
    getElementsByTagName: function (name) {
      name = String(name).toLowerCase();
      return toNodeList(
        descendants(this).filter(function (el) {
          return name === "*" || el.localName === name;
        })
      );
    },
    getElementsByClassName: function (names) {
      var wanted = String(names).trim().split(/\s+/).filter(Boolean);
      return toNodeList(
        descendants(this).filter(function (el) {
          var classes = el._classes();
          return wanted.length > 0 && wanted.every(function (c) {
            return classes.indexOf(c) >= 0;
          });
        })
      );
    },
    getElementById: function (id) {
      id = String(id);
      var all = descendants(this);
      for (var i = 0; i < all.length; i++) if (all[i].getAttribute("id") === id) return all[i];
      return null;
    },
  };

  function mixin(target, source) {
    Object.getOwnPropertyNames(source).forEach(function (key) {
      Object.defineProperty(target, key, Object.getOwnPropertyDescriptor(source, key));
    });
  }

  function toNodeList(array) {
    array.item = function (i) {
      return this[i] || null;
    };
    array.namedItem = function (name) {
      for (var i = 0; i < this.length; i++) if (this[i].id === name || this[i].getAttribute("name") === name) return this[i];
      return null;
    };
    return array;
  }

  function descendants(root) {
    var out = [];
    var stack = root.childNodes.slice().reverse();
    while (stack.length) {
      var node = stack.pop();
      if (node.nodeType !== ELEMENT_NODE) continue;
      out.push(node);
      for (var i = node.childNodes.length - 1; i >= 0; i--) stack.push(node.childNodes[i]);
    }
    return out;
  }

  function textOf(node) {
    if (node.nodeType === TEXT_NODE || node.nodeType === CDATA_SECTION_NODE) return node.data;
    if (node.nodeType === COMMENT_NODE || node.nodeType === PROCESSING_INSTRUCTION_NODE) return "";
    var out = [];
    var stack = [node];
    while (stack.length) {
      var n = stack.pop();
      if (n.nodeType === TEXT_NODE || n.nodeType === CDATA_SECTION_NODE) out.push(n.data);
      else for (var i = n.childNodes.length - 1; i >= 0; i--) stack.push(n.childNodes[i]);
    }
    return out.join("");
  }

  class CharacterData extends Node {
    constructor(data) {
      super();
      this.data = data === undefined ? "" : String(data);
    }
    get nodeValue() {
      return this.data;
    }
    set nodeValue(v) {
      this.data = String(v);
    }
    get textContent() {
      return this.data;
    }
    set textContent(v) {
      this.data = v == null ? "" : String(v);
    }
    get length() {
      return this.data.length;
    }
    appendData(s) {
      this.data += s;
    }
    substringData(offset, count) {
      return this.data.substr(offset, count);
    }
    insertData(offset, s) {
      this.data = this.data.slice(0, offset) + s + this.data.slice(offset);
    }
    deleteData(offset, count) {
      this.data = this.data.slice(0, offset) + this.data.slice(offset + count);
    }
    replaceData(offset, count, s) {
      this.data = this.data.slice(0, offset) + s + this.data.slice(offset + count);
    }
    get nextElementSibling() {
      return Object.getOwnPropertyDescriptor(Node.prototype, "nextElementSibling").get.call(this);
    }
  }

  class Text extends CharacterData {
    get nodeType() {
      return TEXT_NODE;
    }
    get nodeName() {
      return "#text";
    }
    _cloneShallow() {
      var t = new Text(this.data);
      t.ownerDocument = this.ownerDocument;
      return t;
    }
    get wholeText() {
      var p = this.parentNode;
      if (!p) return this.data;
      var list = p.childNodes;
      var i = list.indexOf(this);
      var start = i, end = i;
      while (start > 0 && list[start - 1].nodeType === TEXT_NODE) start--;
      while (end < list.length - 1 && list[end + 1].nodeType === TEXT_NODE) end++;
      var out = "";
      for (var k = start; k <= end; k++) out += list[k].data;
      return out;
    }
    splitText(offset) {
      var tail = new Text(this.data.slice(offset));
      tail.ownerDocument = this.ownerDocument;
      this.data = this.data.slice(0, offset);
      if (this.parentNode) this.parentNode.insertBefore(tail, this.nextSibling);
      return tail;
    }
  }

  class CDATASection extends Text {
    get nodeType() {
      return CDATA_SECTION_NODE;
    }
    get nodeName() {
      return "#cdata-section";
    }
  }

  class Comment extends CharacterData {
    get nodeType() {
      return COMMENT_NODE;
    }
    get nodeName() {
      return "#comment";
    }
    _cloneShallow() {
      var c = new Comment(this.data);
      c.ownerDocument = this.ownerDocument;
      return c;
    }
  }

  class ProcessingInstruction extends CharacterData {
    constructor(target, data) {
      super(data);
      this.target = target;
    }
    get nodeType() {
      return PROCESSING_INSTRUCTION_NODE;
    }
    get nodeName() {
      return this.target;
    }
    _cloneShallow() {
      return new ProcessingInstruction(this.target, this.data);
    }
  }

  class DocumentType extends Node {
    constructor(name, publicId, systemId) {
      super();
      this.name = name || "html";
      this.publicId = publicId || "";
      this.systemId = systemId || "";
    }
    get nodeType() {
      return DOCUMENT_TYPE_NODE;
    }
    get nodeName() {
      return this.name;
    }
    _cloneShallow() {
      return new DocumentType(this.name, this.publicId, this.systemId);
    }
  }

  class DocumentFragment extends Node {
    get nodeType() {
      return DOCUMENT_FRAGMENT_NODE;
    }
    get nodeName() {
      return "#document-fragment";
    }
    get textContent() {
      return textOf(this);
    }
    set textContent(v) {
      while (this.childNodes.length) this._detach(this.childNodes[0]);
      if (v != null && v !== "") this.appendChild(new Text(String(v)));
    }
    get innerHTML() {
      return serializeChildren(this, false);
    }
    _cloneShallow() {
      var f = new DocumentFragment();
      f.ownerDocument = this.ownerDocument;
      return f;
    }
  }
  mixin(DocumentFragment.prototype, parentMixin);

  class Attr {
    constructor(name, value, owner) {
      this.name = name;
      this._value = value;
      this.ownerElement = owner || null;
    }
    get value() {
      return this.ownerElement ? this.ownerElement.getAttribute(this.name) : this._value;
    }
    set value(v) {
      if (this.ownerElement) this.ownerElement.setAttribute(this.name, v);
      else this._value = String(v);
    }
    get nodeName() {
      return this.name;
    }
    get localName() {
      return this.name;
    }
    get nodeValue() {
      return this.value;
    }
    get textContent() {
      return this.value;
    }
    get nodeType() {
      return ATTRIBUTE_NODE;
    }
    get specified() {
      return true;
    }
    get namespaceURI() {
      return null;
    }
    get prefix() {
      return null;
    }
    cloneNode() {
      return new Attr(this.name, this.value, null);
    }
  }

  // ------------------------------------------------------------------
  // Element
  // ------------------------------------------------------------------

  var XHTML_NS = "http://www.w3.org/1999/xhtml";

  class Element extends Node {
    constructor(localName, ownerDocument) {
      super();
      this.localName = localName;
      this._attrs = [];
      if (ownerDocument) this.ownerDocument = ownerDocument;
      this._xml = !!(this.ownerDocument && this.ownerDocument._xml);
      this.namespaceURI = this._xml ? null : XHTML_NS;
    }
    get nodeType() {
      return ELEMENT_NODE;
    }
    get tagName() {
      return this._xml ? this.localName : this.localName.toUpperCase();
    }
    get nodeName() {
      return this.tagName;
    }
    get prefix() {
      return null;
    }
    _cloneShallow() {
      var el = createElementFor(this.ownerDocument, this.localName);
      el._attrs = this._attrs.map(function (a) {
        return [a[0], a[1]];
      });
      if (this._value !== undefined) el._value = this._value;
      return el;
    }
    // Attributes
    _find(name) {
      if (!this._xml) name = String(name).toLowerCase();
      for (var i = 0; i < this._attrs.length; i++) if (this._attrs[i][0] === name) return i;
      return -1;
    }
    getAttribute(name) {
      var i = this._find(name);
      return i >= 0 ? this._attrs[i][1] : null;
    }
    getAttributeNS(ns, name) {
      return this.getAttribute(name);
    }
    setAttribute(name, value) {
      name = String(name);
      if (!/^[^\s"'>\/=]+$/.test(name)) throw DOMException("'" + name + "' is not a valid attribute name.", "InvalidCharacterError");
      if (!this._xml) name = name.toLowerCase();
      var i = this._find(name);
      value = String(value);
      if (i >= 0) this._attrs[i][1] = value;
      else this._attrs.push([name, value]);
      if (name === "style" && this._style) this._style._load();
    }
    setAttributeNS(ns, name, value) {
      this.setAttribute(String(name).replace(/^.*:/, ""), value);
    }
    removeAttribute(name) {
      var i = this._find(name);
      if (i >= 0) this._attrs.splice(i, 1);
      if (String(name).toLowerCase() === "style" && this._style) this._style._load();
    }
    removeAttributeNS(ns, name) {
      this.removeAttribute(name);
    }
    hasAttribute(name) {
      return this._find(name) >= 0;
    }
    hasAttributeNS(ns, name) {
      return this.hasAttribute(name);
    }
    hasAttributes() {
      return this._attrs.length > 0;
    }
    toggleAttribute(name, force) {
      var has = this.hasAttribute(name);
      if (force === true || (force === undefined && !has)) {
        if (!has) this.setAttribute(name, "");
        return true;
      }
      this.removeAttribute(name);
      return false;
    }
    getAttributeNames() {
      return this._attrs.map(function (a) {
        return a[0];
      });
    }
    getAttributeNode(name) {
      return this.hasAttribute(name) ? new Attr(this._xml ? name : String(name).toLowerCase(), null, this) : null;
    }
    setAttributeNode(attr) {
      this.setAttribute(attr.name, attr.value);
      return null;
    }
    removeAttributeNode(attr) {
      this.removeAttribute(attr.name);
      return attr;
    }
    get attributes() {
      var self = this;
      var list = this._attrs.map(function (a) {
        return new Attr(a[0], null, self);
      });
      list.item = function (i) {
        return this[i] || null;
      };
      list.getNamedItem = function (name) {
        return self.getAttributeNode(name);
      };
      list.removeNamedItem = function (name) {
        self.removeAttribute(name);
      };
      list.setNamedItem = function (attr) {
        self.setAttribute(attr.name, attr.value);
      };
      list.forEach(function (attr) {
        if (!(attr.name in list)) list[attr.name] = attr;
      });
      return list;
    }
    get id() {
      return this.getAttribute("id") || "";
    }
    set id(v) {
      this.setAttribute("id", v);
    }
    get className() {
      return this.getAttribute("class") || "";
    }
    set className(v) {
      this.setAttribute("class", v);
    }
    _classes() {
      var value = this.getAttribute("class");
      return value ? value.trim().split(/[\t\n\f\r ]+/).filter(Boolean) : [];
    }
    get classList() {
      return new DOMTokenList(this, "class");
    }
    get relList() {
      return new DOMTokenList(this, "rel");
    }
    get dataset() {
      var el = this;
      return new Proxy(
        {},
        {
          get: function (t, key) {
            if (typeof key !== "string") return undefined;
            var v = el.getAttribute("data-" + key.replace(/[A-Z]/g, "-$&").toLowerCase());
            return v === null ? undefined : v;
          },
          set: function (t, key, value) {
            el.setAttribute("data-" + String(key).replace(/[A-Z]/g, "-$&").toLowerCase(), value);
            return true;
          },
          deleteProperty: function (t, key) {
            el.removeAttribute("data-" + String(key).replace(/[A-Z]/g, "-$&").toLowerCase());
            return true;
          },
          has: function (t, key) {
            return el.hasAttribute("data-" + String(key).replace(/[A-Z]/g, "-$&").toLowerCase());
          },
          ownKeys: function () {
            return el._attrs
              .filter(function (a) {
                return a[0].indexOf("data-") === 0;
              })
              .map(function (a) {
                return a[0].slice(5).replace(/-([a-z])/g, function (m, c) {
                  return c.toUpperCase();
                });
              });
          },
          getOwnPropertyDescriptor: function (t, key) {
            var v = el.getAttribute("data-" + String(key).replace(/[A-Z]/g, "-$&").toLowerCase());
            return v === null ? undefined : { value: v, enumerable: true, configurable: true, writable: true };
          },
        }
      );
    }
    get style() {
      if (!this._style) this._style = makeStyle(this);
      return this._style;
    }
    set style(v) {
      this.setAttribute("style", v);
    }
    // Content
    get textContent() {
      return textOf(this);
    }
    set textContent(v) {
      while (this.childNodes.length) this._detach(this.childNodes[0]);
      if (v != null && v !== "") this.appendChild(new Text(String(v)));
    }
    get innerText() {
      if (isRendered(this)) return renderedText(this);
      return textOf(this);
    }
    set innerText(v) {
      while (this.childNodes.length) this._detach(this.childNodes[0]);
      var doc = this.ownerDocument;
      String(v == null ? "" : v)
        .split(/\r\n|\r|\n/)
        .forEach(function (line, i, all) {
          if (line) this.appendChild(new Text(line));
          if (i < all.length - 1) this.appendChild(createElementFor(doc, "br"));
        }, this);
    }
    get outerText() {
      return this.innerText;
    }
    get innerHTML() {
      return serializeChildren(this.localName === "template" && this._content ? this._content : this, this._xml);
    }
    set innerHTML(html) {
      var target = this.localName === "template" ? this.content : this;
      while (target.childNodes.length) target._detach(target.childNodes[0]);
      target.appendChild(parseFragment(String(html == null ? "" : html), this));
    }
    get outerHTML() {
      return serializeNode(this, this._xml);
    }
    set outerHTML(html) {
      if (!this.parentNode) return;
      var context = this.parentNode.nodeType === ELEMENT_NODE ? this.parentNode : createElementFor(this.ownerDocument, "body");
      this.replaceWith(parseFragment(String(html), context));
    }
    insertAdjacentHTML(where, html) {
      var context = /^(beforebegin|afterend)$/i.test(where) ? this.parentElement || createElementFor(this.ownerDocument, "body") : this;
      this._insertAdjacent(where, parseFragment(String(html), context));
    }
    insertAdjacentElement(where, el) {
      this._insertAdjacent(where, el);
      return el;
    }
    insertAdjacentText(where, text) {
      this._insertAdjacent(where, new Text(String(text)));
    }
    _insertAdjacent(where, node) {
      switch (String(where).toLowerCase()) {
        case "beforebegin":
          if (this.parentNode) this.parentNode.insertBefore(node, this);
          break;
        case "afterbegin":
          this.insertBefore(node, this.firstChild);
          break;
        case "beforeend":
          this.appendChild(node);
          break;
        case "afterend":
          if (this.parentNode) this.parentNode.insertBefore(node, this.nextSibling);
          break;
        default:
          throw DOMException("Invalid position: " + where, "SyntaxError");
      }
    }
    // Selectors
    matches(selector) {
      return matchesSelector(this, selector, null);
    }
    webkitMatchesSelector(selector) {
      return this.matches(selector);
    }
    closest(selector) {
      var list = parseSelector(selector);
      for (var el = this; el && el.nodeType === ELEMENT_NODE; el = el.parentNode) if (matchList(el, list, el)) return el;
      return null;
    }
    // Layout stubs
    getBoundingClientRect() {
      return { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: function () {} };
    }
    getClientRects() {
      return [];
    }
    get offsetWidth() {
      return 0;
    }
    get offsetHeight() {
      return 0;
    }
    get offsetTop() {
      return 0;
    }
    get offsetLeft() {
      return 0;
    }
    get offsetParent() {
      return null;
    }
    get clientWidth() {
      return 0;
    }
    get clientHeight() {
      return 0;
    }
    get scrollWidth() {
      return 0;
    }
    get scrollHeight() {
      return 0;
    }
    scrollIntoView() {}
    scrollTo() {}
    scrollBy() {}
    focus() {}
    blur() {}
    click() {
      this.dispatchEvent(new Event("click", { bubbles: true, cancelable: true }));
    }
    attachShadow() {
      var root = new DocumentFragment();
      root.host = this;
      root.ownerDocument = this.ownerDocument;
      this.shadowRoot = root;
      return root;
    }
    animate() {
      return { finished: Promise.resolve(), cancel: function () {}, play: function () {}, pause: function () {} };
    }
  }
  mixin(Element.prototype, parentMixin);

  class HTMLElement extends Element {
    get hidden() {
      return this.hasAttribute("hidden");
    }
    set hidden(v) {
      if (v) this.setAttribute("hidden", "");
      else this.removeAttribute("hidden");
    }
    get dir() {
      return this.getAttribute("dir") || "";
    }
    set dir(v) {
      this.setAttribute("dir", v);
    }
    get contentEditable() {
      return this.getAttribute("contenteditable") || "inherit";
    }
    get isContentEditable() {
      return false;
    }
  }

  // Reflected string attributes.
  function reflect(proto, props) {
    props.split(" ").forEach(function (prop) {
      var attr = prop.toLowerCase();
      if (Object.getOwnPropertyDescriptor(proto, prop)) return;
      Object.defineProperty(proto, prop, {
        get: function () {
          var v = this.getAttribute(attr);
          return v === null ? "" : v;
        },
        set: function (v) {
          this.setAttribute(attr, v);
        },
        configurable: true,
      });
    });
  }
  function reflectURL(proto, props) {
    props.split(" ").forEach(function (prop) {
      var attr = prop.toLowerCase();
      Object.defineProperty(proto, prop, {
        get: function () {
          var v = this.getAttribute(attr);
          if (v === null) return "";
          var resolved = env.resolveURL(v.trim(), this.baseURI);
          return resolved === null ? v : resolved;
        },
        set: function (v) {
          this.setAttribute(attr, v);
        },
        configurable: true,
      });
    });
  }
  function reflectBool(proto, props) {
    props.split(" ").forEach(function (prop) {
      var attr = prop.toLowerCase();
      Object.defineProperty(proto, prop, {
        get: function () {
          return this.hasAttribute(attr);
        },
        set: function (v) {
          if (v) this.setAttribute(attr, "");
          else this.removeAttribute(attr);
        },
        configurable: true,
      });
    });
  }
  function reflectNumber(proto, props) {
    props.split(" ").forEach(function (prop) {
      var attr = prop.toLowerCase();
      Object.defineProperty(proto, prop, {
        get: function () {
          var v = parseInt(this.getAttribute(attr), 10);
          return isNaN(v) ? 0 : v;
        },
        set: function (v) {
          this.setAttribute(attr, String(v));
        },
        configurable: true,
      });
    });
  }
  reflect(HTMLElement.prototype, "title lang accessKey translate slot");

  var elementClasses = {};
  function defineElement(name, tags, setup) {
    var ctor = class extends HTMLElement {};
    Object.defineProperty(ctor, "name", { value: name });
    if (setup) setup(ctor.prototype);
    global[name] = ctor;
    tags.split(" ").forEach(function (tag) {
      if (tag) elementClasses[tag] = ctor;
    });
    return ctor;
  }

  function urlParts(proto) {
    ["protocol", "host", "hostname", "port", "pathname", "search", "hash", "origin", "username", "password"].forEach(function (part) {
      Object.defineProperty(proto, part, {
        get: function () {
          var href = this.href;
          try {
            return new URL(href)[part];
          } catch (e) {
            return "";
          }
        },
        set: function (v) {
          try {
            var u = new URL(this.href);
            u[part] = v;
            this.setAttribute("href", u.href);
          } catch (e) {}
        },
        configurable: true,
      });
    });
    proto.toString = function () {
      return this.href;
    };
  }

  defineElement("HTMLAnchorElement", "a", function (p) {
    reflectURL(p, "href");
    reflect(p, "target download rel hreflang type referrerPolicy ping");
    Object.defineProperty(p, "text", {
      get: function () {
        return this.textContent;
      },
      set: function (v) {
        this.textContent = v;
      },
    });
    urlParts(p);
  });
  defineElement("HTMLAreaElement", "area", function (p) {
    reflectURL(p, "href");
    reflect(p, "alt coords shape target download rel");
    urlParts(p);
  });
  defineElement("HTMLImageElement", "img", function (p) {
    reflectURL(p, "src");
    reflect(p, "alt srcset sizes crossOrigin useMap referrerPolicy decoding loading");
    reflectNumber(p, "width height");
    reflectBool(p, "isMap");
    Object.defineProperty(p, "currentSrc", {
      get: function () {
        return this.src;
      },
    });
    Object.defineProperty(p, "complete", {
      get: function () {
        return true;
      },
    });
    Object.defineProperty(p, "naturalWidth", {
      get: function () {
        return this.width;
      },
    });
    Object.defineProperty(p, "naturalHeight", {
      get: function () {
        return this.height;
      },
    });
    p.decode = function () {
      return Promise.resolve();
    };
  });
  defineElement("HTMLLinkElement", "link", function (p) {
    reflectURL(p, "href");
    reflect(p, "rel media hreflang type as crossOrigin referrerPolicy sizes");
  });
  defineElement("HTMLScriptElement", "script", function (p) {
    reflectURL(p, "src");
    reflect(p, "type charset crossOrigin referrerPolicy integrity");
    reflectBool(p, "async defer noModule");
    Object.defineProperty(p, "text", {
      get: function () {
        return this.textContent;
      },
      set: function (v) {
        this.textContent = v;
      },
    });
  });
  defineElement("HTMLIFrameElement", "iframe", function (p) {
    reflectURL(p, "src");
    reflect(p, "srcdoc name allow referrerPolicy width height");
    Object.defineProperty(p, "contentWindow", {
      get: function () {
        return null;
      },
    });
    Object.defineProperty(p, "contentDocument", {
      get: function () {
        return null;
      },
    });
    Object.defineProperty(p, "sandbox", {
      get: function () {
        return new DOMTokenList(this, "sandbox");
      },
    });
  });
  defineElement("HTMLMetaElement", "meta", function (p) {
    reflect(p, "name content httpEquiv charset media scheme");
    Object.defineProperty(p, "httpEquiv", {
      get: function () {
        return this.getAttribute("http-equiv") || "";
      },
    });
  });
  defineElement("HTMLBaseElement", "base", function (p) {
    Object.defineProperty(p, "href", {
      get: function () {
        var v = this.getAttribute("href") || "";
        var doc = this.ownerDocument;
        var resolved = env.resolveURL(v, doc ? doc.URL : "about:blank");
        return resolved === null ? v : resolved;
      },
      set: function (v) {
        this.setAttribute("href", v);
      },
    });
    reflect(p, "target");
  });
  function valueProp(p, fromText) {
    Object.defineProperty(p, "value", {
      get: function () {
        if (this._value !== undefined) return this._value;
        if (fromText) return this.textContent;
        var v = this.getAttribute("value");
        return v === null ? "" : v;
      },
      set: function (v) {
        this._value = String(v);
      },
    });
    reflect(p, "defaultValue");
  }
  defineElement("HTMLInputElement", "input", function (p) {
    valueProp(p, false);
    reflect(p, "name type placeholder accept autocomplete pattern min max step");
    reflectBool(p, "checked disabled readOnly required multiple autofocus");
    reflectNumber(p, "size maxLength minLength");
    p.select = function () {};
    p.setSelectionRange = function () {};
  });
  defineElement("HTMLTextAreaElement", "textarea", function (p) {
    valueProp(p, true);
    reflect(p, "name placeholder wrap");
    reflectBool(p, "disabled readOnly required");
    reflectNumber(p, "rows cols maxLength");
    p.select = function () {};
  });
  defineElement("HTMLButtonElement", "button", function (p) {
    valueProp(p, false);
    reflect(p, "name type formAction");
    reflectBool(p, "disabled");
  });
  defineElement("HTMLSelectElement", "select", function (p) {
    reflect(p, "name");
    reflectBool(p, "disabled multiple required");
    Object.defineProperty(p, "options", {
      get: function () {
        return toNodeList(querySelectorAll(this, "option"));
      },
    });
    Object.defineProperty(p, "selectedIndex", {
      get: function () {
        var options = querySelectorAll(this, "option");
        for (var i = 0; i < options.length; i++) if (options[i].hasAttribute("selected")) return i;
        return options.length ? 0 : -1;
      },
    });
    Object.defineProperty(p, "selectedOptions", {
      get: function () {
        var options = querySelectorAll(this, "option");
        var i = this.selectedIndex;
        return toNodeList(i >= 0 ? [options[i]] : []);
      },
    });
    Object.defineProperty(p, "value", {
      get: function () {
        var options = querySelectorAll(this, "option");
        var i = this.selectedIndex;
        return i >= 0 ? options[i].value : "";
      },
      set: function () {},
    });
  });
  defineElement("HTMLOptionElement", "option", function (p) {
    reflectBool(p, "disabled selected defaultSelected");
    reflect(p, "label");
    Object.defineProperty(p, "value", {
      get: function () {
        var v = this.getAttribute("value");
        return v === null ? this.textContent.replace(/\s+/g, " ").trim() : v;
      },
      set: function (v) {
        this.setAttribute("value", v);
      },
    });
    Object.defineProperty(p, "text", {
      get: function () {
        return this.textContent.replace(/\s+/g, " ").trim();
      },
      set: function (v) {
        this.textContent = v;
      },
    });
  });
  defineElement("HTMLFormElement", "form", function (p) {
    reflectURL(p, "action");
    reflect(p, "method enctype name target acceptCharset");
    Object.defineProperty(p, "elements", {
      get: function () {
        return toNodeList(querySelectorAll(this, "input, select, textarea, button"));
      },
    });
    p.submit = function () {};
    p.reset = function () {};
  });
  defineElement("HTMLTemplateElement", "template", function (p) {
    Object.defineProperty(p, "content", {
      get: function () {
        if (!this._content) {
          this._content = new DocumentFragment();
          this._content.ownerDocument = this.ownerDocument;
          while (this.childNodes.length) this._content.appendChild(this.childNodes[0]);
        }
        return this._content;
      },
    });
  });
  defineElement("HTMLTableElement", "table", function (p) {
    Object.defineProperty(p, "rows", {
      get: function () {
        return toNodeList(querySelectorAll(this, ":scope > tr, :scope > tbody > tr, :scope > thead > tr, :scope > tfoot > tr"));
      },
    });
    Object.defineProperty(p, "tBodies", {
      get: function () {
        return toNodeList(this.children.filter(function (c) {
          return c.localName === "tbody";
        }));
      },
    });
  });
  defineElement("HTMLTableRowElement", "tr", function (p) {
    Object.defineProperty(p, "cells", {
      get: function () {
        return toNodeList(this.children.filter(function (c) {
          return c.localName === "td" || c.localName === "th";
        }));
      },
    });
  });
  defineElement("HTMLTableCellElement", "td th", function (p) {
    reflectNumber(p, "colSpan rowSpan");
  });
  defineElement("HTMLTableSectionElement", "tbody thead tfoot");
  defineElement("HTMLMediaElement", "", function (p) {
    reflectURL(p, "src");
  });
  defineElement("HTMLVideoElement", "video", function (p) {
    reflectURL(p, "src poster");
  });
  defineElement("HTMLAudioElement", "audio", function (p) {
    reflectURL(p, "src");
  });
  defineElement("HTMLSourceElement", "source", function (p) {
    reflectURL(p, "src");
    reflect(p, "srcset type media sizes");
  });
  defineElement("HTMLCanvasElement", "canvas", function (p) {
    reflectNumber(p, "width height");
    p.getContext = function () {
      return null;
    };
    p.toDataURL = function () {
      return "data:,";
    };
  });
  defineElement("HTMLDivElement", "div");
  defineElement("HTMLSpanElement", "span");
  defineElement("HTMLParagraphElement", "p");
  defineElement("HTMLBRElement", "br");
  defineElement("HTMLHRElement", "hr");
  defineElement("HTMLHeadingElement", "h1 h2 h3 h4 h5 h6");
  defineElement("HTMLUListElement", "ul");
  defineElement("HTMLOListElement", "ol");
  defineElement("HTMLLIElement", "li");
  defineElement("HTMLDListElement", "dl");
  defineElement("HTMLPreElement", "pre listing xmp");
  defineElement("HTMLQuoteElement", "blockquote q");
  defineElement("HTMLStyleElement", "style", function (p) {
    reflect(p, "media type");
  });
  defineElement("HTMLTitleElement", "title", function (p) {
    Object.defineProperty(p, "text", {
      get: function () {
        return this.textContent;
      },
      set: function (v) {
        this.textContent = v;
      },
    });
  });
  defineElement("HTMLHeadElement", "head");
  defineElement("HTMLBodyElement", "body");
  defineElement("HTMLHtmlElement", "html");
  defineElement("HTMLFontElement", "font", function (p) {
    reflect(p, "color face size");
  });
  defineElement("HTMLLabelElement", "label", function (p) {
    reflect(p, "htmlFor");
  });
  defineElement("HTMLUnknownElement", "");
  global.HTMLMediaElement.prototype.play = function () {
    return Promise.resolve();
  };
  global.HTMLMediaElement.prototype.pause = function () {};

  class SVGElement extends Element {}

  function createElementFor(doc, localName) {
    var name = doc && doc._xml ? String(localName) : String(localName).toLowerCase();
    var ctor = doc && doc._xml ? Element : elementClasses[name] || HTMLElement;
    var saved = currentOwner;
    currentOwner = doc || null;
    try {
      return new ctor(name, doc || null);
    } finally {
      currentOwner = saved;
    }
  }

  // ------------------------------------------------------------------
  // DOMTokenList and style
  // ------------------------------------------------------------------

  class DOMTokenList {
    constructor(el, attr) {
      this._el = el;
      this._attr = attr;
    }
    _tokens() {
      var value = this._el.getAttribute(this._attr);
      return value ? value.trim().split(/[\t\n\f\r ]+/).filter(Boolean) : [];
    }
    _write(tokens) {
      this._el.setAttribute(this._attr, tokens.join(" "));
    }
    get length() {
      return this._tokens().length;
    }
    get value() {
      return this._el.getAttribute(this._attr) || "";
    }
    set value(v) {
      this._el.setAttribute(this._attr, v);
    }
    item(i) {
      return this._tokens()[i] || null;
    }
    contains(token) {
      return this._tokens().indexOf(String(token)) >= 0;
    }
    add() {
      var tokens = this._tokens();
      for (var i = 0; i < arguments.length; i++) if (tokens.indexOf(String(arguments[i])) < 0) tokens.push(String(arguments[i]));
      this._write(tokens);
    }
    remove() {
      var drop = [].map.call(arguments, String);
      this._write(
        this._tokens().filter(function (t) {
          return drop.indexOf(t) < 0;
        })
      );
    }
    toggle(token, force) {
      var has = this.contains(token);
      if (has && force !== true) {
        this.remove(token);
        return false;
      }
      if (!has && force !== false) {
        this.add(token);
        return true;
      }
      return has;
    }
    replace(oldToken, newToken) {
      var tokens = this._tokens();
      var i = tokens.indexOf(oldToken);
      if (i < 0) return false;
      tokens[i] = newToken;
      this._write(tokens);
      return true;
    }
    supports() {
      return true;
    }
    forEach(fn, thisArg) {
      this._tokens().forEach(fn, thisArg);
    }
    entries() {
      return this._tokens().entries();
    }
    keys() {
      return this._tokens().keys();
    }
    values() {
      return this._tokens().values();
    }
    toString() {
      return this.value;
    }
    [Symbol.iterator]() {
      return this._tokens()[Symbol.iterator]();
    }
  }
  global.DOMTokenList = DOMTokenList;

  function parseStyle(text) {
    var out = [];
    String(text || "")
      .split(";")
      .forEach(function (decl) {
        var colon = decl.indexOf(":");
        if (colon < 0) return;
        var name = decl.slice(0, colon).trim().toLowerCase();
        var value = decl.slice(colon + 1).trim();
        var important = /!\s*important$/i.test(value);
        if (important) value = value.replace(/!\s*important$/i, "").trim();
        if (name) out.push([name, value, important]);
      });
    return out;
  }

  function kebab(prop) {
    if (prop === "cssFloat") return "float";
    return String(prop).replace(/[A-Z]/g, "-$&").toLowerCase().replace(/^(webkit|moz|ms)-/, "-$1-");
  }

  function makeStyle(el) {
    var state = { decls: [] };
    var api = {
      _load: function () {
        state.decls = parseStyle(el.getAttribute("style"));
      },
      _save: function () {
        var text = state.decls
          .map(function (d) {
            return d[0] + ": " + d[1] + (d[2] ? " !important" : "") + ";";
          })
          .join(" ");
        if (text) el._attrs[el._find("style")] ? (el._attrs[el._find("style")][1] = text) : el._attrs.push(["style", text]);
        else if (el._find("style") >= 0) el._attrs.splice(el._find("style"), 1);
      },
      getPropertyValue: function (name) {
        name = String(name).toLowerCase();
        for (var i = state.decls.length - 1; i >= 0; i--) if (state.decls[i][0] === name) return state.decls[i][1];
        return "";
      },
      getPropertyPriority: function (name) {
        for (var i = 0; i < state.decls.length; i++) if (state.decls[i][0] === name) return state.decls[i][2] ? "important" : "";
        return "";
      },
      setProperty: function (name, value, priority) {
        name = String(name).toLowerCase();
        state.decls = state.decls.filter(function (d) {
          return d[0] !== name;
        });
        if (value !== null && value !== undefined && String(value) !== "") state.decls.push([name, String(value), priority === "important"]);
        api._save();
      },
      removeProperty: function (name) {
        var old = api.getPropertyValue(name);
        api.setProperty(name, "");
        return old;
      },
      item: function (i) {
        return state.decls[i] ? state.decls[i][0] : "";
      },
    };
    api._load();
    return new Proxy(api, {
      get: function (target, key) {
        if (key in target) return target[key];
        if (key === "cssText") return el.getAttribute("style") || "";
        if (key === "length") return state.decls.length;
        if (typeof key !== "string") return undefined;
        if (/^\d+$/.test(key)) return target.item(Number(key));
        return target.getPropertyValue(kebab(key));
      },
      set: function (target, key, value) {
        if (key === "cssText") {
          el.setAttribute("style", value);
          return true;
        }
        target.setProperty(kebab(key), value);
        return true;
      },
    });
  }

  // ------------------------------------------------------------------
  // Document
  // ------------------------------------------------------------------

  class Document extends Node {
    constructor(options) {
      super();
      options = options || {};
      this.ownerDocument = null;
      this._url = options.url || "about:blank";
      this._xml = !!options.xml;
      this._rendered = !!options.rendered;
      this.contentType = options.contentType || (this._xml ? "application/xml" : "text/html");
      this.characterSet = options.charset || "UTF-8";
      this.readyState = "complete";
      this.visibilityState = "visible";
      this.hidden = false;
      this.compatMode = "CSS1Compat";
      this.designMode = "off";
      this.dir = "";
      this.referrer = "";
      this.defaultView = options.window || null;
      this.location = options.location || null;
      this.implementation = {
        createHTMLDocument: function (title) {
          var doc = createHTMLDocument(global.location ? global.location.href : "about:blank");
          if (title !== undefined) doc.title = title;
          return doc;
        },
        createDocument: function (ns, name) {
          var doc = new Document({ xml: true, url: "about:blank" });
          if (name) doc.appendChild(doc.createElement(name));
          return doc;
        },
        createDocumentType: function (name, publicId, systemId) {
          return new DocumentType(name, publicId, systemId);
        },
        hasFeature: function () {
          return true;
        },
      };
    }
    get nodeType() {
      return DOCUMENT_NODE;
    }
    get nodeName() {
      return "#document";
    }
    get charset() {
      return this.characterSet;
    }
    get inputEncoding() {
      return this.characterSet;
    }
    get URL() {
      return this._url;
    }
    get documentURI() {
      return this._url;
    }
    get domain() {
      try {
        return new URL(this._url).hostname;
      } catch (e) {
        return "";
      }
    }
    get origin() {
      try {
        return new URL(this._url).origin;
      } catch (e) {
        return "null";
      }
    }
    _baseURL() {
      var base = querySelector(this, "base[href]");
      if (base) {
        var resolved = env.resolveURL(base.getAttribute("href"), this._url);
        if (resolved) return resolved;
      }
      return this._url;
    }
    get doctype() {
      for (var i = 0; i < this.childNodes.length; i++) if (this.childNodes[i].nodeType === DOCUMENT_TYPE_NODE) return this.childNodes[i];
      return null;
    }
    get documentElement() {
      return this.firstElementChild;
    }
    get head() {
      var html = this.documentElement;
      if (!html) return null;
      for (var i = 0; i < html.childNodes.length; i++) if (html.childNodes[i].localName === "head") return html.childNodes[i];
      return null;
    }
    get body() {
      var html = this.documentElement;
      if (!html) return null;
      for (var i = 0; i < html.childNodes.length; i++) {
        var name = html.childNodes[i].localName;
        if (name === "body" || name === "frameset") return html.childNodes[i];
      }
      return null;
    }
    set body(el) {
      var html = this.documentElement;
      var old = this.body;
      if (old) html.replaceChild(el, old);
      else html.appendChild(el);
    }
    get title() {
      var el = querySelector(this, "title");
      return el ? el.textContent.replace(/[\t\n\f\r ]+/g, " ").trim() : "";
    }
    set title(v) {
      var el = querySelector(this, "title");
      if (!el) {
        var head = this.head;
        if (!head) return;
        el = this.createElement("title");
        head.appendChild(el);
      }
      el.textContent = v;
    }
    get scrollingElement() {
      return this.documentElement;
    }
    get activeElement() {
      return this.body;
    }
    get images() {
      return this.getElementsByTagName("img");
    }
    get links() {
      return toNodeList(querySelectorAll(this, "a[href], area[href]"));
    }
    get forms() {
      return this.getElementsByTagName("form");
    }
    get scripts() {
      return this.getElementsByTagName("script");
    }
    get anchors() {
      return toNodeList(querySelectorAll(this, "a[name]"));
    }
    get textContent() {
      return null;
    }
    get cookie() {
      try {
        return String(env.native.cookies(this._url) || "");
      } catch (e) {
        return "";
      }
    }
    set cookie(value) {
      try {
        env.native.setCookie(this._url, String(value));
      } catch (e) {}
    }
    get lastModified() {
      return new Date().toLocaleString();
    }
    get fonts() {
      return { ready: Promise.resolve(), check: function () {
        return true;
      }, add: function () {}, forEach: function () {} };
    }
    _cloneShallow() {
      return new Document({ url: this._url, xml: this._xml, charset: this.characterSet, contentType: this.contentType });
    }
    createElement(name) {
      return createElementFor(this, name);
    }
    createElementNS(ns, name) {
      var local = String(name).replace(/^.*:/, "");
      if (ns === XHTML_NS || !ns) return createElementFor(this, local);
      var saved = currentOwner;
      currentOwner = this;
      try {
        var el = new SVGElement(local, this);
        el._xml = true;
        el.namespaceURI = ns;
        return el;
      } finally {
        currentOwner = saved;
      }
    }
    createTextNode(data) {
      var t = new Text(data);
      t.ownerDocument = this;
      return t;
    }
    createComment(data) {
      var c = new Comment(data);
      c.ownerDocument = this;
      return c;
    }
    createCDATASection(data) {
      var c = new CDATASection(data);
      c.ownerDocument = this;
      return c;
    }
    createProcessingInstruction(target, data) {
      var p = new ProcessingInstruction(target, data);
      p.ownerDocument = this;
      return p;
    }
    createDocumentFragment() {
      var f = new DocumentFragment();
      f.ownerDocument = this;
      return f;
    }
    createAttribute(name) {
      return new Attr(String(name).toLowerCase(), "", null);
    }
    createEvent(type) {
      var ev = new Event("");
      ev.initEvent = function (t, bubbles, cancelable) {
        this.type = t;
        this.bubbles = !!bubbles;
        this.cancelable = !!cancelable;
      };
      ev.initCustomEvent = function (t, bubbles, cancelable, detail) {
        this.initEvent(t, bubbles, cancelable);
        this.detail = detail;
      };
      return ev;
    }
    createRange() {
      return new Range(this);
    }
    createTreeWalker(root, whatToShow, filter) {
      return new TreeWalker(root, whatToShow, filter);
    }
    createNodeIterator(root, whatToShow, filter) {
      var walker = new TreeWalker(root, whatToShow, filter);
      var started = false;
      return {
        root: root,
        nextNode: function () {
          if (!started) {
            started = true;
            if (walker._accept(root) === NodeFilter.FILTER_ACCEPT) return root;
          }
          return walker.nextNode();
        },
        previousNode: function () {
          return walker.previousNode();
        },
        detach: function () {},
      };
    }
    importNode(node, deep) {
      var copy = node.cloneNode(!!deep);
      adopt(copy, this);
      return copy;
    }
    adoptNode(node) {
      if (node.parentNode) node.parentNode._detach(node);
      adopt(node, this);
      return node;
    }
    getElementsByName(name) {
      return toNodeList(
        descendants(this).filter(function (el) {
          return el.getAttribute("name") === String(name);
        })
      );
    }
    hasFocus() {
      return true;
    }
    getSelection() {
      return null;
    }
    elementFromPoint() {
      return null;
    }
    elementsFromPoint() {
      return [];
    }
    open() {
      return this;
    }
    close() {}
    write() {}
    writeln() {}
    execCommand() {
      return false;
    }
    queryCommandSupported() {
      return false;
    }
  }
  mixin(Document.prototype, parentMixin);
  // The ParentNode getElementById walks descendants; documents need the same.

  function createHTMLDocument(url, options) {
    options = options || {};
    var doc = new Document({ url: url, rendered: options.rendered, charset: options.charset, window: options.window, location: options.location });
    var saved = currentOwner;
    currentOwner = doc;
    try {
      doc.appendChild(new DocumentType("html"));
      var html = createElementFor(doc, "html");
      html.appendChild(createElementFor(doc, "head"));
      html.appendChild(createElementFor(doc, "body"));
      doc.appendChild(html);
    } finally {
      currentOwner = saved;
    }
    return doc;
  }

  // ------------------------------------------------------------------
  // HTML parser
  // ------------------------------------------------------------------

  function TreeBuilder(doc, context, xml) {
    this.doc = doc;
    this.xml = !!xml;
    this.fragment = !!context;
    this.html = null;
    this.head = null;
    this.body = null;
    this.headClosed = false;
    if (context) {
      this.root = createElementFor(doc, context.localName || "body");
      this.stack = [this.root];
    } else {
      this.stack = [];
    }
  }

  TreeBuilder.prototype = {
    top: function () {
      return this.stack[this.stack.length - 1] || this.doc;
    },
    append: function (parent, node) {
      node.parentNode = parent;
      node.ownerDocument = this.doc;
      parent.childNodes.push(node);
    },
    ensureHtml: function () {
      if (this.html) return;
      this.html = createElementFor(this.doc, "html");
      this.append(this.doc, this.html);
      this.stack = [this.html];
    },
    ensureHead: function () {
      this.ensureHtml();
      if (!this.head) {
        this.head = createElementFor(this.doc, "head");
        this.append(this.html, this.head);
        this.stack = [this.html, this.head];
      }
    },
    ensureBody: function () {
      if (this.body) return;
      this.ensureHead();
      this.headClosed = true;
      this.body = createElementFor(this.doc, "body");
      this.append(this.html, this.body);
      this.stack = [this.html, this.body];
    },
    inBody: function () {
      return this.fragment || this.xml || !!this.body;
    },
    mergeAttrs: function (el, attrs) {
      attrs.forEach(function (a) {
        if (!el.hasAttribute(a[0])) el._attrs.push([a[0], a[1]]);
      });
    },
    text: function (data) {
      if (!data) return;
      if (!this.inBody()) {
        if (/^[\t\n\f\r ]*$/.test(data)) {
          if (this.head && !this.headClosed) this.appendText(this.top(), data);
          return;
        }
        var lead = /^[\t\n\f\r ]*/.exec(data)[0];
        if (lead && this.head && !this.headClosed) this.appendText(this.top(), lead);
        data = data.slice(lead.length);
        this.ensureBody();
      }
      var parent = this.top();
      if (parent === this.doc) return;
      this.appendText(parent, data);
    },
    appendText: function (parent, data) {
      var last = parent.childNodes[parent.childNodes.length - 1];
      if (last && last.nodeType === TEXT_NODE) last.data += data;
      else this.append(parent, new Text(data));
    },
    comment: function (data) {
      var parent = this.stack.length ? this.top() : this.fragment ? this.root : this.doc;
      this.append(parent, new Comment(data));
    },
    doctype: function (name) {
      if (!this.fragment && !this.html) this.append(this.doc, new DocumentType(name || "html"));
    },
    hasInScope: function (name, extra) {
      for (var i = this.stack.length - 1; i >= 0; i--) {
        var tag = this.stack[i].localName;
        if (tag === name) return true;
        if (SCOPE_BOUNDARY[tag] || (extra && extra[tag]) || this.stack[i] === this.root) return false;
      }
      return false;
    },
    popTo: function (name) {
      for (var i = this.stack.length - 1; i >= 0; i--) {
        if (this.stack[i] === this.root) return false;
        if (this.stack[i].localName === name) {
          this.stack.length = i;
          return true;
        }
      }
      return false;
    },
    popToAny: function (names, boundary) {
      for (var i = this.stack.length - 1; i >= 0; i--) {
        var tag = this.stack[i].localName;
        if (this.stack[i] === this.root || (boundary && boundary[tag])) return false;
        if (names[tag]) {
          this.stack.length = i;
          return true;
        }
      }
      return false;
    },
    inForeign: function () {
      for (var i = this.stack.length - 1; i >= 0; i--) {
        var tag = this.stack[i].localName;
        if (tag === "svg" || tag === "math") return true;
        if (tag === "foreignobject") return false;
      }
      return false;
    },
    insert: function (name, attrs) {
      var el = createElementFor(this.doc, name);
      el._attrs = attrs;
      this.append(this.top(), el);
      return el;
    },
    startTag: function (name, attrs, selfClosing) {
      if (this.xml) {
        var xmlEl = this.insert(name, attrs);
        if (!selfClosing) this.stack.push(xmlEl);
        return xmlEl;
      }
      if (name === "html") {
        if (this.fragment) return null;
        this.ensureHtml();
        this.mergeAttrs(this.html, attrs);
        return null;
      }
      if (!this.fragment && !this.body) {
        if (name === "head") {
          this.ensureHead();
          this.mergeAttrs(this.head, attrs);
          return null;
        }
        if (HEAD_TAGS[name] && !this.headClosed) {
          this.ensureHead();
        } else if (name === "body" || name === "frameset") {
          this.ensureBody();
          this.mergeAttrs(this.body, attrs);
          return null;
        } else if (name === "noscript" && !this.headClosed) {
          this.ensureHead();
        } else {
          this.ensureBody();
        }
      } else if (name === "body") {
        if (this.body) this.mergeAttrs(this.body, attrs);
        return null;
      } else if (name === "head") {
        return null;
      }
      var foreign = this.inForeign() || name === "svg" || name === "math";
      if (!foreign) {
        if (CLOSES_P[name] && this.hasInScope("p", { button: true })) this.popTo("p");
        if (name === "li") this.popToAny({ li: true }, { ol: true, ul: true, div: false, table: true, td: true, th: true });
        else if (name === "dd" || name === "dt") this.popToAny({ dd: true, dt: true }, { dl: true, table: true, td: true, th: true });
        else if (HEADINGS[name] && HEADINGS[this.top().localName]) this.stack.pop();
        else if (name === "option" && this.top().localName === "option") this.stack.pop();
        else if (name === "optgroup") this.popToAny({ option: true, optgroup: true }, { select: true });
        else if (name === "a" && this.hasInScope("a")) this.popTo("a");
        else if (name === "button" && this.hasInScope("button")) this.popTo("button");
        else if (name === "nobr" && this.hasInScope("nobr")) this.popTo("nobr");
        else if (name === "td" || name === "th") {
          this.popToAny({ td: true, th: true }, TABLE_SCOPE);
          var t = this.top().localName;
          if (t === "table") this.stack.push(this.insert("tbody", []));
          t = this.top().localName;
          if (t === "tbody" || t === "thead" || t === "tfoot") this.stack.push(this.insert("tr", []));
        } else if (name === "tr") {
          this.popToAny({ tr: true }, TABLE_SCOPE);
          if (this.top().localName === "table") this.stack.push(this.insert("tbody", []));
        } else if (name === "tbody" || name === "thead" || name === "tfoot") {
          this.popToAny({ tbody: true, thead: true, tfoot: true, tr: true, td: true, th: true }, TABLE_SCOPE);
        } else if (name === "caption" || name === "colgroup") {
          this.popToAny({ tbody: true, thead: true, tfoot: true, tr: true, td: true, th: true, caption: true, colgroup: true }, TABLE_SCOPE);
        } else if (name === "table" && this.top().localName === "table") {
          this.stack.pop();
        }
      }
      var el = this.insert(name, attrs);
      if (foreign && name !== "svg" && name !== "math") el._xml = false;
      if (VOID[name] || (selfClosing && foreign)) return el;
      this.stack.push(el);
      return el;
    },
    endTag: function (name) {
      if (this.xml) {
        this.popTo(name);
        return;
      }
      if (name === "html" || name === "body") return;
      if (name === "head") {
        if (!this.fragment && this.head && !this.headClosed) {
          this.headClosed = true;
          this.stack = [this.html];
        }
        return;
      }
      if (!this.fragment && !this.body) {
        if (name === "br" || name === "p") this.ensureBody();
        else {
          this.popTo(name);
          return;
        }
      }
      if (name === "br") {
        this.insert("br", []);
        return;
      }
      if (name === "p" && !this.hasInScope("p", { button: true })) {
        this.insert("p", []);
        return;
      }
      if (SPECIAL[name] || FORMATTING[name]) {
        // Closing a block (or formatting) element pops everything opened inside it.
        var extra = name === "li" ? LIST_SCOPE : null;
        if (this.hasInScope(name, extra) || (TABLE_PARTS[name] && this.hasInScope(name, null))) this.popTo(name);
        return;
      }
      for (var i = this.stack.length - 1; i >= 0; i--) {
        var node = this.stack[i];
        if (node === this.root) return;
        var tag = node.localName;
        if (tag === name) {
          this.stack.length = i;
          return;
        }
        if (SPECIAL[tag]) return;
      }
    },
    finish: function () {
      if (!this.fragment && !this.xml) this.ensureBody();
    },
  };

  var TAG_NAME = /[^\t\n\f\r \/>]+/y;
  var ATTR_NAME = /[^\t\n\f\r \/>][^\t\n\f\r \/>=]*/y;
  var SPACES = /[\t\n\f\r \/]*/y;

  function readTag(src, pos, builder, endTag, xml) {
    // pos points just after "<" or "</".
    TAG_NAME.lastIndex = pos;
    var m = TAG_NAME.exec(src);
    var name = xml ? m[0] : m[0].toLowerCase();
    pos = TAG_NAME.lastIndex;
    var attrs = [];
    var seen = Object.create(null);
    var selfClosing = false;
    for (;;) {
      SPACES.lastIndex = pos;
      var sp = SPACES.exec(src);
      selfClosing = sp[0].charAt(sp[0].length - 1) === "/";
      pos = SPACES.lastIndex;
      if (pos >= src.length) return { pos: pos, name: name, attrs: attrs, selfClosing: false, eof: true };
      if (src.charAt(pos) === ">") {
        pos++;
        break;
      }
      selfClosing = false;
      ATTR_NAME.lastIndex = pos;
      var am = ATTR_NAME.exec(src);
      var attrName = xml ? am[0] : am[0].toLowerCase();
      pos = ATTR_NAME.lastIndex;
      var value = "";
      var ws = /[\t\n\f\r ]*/y;
      ws.lastIndex = pos;
      ws.exec(src);
      if (src.charAt(ws.lastIndex) === "=") {
        pos = ws.lastIndex + 1;
        ws.lastIndex = pos;
        ws.exec(src);
        pos = ws.lastIndex;
        var quote = src.charAt(pos);
        if (quote === '"' || quote === "'") {
          var close = src.indexOf(quote, pos + 1);
          if (close < 0) close = src.length;
          value = src.slice(pos + 1, close);
          pos = close + 1;
        } else {
          var uq = /[^\t\n\f\r >]*/y;
          uq.lastIndex = pos;
          value = uq.exec(src)[0];
          pos = uq.lastIndex;
        }
        value = decodeEntities(value, true);
      }
      if (!seen[attrName]) {
        seen[attrName] = true;
        attrs.push([attrName, value]);
      }
    }
    return { pos: pos, name: name, attrs: attrs, selfClosing: selfClosing };
  }

  function findRawEnd(src, pos, name) {
    var re = new RegExp("</" + name + "(?=[\\t\\n\\f\\r />])", "ig");
    re.lastIndex = pos;
    var m = re.exec(src);
    return m ? m.index : -1;
  }

  function runParser(src, builder, xml) {
    src = String(src).replace(/\r\n?/g, "\n");
    var pos = 0;
    var n = src.length;
    while (pos < n) {
      var lt = src.indexOf("<", pos);
      if (lt < 0) {
        builder.text(decodeEntities(src.slice(pos), false));
        break;
      }
      if (lt > pos) builder.text(decodeEntities(src.slice(pos, lt), false));
      pos = lt;
      var c = src.charAt(lt + 1);
      if (c === "!") {
        if (src.startsWith("<!--", lt)) {
          if (src.startsWith("<!-->", lt) || src.startsWith("<!--->", lt)) {
            builder.comment("");
            pos = src.indexOf(">", lt + 4) + 1;
            continue;
          }
          var endComment = src.indexOf("-->", lt + 4);
          if (endComment < 0) endComment = n;
          builder.comment(src.slice(lt + 4, endComment));
          pos = endComment + 3;
        } else if (src.startsWith("<![CDATA[", lt)) {
          var endCdata = src.indexOf("]]>", lt + 9);
          if (endCdata < 0) endCdata = n;
          if (xml || builder.inForeign()) builder.text(src.slice(lt + 9, endCdata));
          else builder.comment("[CDATA[" + src.slice(lt + 9, endCdata) + "]]");
          pos = endCdata + 3;
        } else {
          var endDecl = src.indexOf(">", lt);
          if (endDecl < 0) endDecl = n;
          var decl = src.slice(lt + 2, endDecl);
          var dt = /^doctype\s*([^\s>]*)/i.exec(decl);
          if (dt) builder.doctype(dt[1].toLowerCase());
          else builder.comment(decl);
          pos = endDecl + 1;
        }
        continue;
      }
      if (c === "?") {
        var endPi = src.indexOf(">", lt);
        if (endPi < 0) endPi = n;
        if (!xml) builder.comment(src.slice(lt + 1, endPi));
        pos = endPi + 1;
        continue;
      }
      if (c === "/") {
        var c2 = src.charAt(lt + 2);
        if (/[A-Za-z]/.test(c2)) {
          var end = readTag(src, lt + 2, builder, true, xml);
          builder.endTag(end.name);
          pos = end.pos;
        } else if (c2 === ">") {
          pos = lt + 3;
        } else {
          var endBogus = src.indexOf(">", lt);
          if (endBogus < 0) endBogus = n;
          builder.comment(src.slice(lt + 2, endBogus));
          pos = endBogus + 1;
        }
        continue;
      }
      if (/[A-Za-z]/.test(c)) {
        var tag = readTag(src, lt + 1, builder, false, xml);
        if (tag.eof) {
          pos = n;
          break;
        }
        builder.startTag(tag.name, tag.attrs, tag.selfClosing);
        pos = tag.pos;
        if (!xml && !builder.inForeign()) {
          if (RAW_TEXT[tag.name] || ESCAPABLE_RAW[tag.name] || (tag.name === "noscript" && builder.doc._rendered)) {
            var rawEnd = findRawEnd(src, pos, tag.name);
            if (rawEnd < 0) rawEnd = n;
            var raw = src.slice(pos, rawEnd);
            if (ESCAPABLE_RAW[tag.name]) raw = decodeEntities(raw, false);
            if (tag.name === "textarea" && raw.charAt(0) === "\n") raw = raw.slice(1);
            if (raw) builder.appendText(builder.top(), raw);
            builder.endTag(tag.name);
            if (rawEnd >= n) {
              pos = n;
              break;
            }
            pos = src.indexOf(">", rawEnd) + 1 || n;
          } else if (tag.name === "plaintext") {
            builder.appendText(builder.top(), src.slice(pos));
            pos = n;
          } else if ((tag.name === "pre" || tag.name === "listing") && src.charAt(pos) === "\n") {
            pos++;
          }
        }
        continue;
      }
      builder.text("<");
      pos = lt + 1;
    }
    builder.finish();
  }

  function parseDocument(html, options) {
    options = options || {};
    var doc = new Document({
      url: options.url || "about:blank",
      rendered: options.rendered,
      charset: options.charset,
      xml: options.xml,
      contentType: options.contentType,
      window: options.window,
      location: options.location,
    });
    var saved = currentOwner;
    currentOwner = doc;
    try {
      runParser(html, new TreeBuilder(doc, null, options.xml), options.xml);
    } finally {
      currentOwner = saved;
    }
    return doc;
  }

  function parseFragment(html, context) {
    var doc = context.ownerDocument || global.document;
    var saved = currentOwner;
    currentOwner = doc;
    try {
      var builder = new TreeBuilder(doc, context, context._xml);
      runParser(html, builder, context._xml);
      var frag = new DocumentFragment();
      frag.ownerDocument = doc;
      var kids = builder.root.childNodes.slice();
      for (var i = 0; i < kids.length; i++) {
        kids[i].parentNode = frag;
        frag.childNodes.push(kids[i]);
      }
      builder.root.childNodes = [];
      return frag;
    } finally {
      currentOwner = saved;
    }
  }

  // ------------------------------------------------------------------
  // Serialization
  // ------------------------------------------------------------------

  function escapeText(s) {
    return s.replace(/&/g, "&amp;").replace(/ /g, "&nbsp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function escapeAttr(s) {
    return s.replace(/&/g, "&amp;").replace(/ /g, "&nbsp;").replace(/"/g, "&quot;");
  }
  function escapeXmlText(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function escapeXmlAttr(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/\t/g, "&#9;").replace(/\n/g, "&#10;").replace(/\r/g, "&#13;");
  }

  function serializeChildren(node, xml) {
    var out = [];
    for (var i = 0; i < node.childNodes.length; i++) serializeInto(node.childNodes[i], xml, out, node);
    return out.join("");
  }

  function serializeNode(node, xml) {
    var out = [];
    serializeInto(node, xml, out, null, true);
    return out.join("");
  }

  function serializeInto(node, xml, out, parent, isRoot) {
    switch (node.nodeType) {
      case ELEMENT_NODE: {
        var tag = node._xml ? node.localName : node.localName;
        out.push("<", tag);
        if (xml && isRoot && node.namespaceURI && !node.hasAttribute("xmlns")) out.push(' xmlns="', node.namespaceURI, '"');
        for (var i = 0; i < node._attrs.length; i++) {
          var a = node._attrs[i];
          out.push(" ", a[0], '="', xml ? escapeXmlAttr(a[1]) : escapeAttr(a[1]), '"');
        }
        var kids = node.localName === "template" && node._content ? node._content.childNodes : node.childNodes;
        if (xml) {
          if (!kids.length && (VOID[node.localName] || node._xml)) {
            out.push(" />");
            return;
          }
        }
        out.push(">");
        if (!xml && VOID[node.localName]) return;
        for (var k = 0; k < kids.length; k++) serializeInto(kids[k], xml, out, node);
        out.push("</", tag, ">");
        return;
      }
      case TEXT_NODE: {
        var p = parent || node.parentNode;
        if (!xml && p && p.nodeType === ELEMENT_NODE && (RAW_TEXT[p.localName] || p.localName === "plaintext" || (p.localName === "noscript" && p.ownerDocument && p.ownerDocument._rendered))) out.push(node.data);
        else out.push(xml ? escapeXmlText(node.data) : escapeText(node.data));
        return;
      }
      case CDATA_SECTION_NODE:
        out.push("<![CDATA[", node.data, "]]>");
        return;
      case COMMENT_NODE:
        out.push("<!--", node.data, "-->");
        return;
      case PROCESSING_INSTRUCTION_NODE:
        out.push("<?", node.target, " ", node.data, "?>");
        return;
      case DOCUMENT_TYPE_NODE:
        out.push("<!DOCTYPE ", node.name, ">");
        return;
      case DOCUMENT_NODE:
      case DOCUMENT_FRAGMENT_NODE:
        for (var j = 0; j < node.childNodes.length; j++) serializeInto(node.childNodes[j], xml, out, node, xml && node.nodeType === DOCUMENT_NODE);
        return;
    }
  }

  // ------------------------------------------------------------------
  // innerText
  // ------------------------------------------------------------------

  function isRendered(el) {
    var doc = el.ownerDocument;
    if (!doc || !doc._rendered) return false;
    var node = el;
    while (node.parentNode) node = node.parentNode;
    if (node !== doc) return false;
    for (var p = el; p && p.nodeType === ELEMENT_NODE; p = p.parentNode) if (isHiddenElement(p)) return false;
    return true;
  }

  function isHiddenElement(el) {
    if (HIDDEN[el.localName] && el.localName !== "head") return true;
    if (el.hasAttribute("hidden")) return true;
    var style = el.getAttribute("style");
    return !!style && /(^|;)\s*display\s*:\s*none/i.test(style);
  }

  function renderedText(root) {
    var items = [];
    function pushBreak(n) {
      items.push(n);
    }
    function walk(node, pre) {
      for (var i = 0; i < node.childNodes.length; i++) {
        var child = node.childNodes[i];
        if (child.nodeType === TEXT_NODE || child.nodeType === CDATA_SECTION_NODE) {
          items.push(pre ? { pre: child.data } : child.data.replace(/[\t\n\f\r ]+/g, " "));
          continue;
        }
        if (child.nodeType !== ELEMENT_NODE || isHiddenElement(child)) continue;
        var tag = child.localName;
        if (tag === "br") {
          items.push({ pre: "\n" });
          continue;
        }
        var childPre = pre || tag === "pre" || tag === "textarea" || tag === "listing" || tag === "xmp" || /white-space\s*:\s*pre/i.test(child.getAttribute("style") || "");
        var breaks = tag === "p" ? 2 : BLOCK[tag] || tag === "tr" ? 1 : 0;
        if (/^h[1-6]$/.test(tag)) breaks = 1;
        if (breaks) pushBreak(breaks);
        if ((tag === "td" || tag === "th") && child.previousElementSibling) items.push({ pre: "\t" });
        walk(child, childPre);
        if (breaks) pushBreak(breaks);
      }
    }
    walk(root, root.localName === "pre");
    // Collapse: spaces next to breaks vanish, runs of breaks keep the largest.
    var out = "";
    var pendingBreak = 0;
    var atLineStart = true;
    for (var k = 0; k < items.length; k++) {
      var item = items[k];
      if (typeof item === "number") {
        if (out) pendingBreak = Math.max(pendingBreak, item);
        continue;
      }
      var text = typeof item === "string" ? item : item.pre;
      if (!text) continue;
      var preserved = typeof item !== "string";
      if (!preserved) {
        if (pendingBreak || atLineStart) text = text.replace(/^ +/, "");
        else if (out.charAt(out.length - 1) === " ") text = text.replace(/^ +/, "");
        if (!text) continue;
      }
      if (pendingBreak) {
        out = out.replace(/ +$/, "") + "\n".repeat(pendingBreak);
        pendingBreak = 0;
      }
      out += text;
      atLineStart = out.charAt(out.length - 1) === "\n";
      if (atLineStart) out = out.replace(/ +\n$/, "\n");
    }
    return out.replace(/ +$/, "");
  }

  // ------------------------------------------------------------------
  // CSS selectors
  // ------------------------------------------------------------------

  var selectorCache = new Map();

  function SelectorParser(text) {
    this.s = text;
    this.i = 0;
  }
  SelectorParser.prototype = {
    fail: function () {
      throw DOMException("'" + this.s + "' is not a valid selector.", "SyntaxError");
    },
    ws: function () {
      var start = this.i;
      while (this.i < this.s.length && /[\t\n\f\r ]/.test(this.s[this.i])) this.i++;
      return this.i > start;
    },
    ident: function () {
      var out = "";
      var s = this.s;
      while (this.i < s.length) {
        var ch = s[this.i];
        if (ch === "\\") {
          var hex = /^[0-9a-fA-F]{1,6}[\t\n\f\r ]?/.exec(s.slice(this.i + 1));
          if (hex) {
            out += String.fromCodePoint(parseInt(hex[0], 16) || 0xfffd);
            this.i += 1 + hex[0].length;
          } else {
            out += s[this.i + 1] || "";
            this.i += 2;
          }
        } else if (/[-\w]/.test(ch) || ch.charCodeAt(0) > 127) {
          out += ch;
          this.i++;
        } else break;
      }
      return out;
    },
    string: function () {
      var quote = this.s[this.i];
      this.i++;
      var out = "";
      while (this.i < this.s.length && this.s[this.i] !== quote) {
        if (this.s[this.i] === "\\") {
          var hex = /^[0-9a-fA-F]{1,6}[\t\n\f\r ]?/.exec(this.s.slice(this.i + 1));
          if (hex) {
            out += String.fromCodePoint(parseInt(hex[0], 16) || 0xfffd);
            this.i += 1 + hex[0].length;
            continue;
          }
          this.i++;
        }
        out += this.s[this.i++];
      }
      this.i++;
      return out;
    },
    list: function (relative) {
      var out = [];
      for (;;) {
        this.ws();
        out.push(this.complex(relative));
        this.ws();
        if (this.s[this.i] === ",") {
          this.i++;
          continue;
        }
        break;
      }
      return out;
    },
    complex: function (relative) {
      var parts = [];
      var comb = relative ? " " : null;
      if (relative && /[>+~]/.test(this.s[this.i])) {
        comb = this.s[this.i++];
        this.ws();
      }
      for (;;) {
        var compound = this.compound();
        if (!compound.length) this.fail();
        parts.push({ comb: comb, tests: compound });
        var hadSpace = this.ws();
        var ch = this.s[this.i];
        if (ch === ">" || ch === "+" || ch === "~") {
          comb = ch;
          this.i++;
          this.ws();
        } else if (ch === undefined || ch === "," || ch === ")") break;
        else if (hadSpace) comb = " ";
        else this.fail();
      }
      return { parts: parts, relative: !!relative };
    },
    compound: function () {
      var tests = [];
      var s = this.s;
      if (s[this.i] === "*") {
        this.i++;
        if (s[this.i] === "|") this.i++;
        tests.push({ t: "any" });
      } else if (/[-\w\\]/.test(s[this.i] || "") || (s[this.i] || "").charCodeAt(0) > 127) {
        var tag = this.ident();
        if (s[this.i] === "|") {
          this.i++;
          tag = this.ident();
        }
        tests.push({ t: "tag", v: tag.toLowerCase(), raw: tag });
      }
      for (;;) {
        var ch = s[this.i];
        if (ch === "#") {
          this.i++;
          tests.push({ t: "id", v: this.ident() });
        } else if (ch === ".") {
          this.i++;
          var cls = this.ident();
          if (!cls) this.fail();
          tests.push({ t: "class", v: cls });
        } else if (ch === "[") {
          tests.push(this.attr());
        } else if (ch === ":") {
          tests.push(this.pseudo());
        } else break;
      }
      return tests;
    },
    attr: function () {
      this.i++;
      this.ws();
      var name = this.ident();
      if (this.s[this.i] === "|") {
        this.i++;
        name = this.ident();
      }
      if (!name) this.fail();
      this.ws();
      var op = null, value = null, flag = "";
      var m = /^([~|^$*]?=)/.exec(this.s.slice(this.i));
      if (m) {
        op = m[1];
        this.i += op.length;
        this.ws();
        var q = this.s[this.i];
        value = q === '"' || q === "'" ? this.string() : this.ident();
        this.ws();
        if (/[iIsS]/.test(this.s[this.i] || "") && /[\t\n\f\r \]]/.test(this.s[this.i + 1] || "]")) {
          flag = this.s[this.i].toLowerCase();
          this.i++;
          this.ws();
        }
      }
      if (this.s[this.i] !== "]") this.fail();
      this.i++;
      return { t: "attr", name: name.toLowerCase(), raw: name, op: op, v: value, ci: flag === "i" };
    },
    pseudo: function () {
      this.i++;
      if (this.s[this.i] === ":") {
        this.i++;
        this.ident();
        if (this.s[this.i] === "(") this.skipArgs();
        return { t: "never" };
      }
      var name = this.ident().toLowerCase();
      var test = { t: "pseudo", name: name };
      if (this.s[this.i] === "(") {
        this.i++;
        this.ws();
        if (name === "not" || name === "is" || name === "where" || name === "matches" || name === "-webkit-any" || name === "any") test.list = this.list(false);
        else if (name === "has") test.list = this.list(true);
        else if (/^nth-/.test(name)) {
          var argEnd = this.s.indexOf(")", this.i);
          if (argEnd < 0) this.fail();
          var arg = this.s.slice(this.i, argEnd).trim();
          var of = /^(.*?)\s+of\s+(.*)$/i.exec(arg);
          if (of) {
            test.nth = parseNth(of[1]);
            test.of = parseSelector(of[2]);
          } else test.nth = parseNth(arg);
          if (!test.nth) this.fail();
          this.i = argEnd;
        } else if (name === "contains" || name === "lang" || name === "dir") {
          var q = this.s[this.i];
          if (q === '"' || q === "'") test.arg = this.string();
          else {
            var close = this.s.indexOf(")", this.i);
            test.arg = this.s.slice(this.i, close).trim();
            this.i = close;
          }
        } else this.skipArgs(true);
        this.ws();
        if (this.s[this.i] !== ")") this.fail();
        this.i++;
      }
      if (!PSEUDO[name]) this.fail();
      return test;
    },
    skipArgs: function (inside) {
      var depth = inside ? 1 : 0;
      if (!inside) {
        this.i++;
        depth = 1;
      }
      while (this.i < this.s.length && depth) {
        var ch = this.s[this.i];
        if (ch === "(") depth++;
        else if (ch === ")") {
          depth--;
          if (!depth) break;
        }
        this.i++;
      }
      if (!inside) this.i++;
    },
  };

  function parseNth(text) {
    text = text.replace(/\s+/g, "").toLowerCase();
    if (text === "odd") return { a: 2, b: 1 };
    if (text === "even") return { a: 2, b: 0 };
    var m = /^([+-]?\d*)n([+-]\d+)?$/.exec(text);
    if (m) {
      var a = m[1] === "" || m[1] === "+" ? 1 : m[1] === "-" ? -1 : parseInt(m[1], 10);
      return { a: a, b: m[2] ? parseInt(m[2], 10) : 0 };
    }
    if (/^[+-]?\d+$/.test(text)) return { a: 0, b: parseInt(text, 10) };
    return null;
  }

  function nthMatches(nth, index) {
    if (nth.a === 0) return index === nth.b;
    var diff = index - nth.b;
    return diff % nth.a === 0 && diff / nth.a >= 0;
  }

  function parseSelector(text) {
    text = String(text);
    var cached = selectorCache.get(text);
    if (cached) return cached;
    var parser = new SelectorParser(text.trim());
    if (!parser.s) parser.fail();
    var list = parser.list(false);
    if (parser.i < parser.s.length) parser.fail();
    if (selectorCache.size > 2000) selectorCache.clear();
    selectorCache.set(text, list);
    return list;
  }

  function siblingIndex(el, ofType, fromEnd, filter) {
    var p = el.parentNode;
    if (!p) return 1;
    var list = p.childNodes;
    var count = 0;
    var start = fromEnd ? list.length - 1 : 0;
    var step = fromEnd ? -1 : 1;
    for (var i = start; i >= 0 && i < list.length; i += step) {
      var n = list[i];
      if (n.nodeType !== ELEMENT_NODE) continue;
      if (ofType && n.localName !== el.localName) continue;
      if (filter && !matchList(n, filter, null)) continue;
      count++;
      if (n === el) return count;
    }
    return count;
  }

  var PSEUDO = {
    "first-child": function (el) {
      return !el.previousElementSibling;
    },
    "last-child": function (el) {
      return !el.nextElementSibling;
    },
    "only-child": function (el) {
      return !el.previousElementSibling && !el.nextElementSibling;
    },
    "first-of-type": function (el) {
      return siblingIndex(el, true, false) === 1;
    },
    "last-of-type": function (el) {
      return siblingIndex(el, true, true) === 1;
    },
    "only-of-type": function (el) {
      return siblingIndex(el, true, false) === 1 && siblingIndex(el, true, true) === 1;
    },
    "nth-child": function (el, test) {
      if (test.of && !matchList(el, test.of, null)) return false;
      return nthMatches(test.nth, siblingIndex(el, false, false, test.of));
    },
    "nth-last-child": function (el, test) {
      if (test.of && !matchList(el, test.of, null)) return false;
      return nthMatches(test.nth, siblingIndex(el, false, true, test.of));
    },
    "nth-of-type": function (el, test) {
      return nthMatches(test.nth, siblingIndex(el, true, false));
    },
    "nth-last-of-type": function (el, test) {
      return nthMatches(test.nth, siblingIndex(el, true, true));
    },
    not: function (el, test, scope) {
      return !matchList(el, test.list, scope);
    },
    is: function (el, test, scope) {
      return matchList(el, test.list, scope);
    },
    has: function (el, test) {
      for (var i = 0; i < test.list.length; i++) if (hasRelative(el, test.list[i])) return true;
      return false;
    },
    empty: function (el) {
      return !el.childNodes.some(function (n) {
        return n.nodeType === ELEMENT_NODE || ((n.nodeType === TEXT_NODE || n.nodeType === CDATA_SECTION_NODE) && n.data.length > 0);
      });
    },
    root: function (el) {
      return !!el.parentNode && el.parentNode.nodeType === DOCUMENT_NODE;
    },
    scope: function (el, test, scope) {
      if (!scope) return PSEUDO.root(el);
      return scope.nodeType === DOCUMENT_NODE ? el === scope.documentElement : el === scope;
    },
    contains: function (el, test) {
      return textOf(el).indexOf(test.arg) >= 0;
    },
    checked: function (el) {
      return el.hasAttribute("checked") || (el.localName === "option" && el.hasAttribute("selected"));
    },
    selected: function (el) {
      return el.hasAttribute("selected");
    },
    disabled: function (el) {
      return el.hasAttribute("disabled");
    },
    enabled: function (el) {
      return /^(input|button|select|textarea|option|optgroup|fieldset)$/.test(el.localName) && !el.hasAttribute("disabled");
    },
    required: function (el) {
      return el.hasAttribute("required");
    },
    optional: function (el) {
      return /^(input|select|textarea)$/.test(el.localName) && !el.hasAttribute("required");
    },
    "read-only": function (el) {
      return el.hasAttribute("readonly");
    },
    "read-write": function (el) {
      return /^(input|textarea)$/.test(el.localName) && !el.hasAttribute("readonly");
    },
    link: function (el) {
      return /^(a|area)$/.test(el.localName) && el.hasAttribute("href");
    },
    "any-link": function (el) {
      return /^(a|area)$/.test(el.localName) && el.hasAttribute("href");
    },
    lang: function (el, test) {
      for (var n = el; n && n.nodeType === ELEMENT_NODE; n = n.parentNode) {
        var lang = n.getAttribute("lang");
        if (lang !== null) return lang.toLowerCase() === test.arg.toLowerCase() || lang.toLowerCase().indexOf(test.arg.toLowerCase() + "-") === 0;
      }
      return false;
    },
    dir: function () {
      return false;
    },
    visible: function () {
      return true;
    },
    defined: function () {
      return true;
    },
  };
  ["hover", "active", "focus", "focus-within", "focus-visible", "visited", "target", "playing", "paused", "fullscreen", "placeholder-shown", "autofill", "indeterminate", "default", "valid", "invalid", "in-range", "out-of-range", "user-invalid", "modal", "popover-open", "open", "closed", "current", "past", "future", "local-link", "blank", "-webkit-autofill", "host", "state", "first", "left", "right"].forEach(function (name) {
    PSEUDO[name] = function () {
      return false;
    };
  });
  PSEUDO.where = PSEUDO.matches = PSEUDO["-webkit-any"] = PSEUDO.any = PSEUDO.is;

  function matchCompound(el, tests, scope) {
    for (var i = 0; i < tests.length; i++) {
      var t = tests[i];
      switch (t.t) {
        case "any":
          break;
        case "tag":
          if (el._xml ? el.localName !== t.raw && el.localName.toLowerCase() !== t.v : el.localName !== t.v) return false;
          break;
        case "id":
          if (el.getAttribute("id") !== t.v) return false;
          break;
        case "class":
          if (el._classes().indexOf(t.v) < 0) return false;
          break;
        case "attr": {
          var actual = el.getAttribute(el._xml ? t.raw : t.name);
          if (actual === null) return false;
          if (!t.op) break;
          var want = t.v;
          if (t.ci || (t.name === "type" && !el._xml)) {
            actual = actual.toLowerCase();
            want = want.toLowerCase();
          }
          var ok;
          switch (t.op) {
            case "=":
              ok = actual === want;
              break;
            case "~=":
              ok = want !== "" && actual.split(/[\t\n\f\r ]+/).indexOf(want) >= 0;
              break;
            case "|=":
              ok = actual === want || actual.indexOf(want + "-") === 0;
              break;
            case "^=":
              ok = want !== "" && actual.indexOf(want) === 0;
              break;
            case "$=":
              ok = want !== "" && actual.slice(-want.length) === want;
              break;
            case "*=":
              ok = want !== "" && actual.indexOf(want) >= 0;
              break;
          }
          if (!ok) return false;
          break;
        }
        case "pseudo":
          if (!PSEUDO[t.name](el, t, scope)) return false;
          break;
        case "never":
          return false;
      }
    }
    return true;
  }

  function matchFrom(el, parts, k, scope) {
    if (!matchCompound(el, parts[k].tests, scope)) return false;
    if (k === 0) return true;
    var comb = parts[k].comb;
    if (comb === ">") {
      var parent = el.parentElement;
      return !!parent && matchFrom(parent, parts, k - 1, scope);
    }
    if (comb === " ") {
      for (var p = el.parentElement; p; p = p.parentElement) if (matchFrom(p, parts, k - 1, scope)) return true;
      return false;
    }
    if (comb === "+") {
      var prev = el.previousElementSibling;
      return !!prev && matchFrom(prev, parts, k - 1, scope);
    }
    if (comb === "~") {
      for (var s = el.previousElementSibling; s; s = s.previousElementSibling) if (matchFrom(s, parts, k - 1, scope)) return true;
      return false;
    }
    return false;
  }

  function matchList(el, list, scope) {
    for (var i = 0; i < list.length; i++) {
      var parts = list[i].parts;
      if (matchFrom(el, parts, parts.length - 1, scope)) return true;
    }
    return false;
  }

  function hasRelative(anchor, complex) {
    var parts = complex.parts;
    var first = parts[0].comb;
    var candidates = [];
    if (first === " " || first === ">") candidates = descendants(anchor);
    else {
      for (var s = anchor.nextElementSibling; s; s = s.nextElementSibling) {
        candidates.push(s);
        candidates = candidates.concat(descendants(s));
      }
    }
    for (var i = 0; i < candidates.length; i++) {
      if (relativeMatch(candidates[i], parts, parts.length - 1, anchor)) return true;
    }
    return false;
  }

  function relativeMatch(el, parts, k, anchor) {
    if (!matchCompound(el, parts[k].tests, anchor)) return false;
    var comb = parts[k].comb;
    var check = function (node) {
      return k === 0 ? node === anchor : relativeMatch(node, parts, k - 1, anchor);
    };
    if (comb === ">") return !!el.parentNode && check(el.parentNode);
    if (comb === " ") {
      for (var p = el.parentNode; p; p = p.parentNode) if (check(p)) return true;
      return false;
    }
    if (comb === "+") {
      var prev = el.previousElementSibling;
      return !!prev && check(prev);
    }
    if (comb === "~") {
      for (var s = el.previousElementSibling; s; s = s.previousElementSibling) if (check(s)) return true;
    }
    return false;
  }

  function matchesSelector(el, selector, scope) {
    return matchList(el, parseSelector(selector), scope || el);
  }

  function querySelectorAll(root, selector) {
    var list = parseSelector(selector);
    var out = [];
    var stack = root.childNodes.slice().reverse();
    while (stack.length) {
      var node = stack.pop();
      if (node.nodeType !== ELEMENT_NODE) continue;
      if (matchList(node, list, root)) out.push(node);
      for (var i = node.childNodes.length - 1; i >= 0; i--) stack.push(node.childNodes[i]);
    }
    return out;
  }

  function querySelector(root, selector) {
    var list = parseSelector(selector);
    var stack = root.childNodes.slice().reverse();
    while (stack.length) {
      var node = stack.pop();
      if (node.nodeType !== ELEMENT_NODE) continue;
      if (matchList(node, list, root)) return node;
      for (var i = node.childNodes.length - 1; i >= 0; i--) stack.push(node.childNodes[i]);
    }
    return null;
  }

  // ------------------------------------------------------------------
  // TreeWalker, Range, NodeFilter
  // ------------------------------------------------------------------

  var NodeFilter = {
    FILTER_ACCEPT: 1, FILTER_REJECT: 2, FILTER_SKIP: 3,
    SHOW_ALL: 0xffffffff, SHOW_ELEMENT: 0x1, SHOW_ATTRIBUTE: 0x2, SHOW_TEXT: 0x4, SHOW_CDATA_SECTION: 0x8,
    SHOW_PROCESSING_INSTRUCTION: 0x40, SHOW_COMMENT: 0x80, SHOW_DOCUMENT: 0x100, SHOW_DOCUMENT_TYPE: 0x200, SHOW_DOCUMENT_FRAGMENT: 0x400,
  };

  class TreeWalker {
    constructor(root, whatToShow, filter) {
      this.root = root;
      this.whatToShow = whatToShow === undefined ? NodeFilter.SHOW_ALL : whatToShow >>> 0;
      this.filter = filter || null;
      this.currentNode = root;
    }
    _accept(node) {
      if (!((1 << (node.nodeType - 1)) & this.whatToShow)) return NodeFilter.FILTER_SKIP;
      if (!this.filter) return NodeFilter.FILTER_ACCEPT;
      var fn = typeof this.filter === "function" ? this.filter : this.filter.acceptNode;
      return fn ? fn.call(this.filter, node) : NodeFilter.FILTER_ACCEPT;
    }
    _following(node, skipChildren) {
      if (!skipChildren && node.childNodes.length) return node.childNodes[0];
      for (var n = node; n && n !== this.root; n = n.parentNode) {
        var next = n.nextSibling;
        if (next) return next;
      }
      return null;
    }
    nextNode() {
      var node = this.currentNode;
      var reject = false;
      for (;;) {
        node = this._following(node, reject);
        if (!node) return null;
        var result = this._accept(node);
        reject = result === NodeFilter.FILTER_REJECT;
        if (result === NodeFilter.FILTER_ACCEPT) {
          this.currentNode = node;
          return node;
        }
      }
    }
    previousNode() {
      var node = this.currentNode;
      while (node !== this.root) {
        var prev = node.previousSibling;
        if (prev) {
          node = prev;
          while (node.childNodes.length) node = node.childNodes[node.childNodes.length - 1];
        } else node = node.parentNode;
        if (!node) return null;
        if (node === this.root) {
          if (this._accept(node) === NodeFilter.FILTER_ACCEPT) {
            this.currentNode = node;
            return node;
          }
          return null;
        }
        if (this._accept(node) === NodeFilter.FILTER_ACCEPT) {
          this.currentNode = node;
          return node;
        }
      }
      return null;
    }
    parentNode() {
      for (var n = this.currentNode; n && n !== this.root; ) {
        n = n.parentNode;
        if (n && this._accept(n) === NodeFilter.FILTER_ACCEPT) {
          this.currentNode = n;
          return n;
        }
      }
      return null;
    }
    firstChild() {
      for (var i = 0; i < this.currentNode.childNodes.length; i++) {
        var n = this.currentNode.childNodes[i];
        if (this._accept(n) === NodeFilter.FILTER_ACCEPT) {
          this.currentNode = n;
          return n;
        }
      }
      return null;
    }
    lastChild() {
      for (var i = this.currentNode.childNodes.length - 1; i >= 0; i--) {
        var n = this.currentNode.childNodes[i];
        if (this._accept(n) === NodeFilter.FILTER_ACCEPT) {
          this.currentNode = n;
          return n;
        }
      }
      return null;
    }
    nextSibling() {
      for (var n = this.currentNode.nextSibling; n; n = n.nextSibling)
        if (this._accept(n) === NodeFilter.FILTER_ACCEPT) {
          this.currentNode = n;
          return n;
        }
      return null;
    }
    previousSibling() {
      for (var n = this.currentNode.previousSibling; n; n = n.previousSibling)
        if (this._accept(n) === NodeFilter.FILTER_ACCEPT) {
          this.currentNode = n;
          return n;
        }
      return null;
    }
  }

  class Range {
    constructor(doc) {
      this._doc = doc || global.document;
      this.startContainer = this._doc;
      this.startOffset = 0;
      this.endContainer = this._doc;
      this.endOffset = 0;
      this.collapsed = true;
    }
    get commonAncestorContainer() {
      return this.startContainer;
    }
    setStart(node, offset) {
      this.startContainer = node;
      this.startOffset = offset;
    }
    setEnd(node, offset) {
      this.endContainer = node;
      this.endOffset = offset;
      this.collapsed = false;
    }
    selectNode(node) {
      this.startContainer = this.endContainer = node.parentNode;
      this.startOffset = node.parentNode ? node.parentNode.childNodes.indexOf(node) : 0;
      this.endOffset = this.startOffset + 1;
      this.collapsed = false;
    }
    selectNodeContents(node) {
      this.startContainer = this.endContainer = node;
      this.startOffset = 0;
      this.endOffset = node.childNodes.length;
      this.collapsed = false;
    }
    _nodes() {
      if (this.startContainer !== this.endContainer) return [];
      return this.startContainer.childNodes.slice(this.startOffset, this.endOffset);
    }
    deleteContents() {
      this._nodes().forEach(function (n) {
        n.remove();
      });
    }
    extractContents() {
      var frag = this._doc.createDocumentFragment();
      this._nodes().forEach(function (n) {
        frag.appendChild(n);
      });
      return frag;
    }
    cloneContents() {
      var frag = this._doc.createDocumentFragment();
      this._nodes().forEach(function (n) {
        frag.appendChild(n.cloneNode(true));
      });
      return frag;
    }
    insertNode(node) {
      var c = this.startContainer;
      c.insertBefore(node, c.childNodes[this.startOffset] || null);
    }
    collapse() {
      this.collapsed = true;
    }
    createContextualFragment(html) {
      var context = this.startContainer && this.startContainer.nodeType === ELEMENT_NODE ? this.startContainer : this._doc.body || createElementFor(this._doc, "body");
      return parseFragment(String(html), context);
    }
    toString() {
      return this._nodes()
        .map(function (n) {
          return textOf(n);
        })
        .join("");
    }
    getBoundingClientRect() {
      return { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 };
    }
    getClientRects() {
      return [];
    }
    detach() {}
  }

  // ------------------------------------------------------------------
  // Parser and serializer objects
  // ------------------------------------------------------------------

  function DOMParser() {}
  DOMParser.prototype.parseFromString = function (text, type) {
    type = String(type || "text/html").toLowerCase();
    var url = global.location ? global.location.href : "about:blank";
    if (type === "text/html") return parseDocument(text, { url: url });
    return parseDocument(text, { url: url, xml: true, contentType: type });
  };

  function XMLSerializer() {}
  XMLSerializer.prototype.serializeToString = function (node) {
    return serializeNode(node, true);
  };

  global.Node = Node;
  global.CharacterData = CharacterData;
  global.Text = Text;
  global.CDATASection = CDATASection;
  global.Comment = Comment;
  global.ProcessingInstruction = ProcessingInstruction;
  global.DocumentType = DocumentType;
  global.DocumentFragment = DocumentFragment;
  global.ShadowRoot = DocumentFragment;
  global.Attr = Attr;
  global.Element = Element;
  global.HTMLElement = HTMLElement;
  global.SVGElement = SVGElement;
  global.Document = Document;
  global.HTMLDocument = Document;
  global.XMLDocument = Document;
  global.NodeList = Array;
  global.HTMLCollection = Array;
  global.NodeFilter = NodeFilter;
  global.TreeWalker = TreeWalker;
  global.Range = Range;
  global.DOMParser = DOMParser;
  global.XMLSerializer = XMLSerializer;
  global.Image = function (width, height) {
    var img = createElementFor(global.document, "img");
    if (width !== undefined) img.setAttribute("width", width);
    if (height !== undefined) img.setAttribute("height", height);
    return img;
  };
  global.Option = function (text, value) {
    var option = createElementFor(global.document, "option");
    if (text !== undefined) option.textContent = text;
    if (value !== undefined) option.setAttribute("value", value);
    return option;
  };

  global.getComputedStyle = function (el) {
    var style = el && el.style ? el.style : null;
    var defaults = {
      display: el && el.localName ? (isHiddenElement(el) ? "none" : BLOCK[el.localName] ? "block" : el.localName === "li" ? "list-item" : "inline") : "inline",
      visibility: "visible",
      position: "static",
      zIndex: "auto",
      opacity: "1",
      color: "rgb(0, 0, 0)",
      backgroundColor: "rgba(0, 0, 0, 0)",
      fontSize: "16px",
      fontFamily: "sans-serif",
      whiteSpace: el && el.localName === "pre" ? "pre" : "normal",
    };
    return new Proxy(
      {},
      {
        get: function (t, key) {
          if (key === "getPropertyValue")
            return function (name) {
              var camel = String(name).replace(/-([a-z])/g, function (m, c) {
                return c.toUpperCase();
              });
              return (style && style.getPropertyValue(name)) || defaults[camel] || "";
            };
          if (typeof key !== "string") return undefined;
          var own = style ? style[key] : "";
          return own || defaults[key] || "";
        },
      }
    );
  };

  global.__npia_dom = {
    parseDocument: parseDocument,
    parseFragment: parseFragment,
    createHTMLDocument: createHTMLDocument,
    serializeNode: serializeNode,
    textOf: textOf,
    isHiddenElement: isHiddenElement,
    BLOCK: BLOCK,
  };
})(this);
