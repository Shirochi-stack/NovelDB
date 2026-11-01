import XCTest
import NpiaCore
@testable import NpiaSources

final class NtkAdapterTests: XCTestCase {
    private func url(_ string: String) -> URL { URL(string: string)! }

    func testRecognisesNtkHostsAndPaths() {
        XCTAssertTrue(NtkAdapter.matches(url("https://ntk01.com/novel/12345")))
        XCTAssertTrue(NtkAdapter.matches(url("https://newtoki468.com/webtoon/999/1000")))
        XCTAssertTrue(NtkAdapter.matches(url("https://sbxh9.com/novel/35110")))
        XCTAssertTrue(NtkAdapter.matches(url("https://www.sbxh12.org/novel/1/2")))
        XCTAssertFalse(NtkAdapter.matches(url("https://example.com/novel/1")))
        XCTAssertFalse(NtkAdapter.matches(url("https://ntk01.com/comic/1")))
        XCTAssertFalse(NtkAdapter.matches(url("https://ntk.com/novel/1")))
        XCTAssertFalse(NtkAdapter.matches(url("https://ntk01.com/")))
    }

    func testOwnsRotatingMirrorHosts() {
        XCTAssertTrue(NtkAdapter.ownsSite("ntk01.com"))
        XCTAssertTrue(NtkAdapter.ownsSite("newtoki468.com"))
        XCTAssertTrue(NtkAdapter.ownsSite("sbxh12.org"))
        XCTAssertTrue(NtkAdapter.ownsSite("www.newtoki1.com"))
        XCTAssertFalse(NtkAdapter.ownsSite("example.com"))
        XCTAssertFalse(NtkAdapter.ownsSite("ntk.com"))
    }

    func testChapterURLCanonicalisesToBookIndex() {
        XCTAssertEqual(NtkAdapter.canonicalURL(url("https://sbxh9.com/novel/35110/812345")).absoluteString, "https://sbxh9.com/novel/35110")
        XCTAssertEqual(NtkAdapter.canonicalURL(url("https://newtoki468.com/webtoon/999/1000")).absoluteString, "https://newtoki468.com/webtoon/999")
        XCTAssertEqual(NtkAdapter.canonicalURL(url("https://sbxh9.com/novel/35110")).absoluteString, "https://sbxh9.com/novel/35110")
        XCTAssertEqual(NtkAdapter.canonicalURL(url("https://example.com/novel/1")).absoluteString, "https://example.com/novel/1")
    }
}
