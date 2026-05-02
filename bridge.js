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

      // Extract cover URL — each site rule's bookParse() calls
      // getAttachment() to set additionalMetadate.cover, but it's
      // async (.then()) so the attachment may not be populated yet.
      // We poll briefly to give it time to resolve.
      var coverUrl = null;
      var meta = book.additionalMetadate;
      if (meta && meta.cover) {
        coverUrl = meta.cover.url || null;
        // If the attachment is still downloading, wait for it
        if (meta.cover.status === 1 && typeof meta.cover.init === 'function') {
          try { await meta.cover.init(); } catch(e) {}
        }
      } else {
        // Poll up to 2s for the async getAttachment .then() to resolve
        for (var poll = 0; poll < 4; poll++) {
          await new Promise(function(r) { setTimeout(r, 500); });
          meta = book.additionalMetadate;
          if (meta && meta.cover) {
            coverUrl = meta.cover.url || null;
            break;
          }
        }
      }
      if (coverUrl) {
        console.log('[ND-Bridge] Cover URL: ' + coverUrl);
      } else {
        console.log('[ND-Bridge] No cover found from rule');
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
        console.log('[ND-Bridge] ' + result.contentImages.length + ' image(s) to download for: ' + (result.chapterName || chapterName));
        for (var i = 0; i < result.contentImages.length; i++) {
          var img = result.contentImages[i];
          console.log('[ND-Bridge] Image ' + (i + 1) + '/' + result.contentImages.length + ': ' + (img.url || img.name));
          // Wait for the image to finish downloading if it hasn't yet
          if (img.status === 1) {
            // Status.downloading
            try {
              await img.init();
              console.log('[ND-Bridge] Image OK: ' + (img.name || img.url));
            } catch (imgErr) {
              console.log('[ND-Bridge] Image FAILED: ' + (img.name || img.url) + ' — ' + imgErr.message);
            }
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

  // ------------------------------------------------------------------
  // Batch chapter parsing (concurrent via Promise.all)
  // ------------------------------------------------------------------

  window.__ND_parseChapterBatch = async function (chaptersJson) {
    try {
      var chapters = JSON.parse(chaptersJson);
      var promises = chapters.map(function (ch) {
        return window
          .__ND_parseChapter(ch.url, ch.name, ch.isVIP, ch.isPaid)
          .catch(function (err) {
            return JSON.stringify({
              error: err.message || String(err),
              chapterName: ch.name,
            });
          });
      });
      var results = await Promise.all(promises);
      return JSON.stringify(results);
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
