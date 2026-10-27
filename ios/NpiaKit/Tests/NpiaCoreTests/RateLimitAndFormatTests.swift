import XCTest
@testable import NpiaCore

final class RateLimitAndFormatTests: XCTestCase {
    func testIntervalIsClampedAndOrderedLikeDesktop() {
        var shared = SharedSettings()
        shared.intervalMin = 2
        shared.intervalMax = 0.5
        XCTAssertEqual(shared.normalizedInterval, 0.5...2)

        shared.intervalMin = -3
        shared.intervalMax = 900
        XCTAssertEqual(shared.normalizedInterval, 0...300)
    }

    func testChapterDelayIsFixedWhenMinEqualsMaxAndZeroWhenOff() {
        var shared = SharedSettings()
        shared.intervalMin = 1.5
        shared.intervalMax = 1.5
        XCTAssertEqual(shared.chapterDelay(), 1.5)

        shared.intervalMin = 0
        shared.intervalMax = 0
        XCTAssertEqual(shared.chapterDelay(), 0)

        shared.intervalMin = 0.2
        shared.intervalMax = 0.8
        for _ in 0..<50 {
            XCTAssertTrue((0.2...0.8).contains(shared.chapterDelay()))
        }
    }

    func testCBZRoundTripsThroughDesktopConfig() {
        let config: [String: Any] = ["ext_formats": ["epub", "cbz"], "save_formats": ["CBZ"]]
        let (settings, _) = DesktopConfig.importConfig(config)
        XCTAssertEqual(settings.shared.formats, [.epub, .cbz])
        XCTAssertEqual(settings.effective(for: .npia).formats, [.cbz])

        let exported = DesktopConfig.exportConfig(settings)
        XCTAssertEqual(exported["ext_formats"] as? [String], ["epub", "cbz"])
        XCTAssertEqual(exported["save_formats"] as? [String], ["cbz"])
        XCTAssertEqual(OutputFormat(rawValue: "cbz"), .cbz)
    }

    func testFontMappingDecodesEachMappedCharacter() throws {
        let map = try FontMapping.parse(Data(#"{"a": "x", "b": "yz", "long": "ignored"}"#.utf8))
        XCTAssertEqual(map.count, 2)
        XCTAssertEqual(FontMapping.decode("abc", with: map), "xyzc")
        XCTAssertThrowsError(try FontMapping.parse(Data("[1, 2]".utf8)))
    }
}
