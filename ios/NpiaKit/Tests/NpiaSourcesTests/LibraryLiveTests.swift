import Foundation
import XCTest
import NpiaCore
@testable import NpiaSources

final class LibraryLiveTests: XCTestCase {
    func testMetadataDeltasAndReadyBodiesSurviveCompletion() throws {
        let session = LibraryLive.Session(referer: "https://source.test/book")
        defer { session.close() }
        let refs = (1...3).map { ChapterRef(id: String($0), index: $0, title: "Chapter \($0)") }
        session.book(refs)
        let initial = session.snapshot(session: "", after: 0, key: nil)
        XCTAssertEqual((initial["chapters"] as? [[String: String]])?.count, 3)
        let version = try XCTUnwrap(initial["version"] as? Int)
        session.chapter(.content(ChapterContent(ref: refs[1], html: "<p>Text\n\nNext</p><img src='images/a.png'>",
            images: [ImageRef(url: URL(string: "https://source.test/a.png")!, filename: "images/a.png", data: Data([1, 2, 3]))])))
        let delta = session.snapshot(session: session.id, after: version, key: nil)
        XCTAssertNil(delta["chapters"])
        XCTAssertEqual((delta["states"] as? [[String: String]])?.first?["key"], "1")
        let body = try XCTUnwrap(session.snapshot(session: session.id, after: 0, key: "1")["html"] as? String)
        XCTAssertTrue(body.contains("Text\n\nNext"))
        XCTAssertTrue(body.contains("data:image/png;base64,AQID"))
        XCTAssertNotNil(session.snapshot(session: session.id, after: 0, key: "../1")["error"])
        session.finish(saved: true, spineCount: 5)
        let finished = session.snapshot(session: "", after: 0, key: nil)
        XCTAssertEqual(finished["active"] as? Bool, false)
        XCTAssertEqual(finished["savedOffset"] as? Int, 2)
        XCTAssertEqual((finished["states"] as? [[String: String]])?.filter { $0["state"] == "unavailable" }.count, 2)
        XCTAssertEqual(session.snapshot(session: session.id, after: 0, key: "1")["html"] as? String, body)
    }
}
