import XCTest
import NpiaCore
@testable import NpiaSources

private final class KeyJar: NpiaCookieSetting, @unchecked Sendable {
    private let lock = NSLock()
    private var values: [String] = []
    var keys: [String] {
        lock.lock(); defer { lock.unlock() }
        return values
    }
    func setLoginKey(_ key: String) {
        lock.lock(); defer { lock.unlock() }
        values.append(key)
    }
}

final class NpiaAuthTests: XCTestCase {
    private let keyPattern = #"^[0-9a-f]{32}_[0-9a-f]{32}$"#

    private func context(_ http: RoutedHTTP, secrets: InMemorySecretStore, logger: CollectingLogger, autoLogin: Bool = false) -> SourceContext {
        var settings = AppSettings()
        settings.npia.autoLogin = autoLogin
        return SourceContext(http: http, web: StubWeb(), secrets: secrets, settings: settings, logger: logger)
    }

    func testSavedCredentialsSignInEvenWithAutoLoginOff() async throws {
        let http = RoutedHTTP()
        http.on("/proc/login", NpiaAuth.successMarker)
        let secrets = InMemorySecretStore([.npiaEmail: "user@example.com", .npiaPassword: "secret"])
        let jar = KeyJar()
        let logger = CollectingLogger()

        let key = try await NpiaAuth.prepare(context: context(http, secrets: secrets, logger: logger), cookies: jar)

        XCTAssertEqual(http.requested("/proc/login").count, 1)
        XCTAssertNotNil(key.range(of: keyPattern, options: .regularExpression))
        XCTAssertEqual(secrets.string(for: .npiaLoginKey), key, "the session key is stored for later jobs")
        XCTAssertEqual(jar.keys, [key])
        XCTAssertFalse(logger.lines.contains { $0.contains("no saved login") })
    }

    func testFailedSignInWarnsAndFallsBackToThrowawayKey() async throws {
        let http = RoutedHTTP()
        http.on("/proc/login", "wrong password")
        let secrets = InMemorySecretStore([.npiaEmail: "user@example.com", .npiaPassword: "bad"])
        let jar = KeyJar()
        let logger = CollectingLogger()

        let key = try await NpiaAuth.prepare(context: context(http, secrets: secrets, logger: logger), cookies: jar)

        XCTAssertEqual(http.requested("/proc/login").count, 1, "a failed sign-in is not repeated")
        XCTAssertNotNil(key.range(of: keyPattern, options: .regularExpression))
        XCTAssertNil(secrets.string(for: .npiaLoginKey))
        XCTAssertEqual(jar.keys.last, key)
        XCTAssertTrue(logger.lines.contains("npia sign-in failed; check the email and password in Settings > npia"))
        XCTAssertTrue(logger.lines.contains("npia: no saved login; only free chapters can be read."))
    }

    func testSavedKeyBeatsSavedCredentialsWhenAutoLoginIsOff() async throws {
        let http = RoutedHTTP()
        let secrets = InMemorySecretStore([.npiaEmail: "a", .npiaPassword: "b", .npiaLoginKey: "saved_key"])
        let jar = KeyJar()

        let key = try await NpiaAuth.prepare(context: context(http, secrets: secrets, logger: CollectingLogger()), cookies: jar)

        XCTAssertEqual(key, "saved_key")
        XCTAssertEqual(jar.keys, ["saved_key"])
        XCTAssertTrue(http.requested("/proc/login").isEmpty)
    }

    func testAutoLoginFailureFallsBackToSavedKey() async throws {
        let http = RoutedHTTP()
        http.on("/proc/login", "nope")
        let secrets = InMemorySecretStore([.npiaEmail: "a", .npiaPassword: "b", .npiaLoginKey: "saved_key"])
        let jar = KeyJar()
        let logger = CollectingLogger()

        let key = try await NpiaAuth.prepare(context: context(http, secrets: secrets, logger: logger, autoLogin: true), cookies: jar)

        XCTAssertEqual(key, "saved_key")
        XCTAssertEqual(jar.keys.last, "saved_key")
        XCTAssertEqual(http.requested("/proc/login").count, 1)
        XCTAssertTrue(logger.lines.contains("npia sign-in failed; check the email and password in Settings > npia"))
    }

    func testWithoutCredentialsOrKeyUsesThrowawayKey() async throws {
        let http = RoutedHTTP()
        let logger = CollectingLogger()
        let key = try await NpiaAuth.prepare(context: context(http, secrets: InMemorySecretStore(), logger: logger), cookies: KeyJar())
        XCTAssertNotNil(key.range(of: keyPattern, options: .regularExpression))
        XCTAssertTrue(http.requests.isEmpty)
        XCTAssertEqual(logger.lines, ["npia: no saved login; only free chapters can be read."])
    }
}
