import Foundation
import NpiaCore

/// Spaces requests out by a random delay in `[min, max]`, like the desktop interval settings.
public actor RequestPacer {
    private let minDelay: Double
    private let maxDelay: Double
    private var nextAllowed = Date.distantPast

    public init(min: Double, max: Double) {
        self.minDelay = Swift.max(0, min)
        self.maxDelay = Swift.max(self.minDelay, max)
    }

    public func wait() async throws {
        let now = Date()
        let start = Swift.max(now, nextAllowed)
        nextAllowed = start.addingTimeInterval(Double.random(in: minDelay...maxDelay))
        let delay = start.timeIntervalSince(now)
        if delay > 0 {
            try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
        }
    }
}

/// `HTTPFetching` over URLSession. Cookies live in `HTTPCookieStorage.shared`, which
/// `NpiaWebEngine` keeps in sync with its web views.
public final class URLSessionHTTPClient: HTTPFetching, @unchecked Sendable {
    public static let defaultUserAgent =
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"

    private let session: URLSession
    private let pacer: RequestPacer?
    private let maxRetries: Int
    private let userAgent: String

    public init(session: URLSession = .shared, pacer: RequestPacer? = nil, maxRetries: Int = 3, userAgent: String = URLSessionHTTPClient.defaultUserAgent) {
        self.session = session
        self.pacer = pacer
        self.maxRetries = max(0, maxRetries)
        self.userAgent = userAgent
    }

    public convenience init(settings: SharedSettings) {
        self.init(pacer: RequestPacer(min: settings.intervalMin, max: settings.intervalMax), maxRetries: settings.maxRetries)
    }

    public func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        var urlRequest = URLRequest(url: request.url, timeoutInterval: request.timeout)
        urlRequest.httpMethod = request.method
        urlRequest.httpBody = request.body
        urlRequest.setValue(userAgent, forHTTPHeaderField: "User-Agent")
        for (name, value) in request.headers {
            urlRequest.setValue(value, forHTTPHeaderField: name)
        }

        var attempt = 0
        while true {
            try Task.checkCancellation()
            try await pacer?.wait()
            do {
                let (data, response) = try await session.data(for: urlRequest)
                guard let http = response as? HTTPURLResponse else {
                    throw HTTPError.invalidResponse(request.url)
                }
                if Self.isRetryable(http.statusCode), attempt < maxRetries {
                    attempt += 1
                    try await backoff(attempt)
                    continue
                }
                var headers: [String: String] = [:]
                for (key, value) in http.allHeaderFields {
                    headers[String(describing: key).lowercased()] = String(describing: value)
                }
                return HTTPResponse(url: http.url ?? request.url, status: http.statusCode, headers: headers, body: data)
            } catch let error as URLError where attempt < maxRetries && Self.isRetryable(error) {
                attempt += 1
                try await backoff(attempt)
            }
        }
    }

    static func isRetryable(_ status: Int) -> Bool {
        status == 429 || (500...599).contains(status)
    }

    static func isRetryable(_ error: URLError) -> Bool {
        [.timedOut, .networkConnectionLost, .cannotConnectToHost, .notConnectedToInternet, .dnsLookupFailed].contains(error.code)
    }

    private func backoff(_ attempt: Int) async throws {
        let seconds = min(30.0, pow(2.0, Double(attempt - 1))) + Double.random(in: 0...0.5)
        try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
    }
}
