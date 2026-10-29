import Foundation

/// One fetch/XHR response recorded by `NetworkCapture.initScript`.
public struct CapturedResponse: Codable, Sendable, Hashable {
    public var url: String
    public var method: String
    public var status: Int
    public var body: String

    public init(url: String, method: String = "GET", status: Int, body: String) {
        self.url = url
        self.method = method
        self.status = status
        self.body = body
    }

    public func json<T: Decodable>(_ type: T.Type) throws -> T {
        try JSONDecoder().decode(type, from: Data(body.utf8))
    }
}

/// Records page fetch/XHR responses from inside the page, replacing Playwright's
/// `page.on("response")` interception. Install with `startCapture` before `load`.
public enum NetworkCapture {
    /// Page global holding the captured responses.
    public static let storeName = "__npiaCaptured"

    /// Wraps `fetch` and `XMLHttpRequest` so responses whose URL contains one of
    /// `patterns` (all responses when empty) are copied into `window.__npiaCaptured`.
    /// Bodies larger than `maxBody` characters are truncated.
    public static func initScript(patterns: [String], maxBody: Int = 8_000_000) -> String {
        let encoded = (try? JSONEncoder().encode(patterns)).map { String(decoding: $0, as: UTF8.self) } ?? "[]"
        return """
        (() => {
          if (window.\(storeName)) return;
          const store = [];
          Object.defineProperty(window, '\(storeName)', { value: store, enumerable: false });
          const patterns = \(encoded);
          const wanted = (url) => !patterns.length || patterns.some((p) => String(url).includes(p));
          const keep = (url, method, status, body) => {
            try {
              store.push({ url: String(url), method: String(method || 'GET').toUpperCase(),
                           status: Number(status) || 0, body: String(body ?? '').slice(0, \(maxBody)) });
            } catch (e) {}
          };
          const originalFetch = window.fetch;
          if (originalFetch) {
            window.fetch = function (input, init) {
              const url = (input && input.url) || input;
              const method = (init && init.method) || (input && input.method) || 'GET';
              return originalFetch.apply(this, arguments).then((response) => {
                if (wanted(response.url || url)) {
                  response.clone().text().then((text) => keep(response.url || url, method, response.status, text), () => {});
                }
                return response;
              });
            };
          }
          const open = XMLHttpRequest.prototype.open;
          const send = XMLHttpRequest.prototype.send;
          XMLHttpRequest.prototype.open = function (method, url) {
            this.__npiaMethod = method;
            this.__npiaURL = url;
            return open.apply(this, arguments);
          };
          XMLHttpRequest.prototype.send = function () {
            this.addEventListener('load', () => {
              const url = this.responseURL || this.__npiaURL;
              if (!wanted(url)) return;
              let body = '';
              try {
                if (this.responseType === '' || this.responseType === 'text') body = this.responseText;
                else if (this.responseType === 'json') body = JSON.stringify(this.response);
              } catch (e) {}
              keep(url, this.__npiaMethod, this.status, body);
            });
            return send.apply(this, arguments);
          };
        })();
        """
    }
}

public extension WebPageDriving {
    /// Starts recording fetch/XHR responses for pages loaded after this call.
    func startCapture(patterns: [String] = []) async {
        await addInitScript(NetworkCapture.initScript(patterns: patterns))
    }

    /// Responses recorded so far in the current document.
    func capturedResponses() async throws -> [CapturedResponse] {
        let data = try await evaluateJSON("return window.\(NetworkCapture.storeName) || [];")
        return try JSONDecoder().decode([CapturedResponse].self, from: data)
    }

    /// Polls until a captured response's URL contains `pattern`, then returns the newest one.
    func waitForResponse(containing pattern: String, timeout: TimeInterval = 0) async throws -> CapturedResponse? {
        let deadline = Date().addingTimeInterval(max(timeout, minimumWait))
        repeat {
            if let match = try await capturedResponses().last(where: { $0.url.contains(pattern) }) {
                return match
            }
            try await Task.sleep(nanoseconds: 250_000_000)
        } while Date() < deadline
        return nil
    }

    /// Runs a JavaScript function body and decodes its JSON result.
    func evaluate<T: Decodable>(_ type: T.Type, _ functionBody: String) async throws -> T {
        try JSONDecoder().decode(type, from: try await evaluateJSON(functionBody))
    }

    /// Polls `condition` (a JavaScript expression) until it is truthy.
    @discardableResult
    func waitFor(_ condition: String, timeout: TimeInterval = 0, interval: TimeInterval = 0.25) async throws -> Bool {
        let deadline = Date().addingTimeInterval(max(timeout, minimumWait))
        repeat {
            if (try? await evaluate(Bool.self, "return !!(\(condition));")) == true { return true }
            try await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
        } while Date() < deadline
        return false
    }
}

public extension WebEngineProviding {
    /// Opens a page, runs `body`, and always closes the page.
    func withPage<T: Sendable>(profile: String, _ body: (any WebPageDriving) async throws -> T) async throws -> T {
        let page = try await newPage(profile: profile)
        do {
            let value = try await body(page)
            await page.close()
            return value
        } catch {
            await page.close()
            throw error
        }
    }
}

/// Web engine profile names. The desktop external downloader used one browser profile
/// for every source that needs a login.
public enum WebProfile {
    public static let external = "external"
    public static let npia = "npia"
}
