import XCTest
import NpiaCore
@testable import NpiaSources

final class MpiaAdapterTests: XCTestCase {
    // The host is read from the escaped constant, so the provider name stays out of this file too.
    private let host = MpiaSite.domain

    private func url(_ address: String) -> URL { URL(string: address)! }

    func testMatchesDetailViewerAndLegacyURLs() {
        XCTAssertTrue(MpiaAdapter.matches(url("https://www.\(host)/novel/detail/12345")))
        XCTAssertTrue(MpiaAdapter.matches(url("https://\(host)/novel/detail/12345/")))
        XCTAssertTrue(MpiaAdapter.matches(url("https://www.\(host)/novel/viewer/12345/67890")))
        XCTAssertTrue(MpiaAdapter.matches(url("https://novel.\(host)/12345")))
        XCTAssertTrue(MpiaAdapter.matches(url("https://novel.\(host)/12345/page/2")))
    }

    func testRejectsOtherURLs() {
        XCTAssertFalse(MpiaAdapter.matches(url("https://www.\(host)/")))
        XCTAssertFalse(MpiaAdapter.matches(url("https://www.\(host)/novel/detail/")))
        XCTAssertFalse(MpiaAdapter.matches(url("https://example.com/novel/detail/1")))
        XCTAssertFalse(MpiaAdapter.matches(url("ftp://www.\(host)/novel/detail/1")))
    }

    func testListsItsSiteAndOwnsSubdomains() {
        XCTAssertEqual(MpiaAdapter.sites, [host])
        XCTAssertTrue(MpiaAdapter.ownsSite(host))
        XCTAssertTrue(MpiaAdapter.ownsSite("www.\(host)"))
        XCTAssertTrue(MpiaAdapter.ownsSite("novel.\(host)"))
        XCTAssertFalse(MpiaAdapter.ownsSite("example.com"))
        XCTAssertEqual(MpiaAdapter.id, .mpia)
    }

    func testCanonicalURLPointsAtTheBookDetailPage() {
        let detail = MpiaSite.bookURL("12345")
        XCTAssertEqual(MpiaAdapter.canonicalURL(url("https://www.\(host)/novel/viewer/12345/67890")), detail)
        XCTAssertEqual(MpiaAdapter.canonicalURL(url("https://novel.\(host)/12345/neSrl/67890")), detail)
        XCTAssertEqual(MpiaAdapter.canonicalURL(url("https://www.\(host)/novel/detail/12345")), detail)
    }
}
