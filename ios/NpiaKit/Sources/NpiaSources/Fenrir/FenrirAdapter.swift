import Foundation
import NpiaCore

/// fenrir: series and chapter lists come from the site's JSON API over plain HTTP.
/// Chapter bodies need the reader's read token, so they are fetched from inside a web page
/// (`WebProfile.external`) exactly like the desktop browser path. Paid chapters the account
/// does not own are refused; nothing is ever bought or unlocked with coins.
public struct FenrirAdapter: SourceAdapter {
    public static let id: SourceID = .fenrir
    public static let tier: SourceTier = .lightBrowser

    /// How long to wait for the reader to mint a read token (the desktop waits up to 300 s for a
    /// person to tick the human check; an offscreen web view cannot show it, so wait less).
    static let tokenTimeout: TimeInterval = 60

    public init() {}

    public static func matches(_ url: URL) -> Bool {
        FenrirParsing.isFenrirURL(url)
    }

    // MARK: Book

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        let slug = FenrirParsing.urlParts(url).series
        guard !slug.isEmpty else { throw SourceError.unsupportedURL(url) }
        let encoded = slug.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? slug
        guard let api = URL(string: "\(FenrirParsing.origin)/api/new/v2/series/\(encoded)"),
              let listAPI = URL(string: "\(FenrirParsing.origin)/api/new/v2/series/\(encoded)/chapters") else {
            throw SourceError.unsupportedURL(url)
        }
        let headers: [String: String] = [
            "User-Agent": FenrirParsing.userAgent,
            "Accept": "application/json",
            "Referer": FenrirParsing.origin + "/",
        ]
        context.logger.log(.info, "[fenrir] Fetching series \(slug)...")
        let infoResponse = try await context.http.fetchOK(api, headers: headers)
        let listResponse = try await context.http.fetchOK(listAPI, headers: headers)
        guard let info = (try? JSONSerialization.jsonObject(with: infoResponse.body)) as? [String: Any] else {
            throw SourceError.parse("Unexpected series response.")
        }
        let listObject = try? JSONSerialization.jsonObject(with: listResponse.body)
        var rowObjects: [Any] = []
        if let list = listObject as? [Any] {
            rowObjects = list
        } else if let dict = listObject as? [String: Any] {
            if let data = dict["data"] as? [Any], !data.isEmpty {
                rowObjects = data
            } else if let chapters = dict["chapters"] as? [Any] {
                rowObjects = chapters
            }
        } else {
            throw SourceError.parse("Unexpected series response.")
        }
        var rows: [[String: Any]] = []
        for object in rowObjects {
            if let row = object as? [String: Any] { rows.append(row) }
        }
        guard let book = FenrirParsing.makeBook(info: info, rows: rows, slug: slug) else {
            throw SourceError.parse("No chapters found.")
        }
        let paidCount = book.chapters.filter { $0.isPaid }.count
        context.logger.log(.info, "[fenrir] Book: \(book.title) - \(book.chapters.count) chapters (\(paidCount) locked paid)")
        return book
    }

    // MARK: Chapters

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        let results = try await parseChapters([ref], book: book, context: context)
        return results.first ?? .failed(ref, message: "fenrir returned no result")
    }

    /// One web page for the whole batch, one chapter at a time: the reader binds its read token to
    /// the chapter it registered last, so parallel requests would lock each other out.
    public func parseChapters(_ refs: [ChapterRef], book: BookInfo, context: SourceContext) async throws -> [ChapterResult] {
        if refs.isEmpty { return [] }
        var results = [ChapterResult?](repeating: nil, count: refs.count)
        var refusals: [Int: String] = [:]
        var pending: [Int] = []
        for (offset, ref) in refs.enumerated() {
            if ref.isPaid {
                results[offset] = .refused(ref, reason: "Paid chapter that this account does not own")
            } else if ref.url == nil {
                results[offset] = .failed(ref, message: "Chapter has no URL")
            } else {
                pending.append(offset)
            }
        }
        if !pending.isEmpty {
            let slug = book.id
            let logger = context.logger
            do {
                try await context.web.withPage(profile: WebProfile.external) { (page: any WebPageDriving) -> Void in
                    try await Self.run(page: page, refs: refs, slug: slug, pending: &pending,
                                       results: &results, refusals: &refusals, logger: logger)
                }
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                for offset in pending where results[offset] == nil {
                    results[offset] = .failed(refs[offset], message: error.localizedDescription)
                }
            }
        }
        let shared = context.shared(for: Self.id)
        if shared.intervalMax > 0 {
            let low = max(0, min(shared.intervalMin, shared.intervalMax))
            let high = max(low, shared.intervalMax)
            let delay = Double.random(in: low...high)
            if delay > 0 { try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000)) }
        }
        var output: [ChapterResult] = []
        for (offset, ref) in refs.enumerated() {
            if let result = results[offset] {
                output.append(result)
            } else if let reason = refusals[offset] {
                output.append(.refused(ref, reason: reason))
            } else {
                output.append(.failed(ref, message: "fenrir returned no chapter content"))
            }
        }
        return output
    }

    private static func run(
        page: any WebPageDriving, refs: [ChapterRef], slug: String, pending: inout [Int],
        results: inout [ChapterResult?], refusals: inout [Int: String], logger: any JobLogging
    ) async throws {
        guard let firstURL = refs[pending[0]].url else { return }
        try await page.load(firstURL, until: .domContentLoaded, timeout: 45)
        var unlocked = false
        for _ in 0..<3 {
            if pending.isEmpty { break }
            try Task.checkCancellation()
            if !(await tokenActive(page)) {
                if unlocked { break }
                guard let url = refs[pending[0]].url else { break }
                if !(await unlockReader(page, chapterURL: url, logger: logger)) {
                    throw SourceError.blocked("fenrir reader human check was not completed")
                }
                unlocked = true
            }
            var items: [[String: String]] = []
            for offset in pending {
                let parts = FenrirParsing.splitRefID(refs[offset].id)
                let chapterSlug = refs[offset].url.map { FenrirParsing.urlParts($0).chapter } ?? parts.slug
                let encoded = chapterSlug.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? chapterSlug
                items.append([
                    "api": "/api/new/v2/series/\(slug)/\(encoded)",
                    "chapterId": parts.chapterID,
                ])
            }
            let replies: [[String: Any]]
            do {
                replies = try await fetchReplies(page, items: items)
            } catch {
                logger.log(.warning, "[fenrir] Chapter request failed: \(error.localizedDescription)")
                break
            }
            var retry: [Int] = []
            var retryDelays: [TimeInterval] = []
            for (position, offset) in pending.enumerated() {
                guard position < replies.count else { break }
                let reply = replies[position]
                let ref = refs[offset]
                let status = FenrirParsing.integer(reply["status"]) ?? 0
                if status == 429 {
                    retryDelays.append(FenrirParsing.retryDelay(reply["retryAfter"] as? String))
                    retry.append(offset)
                    continue
                }
                guard let payload = reply["payload"] as? [String: Any] else {
                    logger.log(.warning, "[fenrir] \(ref.title): HTTP \(status)")
                    if status == 401 || status == 403 { retry.append(offset) }
                    continue
                }
                if FenrirParsing.paidLocked(payload) {
                    results[offset] = .refused(ref, reason: "Paid chapter that this account does not own")
                    continue
                }
                let format = FenrirParsing.string(payload["content_format"])
                if format == "locked" || !FenrirParsing.isTruthy(payload["content"]) {
                    refusals[offset] = "The reader did not release this chapter (locked or not published yet)"
                    retry.append(offset)
                    continue
                }
                if let content = FenrirParsing.buildChapter(payload: payload, ref: ref) {
                    results[offset] = .content(content)
                } else {
                    logger.log(.warning, "[fenrir] Empty chapter: \(ref.title)")
                    results[offset] = .failed(ref, message: "Chapter content was empty")
                }
            }
            pending = retry
            if !pending.isEmpty {
                if !retryDelays.isEmpty {
                    let delay = retryDelays.max() ?? 30
                    logger.log(.info, "[fenrir] Rate limited; waiting \(delay) s...")
                    try await Task.sleep(nanoseconds: UInt64(min(delay, 600) * 1_000_000_000))
                } else if await tokenActive(page) {
                    // The token is fine, so these may be timed releases.
                    break
                }
            }
        }
    }

    // MARK: Web page steps

    private static func tokenActive(_ page: any WebPageDriving) async -> Bool {
        let value: Bool? = try? await page.evaluate(Bool.self, tokenCheckJS)
        return value == true
    }

    private static func unlockReader(_ page: any WebPageDriving, chapterURL: URL, logger: any JobLogging) async -> Bool {
        do {
            try await page.load(chapterURL, until: .domContentLoaded, timeout: 45)
        } catch {
            logger.log(.warning, "[fenrir] Reader page did not load: \(error.localizedDescription)")
            return false
        }
        let deadline = Date().addingTimeInterval(tokenTimeout)
        while Date() < deadline {
            if await tokenActive(page) { return true }
            if let clicked = try? await page.evaluate([String].self, "return await (\(dismissJS))();"), !clicked.isEmpty {
                logger.log(.info, "[fenrir] Closed reader pop-up: " + clicked.joined(separator: ", "))
            }
            do {
                try await Task.sleep(nanoseconds: 1_500_000_000)
            } catch {
                return false
            }
        }
        return await tokenActive(page)
    }

    private static func fetchReplies(_ page: any WebPageDriving, items: [[String: String]]) async throws -> [[String: Any]] {
        let itemData = try JSONSerialization.data(withJSONObject: items)
        let itemJSON = String(decoding: itemData, as: UTF8.self)
        let data = try await page.evaluateJSON("const __fn = \(fetchJS); return await __fn(\(itemJSON));")
        guard let array = (try? JSONSerialization.jsonObject(with: data)) as? [Any] else { return [] }
        var replies: [[String: Any]] = []
        for element in array { replies.append((element as? [String: Any]) ?? [:]) }
        return replies
    }

    static let tokenCheckJS = #"""
    const r = await fetch('/api/read-token', {credentials: 'include'});
    if (!r.ok) return false;
    const j = await r.json();
    return !!(j && j.active);
    """#

    /// Runs in the reader tab. Fetches chapters with the page's read-token cookie; encrypted
    /// replies are decoded the same way the reader does.
    static let fetchJS = #"""
    async (items) => {
      const fragShuffle = async (token, id, n) => {
        if (n < 2) return n === 1 ? [0] : [];
        const d = await crypto.subtle.digest('SHA-256',
          new TextEncoder().encode(`${token}:${id}:fr-frag-v1`));
        const v = new DataView(d);
        const s = Uint32Array.of(v.getUint32(0, true), v.getUint32(4, true),
                                 v.getUint32(8, true), v.getUint32(12, true));
        const next = () => {
          const t = s[0];
          let r = (t ^ (t << 11)) >>> 0; r = (r ^ (r >>> 8)) >>> 0;
          const c = s[3]; s[0] = s[1]; s[1] = s[2]; s[2] = c;
          const f = (c ^ (c >>> 19) ^ r) >>> 0; s[3] = f; return f;
        };
        const order = Array.from({length: n}, (_, i) => i);
        for (let b = n - 1; b > 0; b--) {
          const w = next() % (b + 1); [order[b], order[w]] = [order[w], order[b]];
        }
        return order;
      };
      const decrypt = async (payload) => {
        const tokenResp = await fetch('/api/read-token', {credentials: 'include'});
        if (!tokenResp.ok) return null;
        const tokenData = await tokenResp.json();
        const token = tokenData && tokenData.active ? tokenData.token : null;
        if (!token) return null;
        let content = payload.content;
        if (Array.isArray(content)) {
          const order = await fragShuffle(token, payload.id, content.length);
          const out = new Array(content.length);
          for (let i = 0; i < content.length; i++) out[order[i]] = content[i];
          content = out.join('');
        }
        const keyBytes = await crypto.subtle.digest('SHA-256',
          new TextEncoder().encode(`${token}:${payload.id}:fr-content-v1`));
        const key = await crypto.subtle.importKey('raw', keyBytes,
          {name: 'AES-GCM'}, false, ['decrypt']);
        const raw = Uint8Array.from(atob(content), c => c.charCodeAt(0));
        if (raw.length <= 28) return null;
        const plain = await crypto.subtle.decrypt(
          {name: 'AES-GCM', iv: raw.slice(0, 12)}, key, raw.slice(12));
        return new TextDecoder().decode(plain);
      };
      const fetchOne = async (item) => {
        try {
          if (item.chapterId) {
            await fetch('/api/read-token', {
              method: 'POST', credentials: 'include',
              headers: {'Content-Type': 'application/json',
                        Accept: 'application/json'},
              body: JSON.stringify({chapter_id: String(item.chapterId)}),
            }).catch(() => null);
          }
          const resp = await fetch(item.api, {credentials: 'include',
                                              headers: {Accept: 'application/json'}});
          if (!resp.ok) return {status: resp.status,
            retryAfter: resp.headers.get('Retry-After')};
          const payload = await resp.json();
          if (payload.content_format === 'encrypted') {
            const plain = await decrypt(payload).catch(() => null);
            if (plain === null) return {status: resp.status, payload: {
              ...payload, content: '', content_format: 'locked'}};
            payload.content = plain;
            payload.content_format = 'html';
          }
          return {status: resp.status, payload};
        } catch (e) {
          return {status: 0, error: String(e)};
        }
      };
      const out = [];
      for (const item of items) out.push(await fetchOne(item));
      return out;
    }
    """#

    /// Clears the reader's own pop-ups (cookie banner, notices, content-warning dialogs).
    static let dismissJS = #"""
    () => {
      const visible = (el) => {
        const r = el.getBoundingClientRect();
        const st = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && st.visibility !== 'hidden'
          && st.display !== 'none';
      };
      const label = (el) => ((el.innerText || '') + ' '
        + (el.getAttribute('aria-label') || '') + ' '
        + (el.getAttribute('title') || '')).trim().toLowerCase();
      const clicked = [];
      const press = (el) => { el.click(); clicked.push(label(el).slice(0, 40)); };
      const buttons = (root) => [...root.querySelectorAll(
        'button, [role=button], input[type=button], input[type=submit]')]
        .filter(visible);
      for (const b of buttons(document)) {
        const t = label(b);
        if (t === 'essential only' || t.startsWith('dismiss notice')
            || t === 'dismiss' || t === 'got it') press(b);
      }
      const modals = [...document.querySelectorAll(
        '[role=dialog], [role=alertdialog], dialog[open], [aria-modal=true]')]
        .filter(visible);
      for (const modal of modals) {
        for (const box of modal.querySelectorAll(
            'input[type=checkbox], [role=checkbox]')) {
          const on = box.checked || box.getAttribute('aria-checked') === 'true';
          if (!on && visible(box)) { box.click(); clicked.push('checkbox'); }
        }
        const next = buttons(modal).find((b) => /^(continue|proceed|confirm|i understand|i agree|agree|accept|ok|okay|yes|read|start reading|continue reading)\b/
          .test(label(b)) && !b.disabled);
        const close = buttons(modal).find((b) => /^(close|dismiss|×|x)$/
          .test(label(b)));
        if (next) press(next); else if (close) press(close);
      }
      return clicked;
    }
    """#
}
