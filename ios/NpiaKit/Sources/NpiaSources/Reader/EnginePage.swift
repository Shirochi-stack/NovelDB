import Foundation
import NpiaCore

/// The engine found nothing to parse on this site (no rule, no parser), as opposed to failing
/// while reading it. It ranks below real failures when every engine fails.
struct EngineNoMatch: Error, LocalizedError {
    var message: String
    var errorDescription: String? { message }
}

/// The book page the JavaScript engines share. It loads on first use, so a book the script
/// engine can read never opens a web view, and the novel-downloader rules and the WebToEpub
/// parsers run in one page load. A failed load is remembered: the engines after it skip the page.
///
/// A session that succeeds takes the page over (`handOff`) and closes it when the session ends;
/// otherwise `closeUnlessHandedOff` closes it when the attempts are over.
actor EnginePage {
    static let profile = "reader"
    /// The message handler the page's `__W2E_transport` calls.
    static let transportName = "npiaTransport"

    private let url: URL
    private let context: SourceContext
    private var page: (any WebPageDriving)?
    private var failure: Error?
    private var handedOff = false

    init(url: URL, context: SourceContext) {
        self.url = url
        self.context = context
    }

    /// The loaded page, loading it first when needed.
    func acquire() async throws -> any WebPageDriving {
        if let page { return page }
        if let failure { throw failure }
        do {
            let opened = try await context.web.newPage(profile: Self.profile)
            let http = context.http
            await opened.addMessageHandler(Self.transportName) { json in
                await EngineTransport.handle(json, http: http)
            }
            do {
                try await opened.load(url, until: .domContentLoaded, timeout: 30)
            } catch {
                await opened.close()
                throw error
            }
            page = opened
            return opened
        } catch {
            if !(error is CancellationError) { failure = error }
            throw error
        }
    }

    /// The session that parsed the book now owns the page.
    func handOff() {
        handedOff = true
    }

    /// Closes the page of a session that has ended.
    func finish() async {
        let open = page
        page = nil
        handedOff = false
        failure = CancellationError()
        await open?.close()
    }

    /// Closes the page when no session took it over.
    func closeUnlessHandedOff() async {
        guard !handedOff else { return }
        await finish()
    }
}

/// The network the WebToEpub bundle falls back to when the page's own `fetch` is refused (CORS):
/// `window.__W2E_transport(details)`. The request goes out through the app's HTTP client, which
/// has no such limit and carries the web view's cookies (the engine copies them to the shared
/// jar before each call).
///
/// The request is `{url, method, headers, data? | dataBase64?, responseType, timeout}`; the reply
/// is `{status, statusText, responseHeaders, finalUrl, responseBase64}` or `{error}`.
enum EngineTransport {
    static func handle(_ json: String, http: any HTTPFetching) async -> String {
        guard let object = (try? JSONSerialization.jsonObject(with: Data(json.utf8))) as? [String: Any],
              let text = object["url"] as? String, let url = URL(string: text),
              let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https"
        else {
            return encode(["error": "invalid request"])
        }
        var headers: [String: String] = [:]
        if let given = object["headers"] as? [String: Any] {
            for (name, value) in given { headers[name] = String(describing: value) }
        }
        let body: Data?
        if let encoded = object["dataBase64"] as? String, let bytes = Data(base64Encoded: encoded) {
            body = bytes
        } else if let text = object["data"] as? String {
            body = Data(text.utf8)
        } else {
            body = nil
        }
        let milliseconds = (object["timeout"] as? NSNumber)?.doubleValue ?? 30000
        let request = HTTPRequest(
            url: url,
            method: ((object["method"] as? String) ?? "GET").uppercased(),
            headers: headers,
            body: body,
            timeout: max(1, milliseconds / 1000)
        )
        do {
            let response = try await http.send(request)
            return reply(status: response.status, url: response.url, headers: response.headers, body: response.body)
        } catch HTTPError.status(let status, let finalURL) {
            return reply(status: status, url: finalURL, headers: [:], body: Data())
        } catch {
            return encode(["error": error.localizedDescription])
        }
    }

    static func reply(status: Int, url: URL, headers: [String: String], body: Data) -> String {
        let lines = headers.keys.sorted().map { "\($0): \(headers[$0] ?? "")" }
        return encode([
            "status": status,
            "statusText": HTTPURLResponse.localizedString(forStatusCode: status),
            "responseHeaders": lines.joined(separator: "\r\n"),
            "finalUrl": url.absoluteString,
            "responseBase64": body.base64EncodedString(),
        ])
    }

    private static func encode(_ object: [String: Any]) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: object) else { return "{\"error\":\"encoding\"}" }
        return String(decoding: data, as: UTF8.self)
    }
}
