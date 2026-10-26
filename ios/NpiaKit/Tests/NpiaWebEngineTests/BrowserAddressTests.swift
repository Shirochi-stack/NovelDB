import XCTest
@testable import NpiaWebEngine

final class BrowserAddressTests: XCTestCase {
    func testNormalizeKeepsUrlsAddsHttpsAndSearchesText() {
        XCTAssertEqual(BrowserProfile.normalize(" https://example.com/a ")?.absoluteString, "https://example.com/a")
        XCTAssertEqual(BrowserProfile.normalize("example.com/login")?.absoluteString, "https://example.com/login")
        XCTAssertEqual(BrowserProfile.normalize("light novel")?.absoluteString, "https://duckduckgo.com/?q=light%20novel")
        XCTAssertNil(BrowserProfile.normalize("  "))
    }

    func testRegistrableDomain() {
        XCTAssertEqual(SiteCookies.registrableDomain(".www.example.com"), "example.com")
        XCTAssertEqual(SiteCookies.registrableDomain("m.example.co.kr"), "example.co.kr")
        XCTAssertEqual(SiteCookies.registrableDomain("example.com"), "example.com")
    }
}
