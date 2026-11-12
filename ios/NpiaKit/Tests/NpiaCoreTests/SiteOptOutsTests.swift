import CryptoKit
import XCTest
@testable import NpiaCore

final class SiteOptOutsTests: XCTestCase {
    // The shipped list names no site, so the tests bring their own.
    private let site = "optout.example"
    private var listed: Set<String> {
        [SHA256.hash(data: Data(site.utf8)).map { String(format: "%02x", $0) }.joined()]
    }

    func testASiteAndItsSubdomainsAreOptedOut() {
        for address in [
            "https://\(site)/novel/some-book/", "http://\(site)", "https://www.\(site)/", "https://m.reader.\(site)/chapter/1?x=1#top",
            "https://\(site.uppercased())/", "https://\(site).:443/a", site, "www.\(site)/novel/1",
            "https://user@\(site)/a", "https://\(site)/a book with spaces", "//\(site)/a",
        ] {
            XCTAssertTrue(SiteOptOuts.matches(address, digests: listed), address)
        }
    }

    func testOtherAddressesAreNot() {
        for address in [
            "", "not a url", "https://example/", "https://other.example/", "https://not\(site)/",
            "https://\(site).evil.test/", "https://evil.test/\(site)/", "https://evil.test/?u=https://\(site)/",
            "file:///var/mobile/Library/book.epub",
        ] {
            XCTAssertFalse(SiteOptOuts.matches(address, digests: listed), address)
        }
    }

    func testTheShippedListLeavesOrdinarySitesAlone() {
        XCTAssertFalse(SiteOptOuts.digests.isEmpty)
        XCTAssertTrue(SiteOptOuts.digests.allSatisfy { $0.count == 64 })
        XCTAssertFalse(SiteOptOuts.matches("https://\(site)/"))
        XCTAssertFalse(SiteOptOuts.matches(URL(string: "https://example.com/novel/1")!))
    }

    func testTheErrorSaysWhy() {
        XCTAssertEqual(SourceError.siteDisabled.localizedDescription, SiteOptOuts.message)
        XCTAssertTrue(SiteOptOuts.message.contains("request of the site owner"))
    }
}
