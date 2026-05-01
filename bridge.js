/**
 * bridge.js — NpiaDownloader ↔ novel-downloader rules bridge
 *
 * Injected into QWebEngineView AFTER rules-lib.js.
 * Provides two entry points callable from Python via QWebChannel:
 *
 *   1. __ND_parseBook()  — extract metadata + chapter list from the current page
 *   2. __ND_parseChapter(chapterUrl) — fetch and parse a single chapter
 *
 * Results are posted back to Python via the `ndBridge` QWebChannel object.
 */

(function () {
  "use strict";

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------

  /** Safely serialise a DOM element to an HTML string. */
  function domToHtml(el) {
    if (!el) return "";
    if (typeof el === "string") return el;
    if (el.outerHTML) return el.outerHTML;
    if (el.innerHTML) return el.innerHTML;
    return "";
  }

  /** Collect all <img> src URLs from an HTML string. */
  function extractImageUrls(html) {
    var urls = [];
    if (!html) return urls;
    var re = /<img[^>]+src=["']([^"']+)["']/gi;
    var m;
    while ((m = re.exec(html)) !== null) {
      urls.push(m[1]);
    }
    return urls;
  }

  // ------------------------------------------------------------------
  // Book parsing (metadata + chapter list)
  // ------------------------------------------------------------------

  window.__ND_parseBook = async function () {
    try {
      if (!window.__ND_getRule) {
        return JSON.stringify({ error: "rules-lib.js not loaded" });
      }

      // Instantiate the matching rule for the current page.
      // getRule() may return a class (function) or an already-created instance.
      var ruleResult = await window.__ND_getRule();
      var rule;
      if (typeof ruleResult === "function") {
        rule = new ruleResult();
      } else {
        rule = ruleResult;
      }

      // bookParse() reads the current DOM to extract metadata + chapter list
      var book = await rule.bookParse();
      // Extract cover URL if available
      var coverUrl = null;
      if (book.additionalMetadate && book.additionalMetadate.cover) {
        coverUrl = book.additionalMetadate.cover.url || null;
      }

      // Serialise chapter list
      var chapters = [];
      for (var i = 0; i < book.chapters.length; i++) {
        var ch = book.chapters[i];
        chapters.push({
          url: ch.chapterUrl,
          name: ch.chapterName,
          number: ch.chapterNumber,
          sectionName: ch.sectionName,
          sectionNumber: ch.sectionNumber,
          isVIP: ch.isVIP,
          isPaid: ch.isPaid,
        });
      }

      // Store the rule instance for chapter parsing later
      window.__ND_ruleInstance = rule;
      window.__ND_bookObj = book;

      return JSON.stringify({
        bookUrl: book.bookUrl,
        bookname: book.bookname,
        author: book.author,
        introduction: book.introduction || "",
        coverUrl: coverUrl,
        tags: (book.additionalMetadate && book.additionalMetadate.tags) || [],
        language:
          (book.additionalMetadate && book.additionalMetadate.language) || "zh",
        chapterCount: chapters.length,
        chapters: chapters,
      });
    } catch (err) {
      return JSON.stringify({
        error: err.message || String(err),
        stack: err.stack || "",
      });
    }
  };

  // ------------------------------------------------------------------
  // Chapter parsing (content extraction)
  // ------------------------------------------------------------------

  window.__ND_parseChapter = async function (
    chapterUrl,
    chapterName,
    isVIP,
    isPaid
  ) {
    try {
      var rule = window.__ND_ruleInstance;
      if (!rule) {
        return JSON.stringify({ error: "No rule instance — call parseBook first" });
      }

      var charset = rule.charset || document.characterSet || "utf-8";
      var bookname =
        (window.__ND_bookObj && window.__ND_bookObj.bookname) || "";

      // Call the rule's chapterParse to fetch + extract the chapter
      var result = await rule.chapterParse(
        chapterUrl,
        chapterName || null,
        !!isVIP,
        isPaid !== undefined ? isPaid : null,
        charset,
        { bookname: bookname }
      );

      // Serialise content
      var contentHtml = domToHtml(result.contentHTML);
      var contentText = result.contentText || "";

      // Collect image data from contentImages (AttachmentClass instances)
      var images = [];
      if (result.contentImages && result.contentImages.length > 0) {
        for (var i = 0; i < result.contentImages.length; i++) {
          var img = result.contentImages[i];
          // Wait for the image to finish downloading if it hasn't yet
          if (img.status === 1) {
            // Status.downloading
            await img.init();
          }
          var imgData = null;
          if (img.imageBlob) {
            // Convert blob to base64
            var reader = new FileReader();
            imgData = await new Promise(function (resolve) {
              reader.onload = function () {
                resolve(reader.result);
              };
              reader.readAsDataURL(img.imageBlob);
            });
          }
          images.push({
            name: img.name,
            url: img.url,
            data: imgData, // data:image/...;base64,... or null
          });
        }
      }

      return JSON.stringify({
        chapterName: result.chapterName || chapterName,
        contentHtml: contentHtml,
        contentText: contentText,
        imageCount: images.length,
        images: images,
      });
    } catch (err) {
      return JSON.stringify({
        error: err.message || String(err),
        stack: err.stack || "",
      });
    }
  };

  // Mark bridge as ready
  window.__ND_BRIDGE_READY = true;
  console.log("[ND-Bridge] bridge.js loaded");
})();
