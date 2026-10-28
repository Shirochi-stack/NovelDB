import Foundation
import NpiaCore

/// kobo: reads a book the signed-in account already owns through Kobo's web reader, which is
/// driven in a web page (`WebProfile.external`). Books that are not in the account's library are
/// refused. Rakuten Books JP links need the Rakuten Kobo Desktop app on a PC and are refused here.
public struct KoboAdapter: SourceAdapter {
    public static let id: SourceID = .kobo
    public static let tier: SourceTier = .lightBrowser
    public static let sites: Set<String> = KoboParsing.sites

    public init() {}

    public static func matches(_ url: URL) -> Bool {
        KoboParsing.matches(url)
    }

    // MARK: Book

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        if KoboParsing.isRakutenURL(url) {
            context.logger.log(.info, "[kobo] Rakuten Books JP link: only the PC Kobo Desktop app can read it")
            return KoboParsing.rakutenBook(url)
        }
        let meta: KoboMeta = try await context.web.withPage(profile: WebProfile.external) { (page: any WebPageDriving) -> KoboMeta in
            try await Self.discover(page: page, url: url)
        }
        let book = KoboParsing.makeBook(url: url, meta: meta)
        if meta.readUrl.isEmpty {
            context.logger.log(.warning, "[kobo] This account has no Read Now access to this volume. Check that the book is in My Books.")
        }
        context.logger.log(.info, "[kobo] Book: \(book.title) by \(book.author ?? "?") - 1 volume")
        return book
    }

    private static func discover(page: any WebPageDriving, url: URL) async throws -> KoboMeta {
        try await page.load(url, until: .domContentLoaded, timeout: 45)
        // The JP storefront renders a hidden desktop/mobile title before its visible title.
        // The first h1 still contains the real title.
        let hasTitle = try await page.waitFor("document.querySelector('h1')", timeout: 30)
        if !hasTitle { throw SourceError.parse("The Kobo book page did not load.") }
        _ = try await page.waitFor(
            "document.querySelector('a[href*=\"readnow.kobo.com\"], [data-kobo-gizmo-config*=\"readNowUrl\"]')",
            timeout: 8)
        var meta: KoboMeta = try await page.evaluate(KoboMeta.self, metaJS)
        if meta.readUrl.isEmpty {
            meta.readUrl = KoboParsing.ownedReadURL(try await page.html())
        }
        return meta
    }

    // MARK: Chapter

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        if ref.id == KoboParsing.rakutenChapterID {
            return .refused(ref, reason: "Rakuten Books JP volumes are read through the Rakuten Kobo Desktop app on a PC, which is not available on iOS")
        }
        if ref.isPaid {
            return .refused(ref, reason: "This book is not in the signed-in Kobo account's library (only owned books can be read)")
        }
        guard let readURL = ref.url, KoboParsing.isReaderURL(readURL) else {
            return .refused(ref, reason: "No valid Kobo Read Now link for this book")
        }
        let horizontal = context.settings.sources.koboHorizontalLayout
        let logger = context.logger
        let content: ChapterContent = try await context.web.withPage(profile: WebProfile.external) { (page: any WebPageDriving) -> ChapterContent in
            try await Self.extract(page: page, readURL: readURL, ref: ref, horizontalLayout: horizontal, logger: logger)
        }
        return .content(content)
    }

    /// Walks the reader's table of contents, snapshots every source section, and assembles the book.
    private static func extract(
        page: any WebPageDriving, readURL: URL, ref: ChapterRef, horizontalLayout: Bool, logger: any JobLogging
    ) async throws -> ChapterContent {
        logger.log(.info, "[kobo] Opening owned volume in Kobo Web Reader: \(ref.title)")
        try await page.load(readURL, until: .domContentLoaded, timeout: 45)
        let opened = try await page.waitFor(visibleExpression(contentsButton), timeout: 45)
        if !opened {
            let current = await page.currentURL()
            if current?.host != KoboParsing.readerHost { throw SourceError.loginRequired(.kobo) }
            throw SourceError.blocked("The Kobo reader did not open; check that the book is in My Books")
        }

        let toc = try await loadTOC(page)
        var entries: [(title: String, path: String)] = []
        for item in toc.entries {
            let path = KoboParsing.resolvePath(item.href, base: toc.base)
            if !path.isEmpty { entries.append((title: item.title, path: path)) }
        }
        if entries.isEmpty { throw SourceError.parse("Kobo reader table of contents is empty.") }

        var collected: [String: KoboSnapshotSection] = [:]
        var snapshots: [[String]] = []
        for (index, entry) in entries.enumerated() {
            try Task.checkCancellation()
            try await openContents(page)
            let count = try await page.evaluate(Int.self, clickLinkJS(index))
            if count < 0 { throw SourceError.parse("Kobo reader table of contents changed.") }
            let ready = try await page.waitFor(sectionReadyExpression(entry.path), timeout: 20)
            if !ready { throw SourceError.parse("Kobo reader did not load: \(entry.title)") }
            try await Task.sleep(nanoseconds: 450_000_000)
            let known = KoboParsing.jsString(try jsonText(Array(collected.keys)))
            let snapshot = try await page.evaluate(KoboSnapshot.self, "const __fn = \(snapshotJS); return await __fn(JSON.parse(\(known)));")
            snapshots.append(snapshot.paths)
            for section in snapshot.sections { collected[section.path] = section }
            logger.log(.info, "[kobo] Read source section \(index + 1)/\(entries.count): \(entry.title)")
        }
        return try KoboParsing.buildContent(
            ref: ref, entries: entries, collected: collected, snapshots: snapshots, horizontalLayout: horizontalLayout)
    }

    private static func jsonText(_ values: [String]) throws -> String {
        let data = try JSONSerialization.data(withJSONObject: values)
        return String(decoding: data, as: UTF8.self)
    }

    private static func openContents(_ page: any WebPageDriving) async throws {
        let clicked = try await page.evaluate(Bool.self,
            "const e = document.querySelector(\(KoboParsing.jsString(contentsButton))); if (!e) return false; e.click(); return true;")
        if !clicked { throw SourceError.parse("Kobo reader contents button is missing.") }
        let shown = try await page.waitFor(visibleExpression(contentsDialog), timeout: 12)
        if !shown { throw SourceError.parse("Kobo reader contents did not open.") }
    }

    private static func loadTOC(_ page: any WebPageDriving) async throws -> KoboTOC {
        try await openContents(page)
        let labels = try await page.evaluate([String].self, """
            const d = document.querySelector(\(KoboParsing.jsString(contentsDialog)));
            if (!d) return [];
            return [...d.querySelectorAll('[role="link"]')].map(a => (a.textContent || '').trim());
            """)
        if labels.isEmpty { throw SourceError.parse("Kobo reader did not expose its source contents.") }
        _ = try await page.evaluate(Int.self, clickLinkJS(0))
        let loaded = try await page.waitFor(
            "[...document.querySelectorAll('iframe[data-chapterurl]')].some(f => { try { return !!f.contentDocument?.querySelector('nav a[href]'); } catch (_) { return false; } })",
            timeout: 15)
        if !loaded { throw SourceError.parse("Kobo reader source contents were not loaded.") }
        try await Task.sleep(nanoseconds: 1_000_000_000)
        guard var toc = try await page.evaluate(Optional<KoboTOC>.self, tocJS) else {
            throw SourceError.parse("Kobo reader source contents were not loaded.")
        }
        if labels.count >= toc.entries.count {
            for position in 0..<toc.entries.count { toc.entries[position].title = labels[position] }
        }
        return toc
    }

    // MARK: JavaScript

    static let contentsButton = "[role=\"button\"][aria-label=\"Open table of contents\"], [role=\"button\"][aria-label=\"\u{76EE}\u{6B21}\u{3092}\u{8868}\u{793A}\"]"
    static let contentsDialog = "[role=\"dialog\"][aria-label=\"Table of contents\"], [role=\"dialog\"][aria-label=\"\u{76EE}\u{6B21}\"]"

    static func visibleExpression(_ selector: String) -> String {
        "(() => { const e = document.querySelector(\(KoboParsing.jsString(selector))); if (!e) return false; const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })()"
    }

    static func sectionReadyExpression(_ path: String) -> String {
        "[...document.querySelectorAll('iframe[data-chapterurl]')].some(f => { try { return f.dataset.chapterurl === \(KoboParsing.jsString(path)) && !!f.contentDocument?.body?.innerHTML?.trim(); } catch (_) { return false; } })"
    }

    /// Clicks the nth link in the contents dialog; returns the link count, or -1 when missing.
    static func clickLinkJS(_ index: Int) -> String {
        """
        const d = document.querySelector(\(KoboParsing.jsString(contentsDialog)));
        if (!d) return -1;
        const links = d.querySelectorAll('[role="link"]');
        if (\(index) >= links.length) return -1;
        links[\(index)].click();
        return links.length;
        """
    }

    static let metaJS = #"""
    const text = (v) => typeof v === 'string' ? v : (Array.isArray(v) ? text(v[0]) : ((v && (v.name || v.url)) || ''));
    const meta = (name) => document.querySelector(
      `meta[property="${name}"],meta[name="${name}"]`)?.content || '';
    const json = [...document.querySelectorAll('script[type="application/ld+json"]')]
      .map(s => { try { return JSON.parse(s.textContent); } catch (_) { return null; } })
      .flatMap(x => Array.isArray(x) ? x : [x])
      .find(x => x && (x['@type'] === 'Book' || x['@type'] === 'Product')) || {};
    const read = [...document.querySelectorAll('a[href]')]
      .find(a => /^https:\/\/readnow\.kobo\.com\//.test(a.href)
        && /read now/i.test(a.innerText || a.textContent || ''));
    return {
      title: document.querySelector('h1')?.textContent?.trim() || '',
      author: text(json.author)
        || document.querySelector('a[href*="/author/"]')?.textContent?.trim()
        || meta('book:author') || '',
      cover: meta('og:image') || text(json.image) ||
        [...document.images].find(x => /cover/i.test((x.alt || '') + ' ' + (x.src || '')))?.src || '',
      description: meta('og:description') || meta('description') || '',
      publisher: text(json.publisher),
      readUrl: read?.href || '',
      language: document.documentElement.lang || 'en',
    };
    """#

    static let tocJS = #"""
    for (const frame of document.querySelectorAll('iframe[data-chapterurl]')) {
      let doc;
      try { doc = frame.contentDocument; } catch (_) { continue; }
      const nav = doc?.querySelector('nav');
      if (!nav) continue;
      const entries = [...nav.querySelectorAll('a[href]')].map(a => ({
        title: a.innerText?.trim() || a.textContent?.trim() || '',
        href: a.getAttribute('href') || ''
      })).filter(x => x.title && /\.xhtml?(?:#|$)/i.test(x.href));
      if (entries.length > 1) return { base: frame.dataset.chapterurl, entries };
    }
    return null;
    """#

    static let snapshotJS = #"""
    async (known) => {
      const seen = new Set(known);
      const frames = [...document.querySelectorAll('iframe[data-chapterurl]')];
      const paths = frames.map(f => f.dataset.chapterurl).filter(Boolean);
      const sections = [];
      for (const frame of frames) {
        const path = frame.dataset.chapterurl;
        if (!path || seen.has(path)) continue;
        let doc;
        try { doc = frame.contentDocument; } catch (_) { continue; }
        if (!doc?.body || !doc.body.innerHTML.trim()) continue;
        const text = doc.body.innerText || '';
        const encoded = [...text].filter(c => {
          const value = c.codePointAt(0);
          return value >= 0x1f60 && value <= 0x1fff;
        }).length;
        if (/^en\b/i.test(frame.lang || doc.documentElement.lang || '') &&
            text.length > 50 && encoded > text.length / 20) continue;
        const clone = doc.body.cloneNode(true);
        const images = [];
        const originals = [...doc.body.querySelectorAll('img')];
        const copies = [...clone.querySelectorAll('img')];
        for (let i = 0; i < originals.length; i++) {
          const source = originals[i].currentSrc || originals[i].src || '';
          if (!source) continue;
          copies[i].setAttribute('src', source);
          let data = source.startsWith('data:image/') ? source : null;
          if (!data) {
            try {
              const response = await doc.defaultView.fetch(source, {
                credentials: 'include'
              });
              if (response.ok) {
                const blob = await response.blob();
                data = await new Promise(resolve => {
                  const reader = new FileReader();
                  reader.onload = () => resolve(reader.result);
                  reader.onerror = () => resolve(null);
                  reader.readAsDataURL(blob);
                });
              }
            } catch (_) {}
          }
          if (!data && originals[i].complete &&
              originals[i].naturalWidth && originals[i].naturalHeight) {
            try {
              const canvas = doc.createElement('canvas');
              canvas.width = originals[i].naturalWidth;
              canvas.height = originals[i].naturalHeight;
              canvas.getContext('2d').drawImage(originals[i], 0, 0);
              data = canvas.toDataURL('image/png');
            } catch (_) {}
          }
          images.push({url: source, data});
        }
        sections.push({path, html: clone.innerHTML, images});
      }
      return {paths, sections};
    }
    """#
}
