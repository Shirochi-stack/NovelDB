import Foundation
import NpiaCore

/// A userscript as an engine (a `.user.js` added in Settings > Site engines; the desktop's
/// noveldb/core/engines/userscript_engine.py has the idea in full).
///
/// A link is one chapter. It opens in a web view page of its own, never shown, with the script running in it
/// from the start of the page (`userscript-host.js` gives it its GM_ functions, reads the page to its end,
/// presses the script's own buttons and keeps the files it saves). The images are taken from the page a few
/// megabytes at a time: a web view answers a script within its own time limit, and a page can take longer to read.
enum UserscriptEngine {
    /// The message handler the host's requests to other sites go through (`ndbUserscriptRequest` in the host).
    static let requestHandler = "ndbUserscriptRequest"
    private static let loadTimeout: TimeInterval = 30
    private static let readSeconds = 600
    private static let takeBytes = 3_000_000
    private static let extensions = [
        "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp",
        "image/avif": "avif", "image/bmp": "bmp",
    ]

    private static func hostScript() -> String? {
        let file = ["Resources/userscript", "userscript"].lazy
            .compactMap { Bundle.module.url(forResource: "userscript-host", withExtension: "js", subdirectory: $0) }
            .first
        return file.flatMap { try? String(contentsOf: $0, encoding: .utf8) }
    }

    /// The one-chapter book of `url`: the page's title, from a plain request (the script runs for the chapter).
    static func openBook(_ script: CustomEngines.Info, url: URL, context: SourceContext) async throws -> ReaderBookPayload {
        var title = ""
        if let response = try? await context.http.get(url) {
            let html = String(decoding: response.body.prefix(200_000), as: UTF8.self)
            if let regex = try? NSRegularExpression(pattern: "<title[^>]*>(.*?)</title>", options: [.caseInsensitive, .dotMatchesLineSeparators]),
               let match = regex.firstMatch(in: html, range: NSRange(html.startIndex..., in: html)),
               let range = Range(match.range(at: 1), in: html) {
                title = decodeEntities(String(html[range]))
                    .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
                    .trimmingCharacters(in: .whitespacesAndNewlines)
            }
        }
        if title.isEmpty { title = url.host ?? "Page" }
        context.logger.log(.info, "[Reader] \(script.label) (userscript)")
        let object: [String: Any] = [
            "bookUrl": url.absoluteString, "pageUrl": url.absoluteString, "bookname": title, "author": "",
            "chapters": [["url": url.absoluteString, "name": title]],
        ]
        let data = try JSONSerialization.data(withJSONObject: object)
        return try JSONDecoder().decode(ReaderBookPayload.self, from: data)
    }

    /// The chapter's images: what the script saved from the page, in the order it numbered them.
    static func chapter(
        _ script: CustomEngines.Info, info: ReaderBookSession.ChapterInfo, context: SourceContext
    ) async throws -> (blocks: [RuleBlock], images: [URL: Data]) {
        guard let host = hostScript() else { throw SourceError.parse("Userscript engines are missing from this build.") }
        guard let url = URL(string: info.url) else { throw SourceError.parse("\(script.label): not a web address: \(info.url)") }
        let page = try await context.web.newPage(profile: EnginePage.profile)
        do {
            let http = context.http
            let web = context.web
            // The script's requests to other sites go through the app (the page may not make them).
            await page.addMessageHandler(requestHandler) { json in
                await EngineTransport.handle(json, http: http, web: web)
            }
            let meta = ReaderWebFallback.jsonLiteral(["name": script.label])
            await page.addMainFrameInitScript(
                host + "\n;window.__NDB_USERSCRIPT.start(" + ReaderWebFallback.jsonLiteral(script.source) + ", " + meta + ");"
            )
            do {
                try await page.load(url, until: .domContentLoaded, timeout: loadTimeout)
            } catch WebEngineError.timeout(_) {
                context.logger.log(.info, "The page took over \(Int(loadTimeout)) s to load; reading what is there.")
            }
            let read = try await self.read(page, script: script, url: url, context: context)
            await page.close()
            return read
        } catch {
            await page.close()
            throw error
        }
    }

    private struct Taken: Decodable {
        struct File: Decodable {
            var name: String?
            var mime: String?
            var base64: String
        }

        struct Done: Decodable {
            var pressed: [String]?
            var failed: String?
        }

        var files: [File]
        var done: Done?
    }

    private static func read(
        _ page: any WebPageDriving, script: CustomEngines.Info, url: URL, context: SourceContext
    ) async throws -> (blocks: [RuleBlock], images: [URL: Data]) {
        let started = try await page.evaluateJSON("return !!(window.__NDB_USERSCRIPT && window.__NDB_USERSCRIPT.begin({}));")
        guard String(decoding: started, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines) == "true" else {
            throw SourceError.parse("\(script.label): the script did not start on \(url.absoluteString).")
        }
        var blocks: [RuleBlock] = []
        var images: [URL: Data] = [:]
        var done: Taken.Done?
        var waited = 0
        while done == nil, waited < readSeconds {
            try Task.checkCancellation()
            // A busy page is asked again.
            let taken = (try? await page.evaluateJSON("return window.__NDB_USERSCRIPT.take(\(takeBytes));"))
                .flatMap { try? JSONDecoder().decode(Taken.self, from: $0) }
            let files = taken?.files ?? []
            for file in files {
                guard let bytes = Data(base64Encoded: file.base64), !bytes.isEmpty else { continue }
                let ext = extensions[file.mime ?? ""] ?? "png"
                // An address of its own per image: the pipeline names the file from it and never fetches it (the bytes are here).
                guard let address = URL(string: String(format: "https://userscript.invalid/%04d.%@", blocks.count + 1, ext)) else { continue }
                blocks.append(.image(address))
                images[address] = bytes
            }
            done = taken?.done
            if done == nil, files.isEmpty {
                try await Task.sleep(nanoseconds: 1_000_000_000)
                waited += 1
            }
        }
        if let failed = done?.failed, !failed.isEmpty {
            context.logger.log(.warning, "[Reader] \(script.label): the script stopped: \(failed)")
        }
        guard !blocks.isEmpty else {
            throw SourceError.parse(done == nil
                ? "\(script.label): the page was still being read after \(readSeconds / 60) minutes."
                : "\(script.label): the script saved no images from this page.")
        }
        let pressed = done?.pressed ?? []
        context.logger.log(.info, "[Reader] \(script.label): \(blocks.count) image(s)"
            + (pressed.isEmpty ? "." : " (pressed \(pressed.joined(separator: ", "))).")
        )
        return (blocks, images)
    }

    private static func decodeEntities(_ text: String) -> String {
        text.replacingOccurrences(of: "&amp;", with: "&").replacingOccurrences(of: "&lt;", with: "<")
            .replacingOccurrences(of: "&gt;", with: ">").replacingOccurrences(of: "&quot;", with: "\"")
            .replacingOccurrences(of: "&#39;", with: "'").replacingOccurrences(of: "&nbsp;", with: " ")
    }
}
