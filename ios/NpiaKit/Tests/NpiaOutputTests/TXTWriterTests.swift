import XCTest
import NpiaCore
@testable import NpiaOutput

final class TXTWriterTests: XCTestCase {
    private func makeBundle(_ configure: (inout AppSettings) -> Void = { _ in }) -> BookBundle {
        var settings = AppSettings()
        configure(&settings)
        let book = BookInfo(source: .reader, id: "42", url: URL(string: "https://example.com/42")!, title: "My: Book", author: "Writer")
        let chapters = [
            ChapterContent(ref: ChapterRef(id: "1", index: 1, title: "Start"), html: "<p>Hello &amp; welcome</p><p>Line two</p>"),
            ChapterContent(ref: ChapterRef(id: "2", index: 2, title: "Next"), html: "<div>Second<br>chapter</div><script>x()</script>"),
        ]
        return BookBundle(book: book, chapters: chapters, settings: settings)
    }

    func testSingleFile() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let bundle = makeBundle { $0.shared.numberChapters = true }
        let name = Writers.baseName(for: bundle.book, settings: bundle.settings.shared)
        XCTAssertEqual(name, "My_ Book")

        let url = try await TXTWriter().write(bundle, baseName: name, to: dir)
        let text = try String(contentsOf: url, encoding: .utf8)
        XCTAssertTrue(text.hasPrefix("My: Book\nWriter\n"))
        XCTAssertTrue(text.contains("1. Start\n\nHello & welcome\nLine two"))
        XCTAssertTrue(text.contains("2. Next\n\nSecond\nchapter"))
        XCTAssertFalse(text.contains("x()"))
    }

    func testSeparateFiles() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let folder = try await TXTWriter().write(makeBundle { $0.shared.separateTXT = true }, baseName: "book", to: dir)
        let files = try FileManager.default.contentsOfDirectory(atPath: folder.path).sorted()
        XCTAssertEqual(files, ["0001.txt", "0002.txt"])
    }

    func testBaseNameAppendsRange() {
        var shared = SharedSettings()
        shared.appendRange = true
        shared.rangeFrom = 3
        let book = BookInfo(source: .npia, id: "7", url: URL(string: "https://example.com")!, title: "Title")
        XCTAssertEqual(Writers.baseName(for: book, settings: shared), "Title (3-end)")
        shared.namingMode = .id
        XCTAssertEqual(Writers.baseName(for: book, settings: shared), "7 (3-end)")
    }
}
