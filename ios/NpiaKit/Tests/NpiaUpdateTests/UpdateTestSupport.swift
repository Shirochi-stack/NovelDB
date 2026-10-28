import CryptoKit
import Foundation
import XCTest
@testable import NpiaUpdate

/// Answers every request from `StubURLProtocol.handler` and records what was asked, like the
/// stub the upload tests use, plus a body that arrives in slow chunks. No test touches a live host.
final class StubURLProtocol: URLProtocol {
    struct Reply {
        var status = 200
        var headers: [String: String] = [:]
        var body = Data()
        /// Delivers the body in pieces of this size.
        var chunkSize: Int?
        /// Seconds between pieces.
        var pause: TimeInterval = 0

        static func json(_ text: String, status: Int = 200) -> Reply {
            Reply(status: status, headers: ["Content-Type": "application/json"], body: Data(text.utf8))
        }

        static func file(_ data: Data, status: Int = 200, chunkSize: Int? = nil, pause: TimeInterval = 0) -> Reply {
            Reply(status: status, headers: ["Content-Type": "application/octet-stream"], body: data, chunkSize: chunkSize, pause: pause)
        }
    }

    private static let lock = NSLock()
    private static var _handler: ((URLRequest) throws -> Reply)?
    private static var _seen: [URLRequest] = []

    static var handler: ((URLRequest) throws -> Reply)? {
        get { lock.lock(); defer { lock.unlock() }; return _handler }
        set { lock.lock(); _handler = newValue; _seen = []; lock.unlock() }
    }

    static var seen: [URLRequest] {
        lock.lock(); defer { lock.unlock() }
        return _seen
    }

    static func configuration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        return configuration
    }

    private let stopLock = NSLock()
    private var stopped = false

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.lock.lock()
        Self._seen.append(request)
        let handler = Self._handler
        Self.lock.unlock()

        do {
            guard let handler else { throw URLError(.unsupportedURL) }
            let reply = try handler(request)
            let response = HTTPURLResponse(url: request.url!, statusCode: reply.status, httpVersion: "HTTP/1.1", headerFields: reply.headers)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            deliver(reply.body, from: 0, size: max(1, reply.chunkSize ?? max(reply.body.count, 1)), pause: reply.pause)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {
        stopLock.lock(); stopped = true; stopLock.unlock()
    }

    /// Sends the body piece by piece. A pause is a scheduled hop, not a sleep, so `stopLoading`
    /// (a cancelled task) is not stuck behind it.
    private func deliver(_ body: Data, from offset: Int, size: Int, pause: TimeInterval) {
        if isStopped { return }
        let end = min(offset + size, body.count)
        if end > offset { client?.urlProtocol(self, didLoad: body.subdata(in: offset..<end)) }
        if end >= body.count {
            client?.urlProtocolDidFinishLoading(self)
        } else if pause > 0 {
            DispatchQueue.global().asyncAfter(deadline: .now() + pause) { [self] in
                deliver(body, from: end, size: size, pause: pause)
            }
        } else {
            deliver(body, from: end, size: size, pause: pause)
        }
    }

    private var isStopped: Bool {
        stopLock.lock(); defer { stopLock.unlock() }
        return stopped
    }
}

/// A clock the tests move by hand.
final class TestClock: @unchecked Sendable {
    private let lock = NSLock()
    private var current: Date

    init(_ start: Date = Date(timeIntervalSince1970: 1_800_000_000)) {
        current = start
    }

    var date: Date {
        lock.lock(); defer { lock.unlock() }
        return current
    }

    func advance(by seconds: TimeInterval) {
        lock.lock(); current = current.addingTimeInterval(seconds); lock.unlock()
    }
}

/// The IPA the stub serves, with the manifest entry that describes it.
struct FakeRelease {
    static let tag = "v98"
    static let fileName = "NovelDB-unsigned.ipa"

    var payload: Data
    var build = 1_800_000_500
    var label = "hotfix3"

    init(bytes: Int = 5_000, build: Int = 1_800_000_500, label: String = "hotfix3") {
        payload = Data((0..<bytes).map { UInt8(($0 &* 31 &+ 7) % 251) })
        self.build = build
        self.label = label
    }

    var sha256: String { Self.sha256(payload) }

    static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    /// An `update-ios.json` for this release; `entry` replaces fields of the one IPA entry.
    func manifest(entry overrides: [String: Any] = [:], extra: [[String: Any]] = [], top: [String: Any] = [:]) -> Data {
        var entry: [String: Any] = [
            "name": Self.fileName, "variant": "universal", "arch": "universal", "kind": "ipa",
            "build": build, "label": label, "commit": String(repeating: "a", count: 40),
            "size": payload.count, "sha256": sha256, "future_field": ["nested": true],
        ]
        for (key, value) in overrides { entry[key] = value }
        var object: [String: Any] = [
            "schema": 1, "platform": "ios", "tag": Self.tag,
            "release_url": "https://github.com/Shirochi-stack/NovelDB/releases/tag/\(Self.tag)",
            "assets": [entry] + extra, "generated_by": "tests",
        ]
        for (key, value) in top { object[key] = value }
        return try! JSONSerialization.data(withJSONObject: object)
    }
}

struct OfferError: Error {}

/// A temporary folder, a private `UserDefaults` suite and a stubbed network for each test.
class UpdateTestCase: XCTestCase {
    var directory: URL!
    var defaults: UserDefaults!
    private var suite = ""

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent("npia-update-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        suite = "npia-update-tests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite)
        StubURLProtocol.handler = nil
    }

    override func tearDownWithError() throws {
        StubURLProtocol.handler = nil
        defaults.removePersistentDomain(forName: suite)
        try? FileManager.default.removeItem(at: directory)
    }

    var downloads: URL { directory.appendingPathComponent("downloads", isDirectory: true) }

    /// File names left in the download folder.
    var leftovers: [String] {
        ((try? FileManager.default.contentsOfDirectory(atPath: downloads.path)) ?? []).sorted()
    }

    func makeChecker(
        build: AppBuildInfo = AppBuildInfo(build: 1_800_000_000, tag: "v97", label: "none"),
        endpoints: AppUpdateEndpoints = AppUpdateEndpoints(),
        clock: TestClock = TestClock()
    ) -> AppUpdateChecker {
        AppUpdateChecker(
            build: build, endpoints: endpoints, configuration: StubURLProtocol.configuration(),
            defaults: defaults, downloadDirectory: downloads, now: { clock.date }
        )
    }

    /// The `AppUpdate` a check of `release` would offer.
    func offer(_ release: FakeRelease, entry: [String: Any] = [:]) throws -> AppUpdate {
        let outcome = AppUpdateChecker.evaluate(
            manifest: release.manifest(entry: entry),
            build: AppBuildInfo(build: 1_800_000_000, tag: "v97", label: "none")
        )
        guard case .available(let update) = outcome else {
            XCTFail("the manifest did not offer an update: \(outcome)")
            throw OfferError()
        }
        return update
    }
}
