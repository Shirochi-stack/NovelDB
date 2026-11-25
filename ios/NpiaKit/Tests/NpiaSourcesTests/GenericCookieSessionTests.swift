import XCTest
import NpiaCore
@testable import NpiaSources

final class GenericCookieSessionTests: XCTestCase {
    private let session = GenericCookieSession(
        key: SecretKey(rawValue: "test.appCookie"),
        cookieURLs: [URL(string: "https://example.com/")!],
        domain: "example.com"
    )

    func testSaveWritesTheStoreAndKeepsACopy() async {
        let secrets = InMemorySecretStore()
        let cookies = FakeCookies()
        let ok = await session.save("a=1; b=2", secrets: secrets, cookies: cookies)
        XCTAssertTrue(ok)
        XCTAssertEqual(cookies.values["a"], "1")
        XCTAssertEqual(cookies.values["b"], "2")
        XCTAssertTrue(session.hasSession(secrets))
        XCTAssertEqual(session.savedHeader(secrets), "a=1; b=2")
    }

    func testRestorePutsSavedCookiesBack() async {
        let secrets = InMemorySecretStore()
        let cookies = FakeCookies()
        _ = await session.save("ses=xyz", secrets: secrets, cookies: cookies)
        cookies.clear()
        let count = await session.restore(secrets, cookies: cookies)
        XCTAssertEqual(count, 1)
        XCTAssertEqual(cookies.values["ses"], "xyz")
    }

    func testSignOutRemovesCookiesAndCopy() async {
        let secrets = InMemorySecretStore()
        let cookies = FakeCookies()
        _ = await session.save("ses=xyz", secrets: secrets, cookies: cookies)
        await session.signOut(secrets, cookies: cookies)
        XCTAssertFalse(session.hasSession(secrets))
        XCTAssertTrue(cookies.removed.contains("ses"))
    }

    func testSaveRejectsHeaderWithoutPairs() async {
        let secrets = InMemorySecretStore()
        let ok = await session.save("garbage", secrets: secrets, cookies: FakeCookies())
        XCTAssertFalse(ok)
        XCTAssertFalse(session.hasSession(secrets))
    }

    func testRestoreWithNoSavedCopyDoesNothing() async {
        let count = await session.restore(InMemorySecretStore(), cookies: FakeCookies())
        XCTAssertEqual(count, 0)
    }
}
