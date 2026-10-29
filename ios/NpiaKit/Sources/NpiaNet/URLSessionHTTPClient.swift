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
    private let logger: (any JobLogging)?
    /// The app's request timeout: every request waits at least this long for the server.
    private let minTimeout: TimeInterval

    /// `logger` receives every retry and every request that ends in an error, so the job log
    /// shows why a page was slow or missing.
    public init(session: URLSession = .shared, pacer: RequestPacer? = nil, maxRetries: Int = 3, userAgent: String = URLSessionHTTPClient.defaultUserAgent, logger: (any JobLogging)? = nil, minTimeout: TimeInterval = TimeInterval(SharedSettings.defaultTimeoutSeconds)) {
        self.session = session
        self.minTimeout = minTimeout
        self.pacer = pacer
        self.maxRetries = max(0, maxRetries)
        self.userAgent = userAgent
        self.logger = logger
    }

    /// Retries follow `maxRetries`. The chapter delay is not applied here: like the desktop
    /// scraper, each chapter worker sleeps `SharedSettings.chapterDelay()` after a chapter.
    public convenience init(settings: SharedSettings, logger: (any JobLogging)? = nil) {
        self.init(maxRetries: settings.maxRetries, logger: logger, minTimeout: TimeInterval(settings.requestTimeoutSeconds))
    }

    public func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        // How long the server may stay silent: what the request asks for or the app's request timeout.
        var urlRequest = URLRequest(url: request.url, timeoutInterval: max(1, request.timeout, minTimeout))
        urlRequest.httpMethod = request.method
        urlRequest.httpBody = request.body
        urlRequest.setValue(userAgent, forHTTPHeaderField: "User-Agent")
        for (name, value) in request.headers {
            urlRequest.setValue(value, forHTTPHeaderField: name)
        }

        let label = Self.label(request)
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
                    try await backoff(attempt, because: "HTTP \(http.statusCode) for \(label)")
                    continue
                }
                if http.statusCode >= 400, !request.quietStatuses.contains(http.statusCode) {
                    // A 404 is often an answer the adapter expects (a missing image, a probe), so
                    // only a server that kept failing is a warning.
                    let level: LogLevel = Self.isRetryable(http.statusCode) ? .warning : .debug
                    logger?.log(level, "HTTP \(http.statusCode) for \(label)" + (attempt > 0 ? " after \(attempt) retries" : ""))
                }
                var headers: [String: String] = [:]
                for (key, value) in http.allHeaderFields {
                    headers[String(describing: key).lowercased()] = String(describing: value)
                }
                return HTTPResponse(url: http.url ?? request.url, status: http.statusCode, headers: headers, body: data)
            } catch let error as URLError where error.code == .appTransportSecurityRequiresSecureConnection && urlRequest.url?.scheme == "http" {
                // App Transport Security allows the app no plain http. Sites that still link http://
                // covers and images (sfc's, for one) serve them over https too, so ask again that way.
                guard let url = urlRequest.url, var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { throw error }
                components.scheme = "https"
                if components.port == 80 { components.port = nil }
                guard let secure = components.url else { throw error }
                urlRequest.url = secure
            } catch let error as URLError where attempt < maxRetries && Self.isRetryable(error) {
                attempt += 1
                try await backoff(attempt, because: "\(label) failed: \(error.localizedDescription)")
            } catch let error as URLError where error.code != .cancelled {
                logger?.log(.warning, "\(label) failed: \(error.localizedDescription)" + (attempt > 0 ? " after \(attempt) retries" : ""))
                throw error
            }
        }
    }

    /// "GET host/path": no query string, which can carry keys.
    static func label(_ request: HTTPRequest) -> String {
        let path = request.url.path.isEmpty ? "/" : request.url.path
        return "\(request.method) \(request.url.host ?? "")\(path)"
    }

    static func isRetryable(_ status: Int) -> Bool {
        status == 429 || (500...599).contains(status)
    }

    static func isRetryable(_ error: URLError) -> Bool {
        [.timedOut, .networkConnectionLost, .cannotConnectToHost, .notConnectedToInternet, .dnsLookupFailed].contains(error.code)
    }

    private func backoff(_ attempt: Int, because reason: String) async throws {
        let seconds = min(30.0, pow(2.0, Double(attempt - 1))) + Double.random(in: 0...0.5)
        logger?.log(.warning, "\(reason); retry \(attempt) of \(maxRetries) in \(String(format: "%.1f", seconds)) s")
        try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
    }
}
