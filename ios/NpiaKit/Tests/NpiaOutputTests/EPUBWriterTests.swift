import XCTest
import NpiaCore
import ZIPFoundation
@testable import NpiaOutput

/// Shared fixture: one notice, two chapters (one image present, one missing), and a cover.
func makeWriterBundle(numbered: Bool = true, cover: Bool = true) -> BookBundle {
    var settings = AppSettings()
    settings.shared.numberChapters = numbered
    let book = BookInfo(
        source: .npia, id: "123", url: URL(string: "https://example.com/novel/123")!, title: "Tower & Me",
        author: "Writer <Kim>", synopsis: "Line one\nLine two", coverURL: URL(string: "https://example.com/c.jpg"),
        tags: ["Fantasy", "Regression"], status: "Completed"
    )
    let png = Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 1, 2, 3])
    let image = ImageRef(url: URL(string: "https://example.com/a.png")!, filename: "images/0002_01.png")
    let missing = ImageRef(url: URL(string: "https://example.com/b.png")!, filename: "images/0002_02.png")
    let chapters = [
        ChapterContent(ref: ChapterRef(id: "1", index: 1, title: "Start"), html: "<p>Hello&nbsp;world &amp; more<br>next</p>"),
        ChapterContent(
            ref: ChapterRef(id: "2", index: 2, title: "Pictures"),
            html: #"<p><img alt="1" src="images/0002_01.png" width="100%"/>a</p><p><img alt="2" src="images/0002_02.png"/>b</p><p><img class="remote-image" src="https://example.com/r.png"/></p>"#,
            images: [image, missing]
        ),
        ChapterContent(ref: ChapterRef(id: "901", index: 1, title: "Notice: Hi", isNotice: true), html: "<p>notice body</p>"),
    ]
    return BookBundle(
        book: book, chapters: chapters, images: ["images/0002_01.png": png],
        cover: cover ? ProcessedCover(data: Data([0xFF, 0xD8, 0xFF, 0xE0]), fileExtension: "jpg") : nil,
        settings: settings
    )
}

final class EPUBWriterTests: XCTestCase {
    private func tempDir() -> URL {
        FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    }

    private func read(_ archive: Archive, _ path: String) throws -> String {
        let entry = try XCTUnwrap(archive[path], "missing \(path)")
        var data = Data()
        _ = try archive.extract(entry) { data.append($0) }
        return String(decoding: data, as: UTF8.self)
    }

    private func assertWellFormed(_ xml: String, _ name: String, file: StaticString = #filePath, line: UInt = #line) {
        let parser = XMLParser(data: Data(xml.utf8))
        XCTAssertTrue(parser.parse(), "\(name) is not well-formed XML: \(String(describing: parser.parserError))", file: file, line: line)
    }

    func testStructure() async throws {
        let url = try await EPUBWriter().write(makeWriterBundle(), baseName: "book", to: tempDir())
        XCTAssertEqual(url.lastPathComponent, "book.epub")

        // The mimetype entry is first and stored: local header at offset 0, method 0, name at 30.
        let raw = try Data(contentsOf: url)
        XCTAssertEqual(Array(raw.prefix(4)), [0x50, 0x4B, 0x03, 0x04])
        XCTAssertEqual(raw[8], 0)
        XCTAssertEqual(raw[9], 0)
        let nameLength = Int(raw[26]) | Int(raw[27]) << 8
        let extraLength = Int(raw[28]) | Int(raw[29]) << 8
        XCTAssertEqual(String(decoding: raw[30..<(30 + nameLength)], as: UTF8.self), "mimetype")
        let contentStart = 30 + nameLength + extraLength
        XCTAssertEqual(String(decoding: raw[contentStart..<(contentStart + 20)], as: UTF8.self), "application/epub+zip")

        let archive = try Archive(url: url, accessMode: .read, pathEncoding: nil)
        let paths = Array(archive).map(\.path)
        XCTAssertEqual(paths.first, "mimetype")
        XCTAssertEqual(Set(paths), [
            "mimetype", "META-INF/container.xml", "OEBPS/content.opf", "OEBPS/nav.xhtml", "OEBPS/toc.ncx",
            "OEBPS/Styles/style.css", "OEBPS/Text/cover.xhtml", "OEBPS/Text/info.xhtml",
            "OEBPS/Text/chapter_notice0001.xhtml", "OEBPS/Text/chapter0001.xhtml", "OEBPS/Text/chapter0002.xhtml",
            "OEBPS/images/cover.jpg", "OEBPS/images/0002_01.png",
        ])

        for path in paths where path.hasSuffix(".xhtml") || path.hasSuffix(".opf") || path.hasSuffix(".ncx") || path.hasSuffix(".xml") {
            assertWellFormed(try read(archive, path), path)
        }

        let opf = try read(archive, "OEBPS/content.opf")
        XCTAssertTrue(opf.contains(#"<package version="3.0""#))
        XCTAssertTrue(opf.contains("<dc:title>Tower &amp; Me</dc:title>"))
        XCTAssertTrue(opf.contains("<dc:creator>Writer &lt;Kim&gt;</dc:creator>"))
        XCTAssertTrue(opf.contains("<dc:subject>Regression</dc:subject>"))
        XCTAssertTrue(opf.contains("<dc:language>ko</dc:language>"))
        XCTAssertTrue(opf.contains(#"properties="nav""#))
        XCTAssertTrue(opf.contains(#"href="images/cover.jpg" media-type="image/jpeg" properties="cover-image""#))
        XCTAssertTrue(opf.contains(#"href="images/0002_01.png" media-type="image/png""#))
        XCTAssertTrue(opf.contains(#"property="dcterms:modified""#))
        // Spine: cover, info, notices, then chapters.
        let spine = opf.components(separatedBy: "<itemref idref=\"").dropFirst().map { $0.prefix { $0 != "\"" } }
        XCTAssertEqual(spine, ["coverpage", "info", "chapter_notice0001", "chapter0001", "chapter0002"])

        let nav = try read(archive, "OEBPS/nav.xhtml")
        XCTAssertTrue(nav.contains(#"epub:type="toc""#))
        XCTAssertTrue(nav.contains(#"<a href="Text/chapter0001.xhtml">1. Start</a>"#))
        XCTAssertTrue(nav.contains(#"<a href="Text/chapter_notice0001.xhtml">Notice: Hi</a>"#))
        XCTAssertTrue(try read(archive, "OEBPS/toc.ncx").contains(#"<content src="Text/chapter0002.xhtml"/>"#))

        let first = try read(archive, "OEBPS/Text/chapter0001.xhtml")
        XCTAssertTrue(first.contains("<h1>1. Start</h1>"))
        XCTAssertTrue(first.contains("Hello&#160;world &amp; more<br/>next"))

        let second = try read(archive, "OEBPS/Text/chapter0002.xhtml")
        XCTAssertTrue(second.contains(#"src="../images/0002_01.png""#))
        XCTAssertFalse(second.contains("0002_02.png"), "missing images are dropped")
        XCTAssertTrue(second.contains(#"src="https://example.com/r.png""#))

        XCTAssertTrue(try read(archive, "OEBPS/Text/cover.xhtml").contains(#"src="../images/cover.jpg""#))
        let info = try read(archive, "OEBPS/Text/info.xhtml")
        XCTAssertTrue(info.contains("Completed"))
        XCTAssertTrue(info.contains("Line one<br/>Line two"))
    }

    func testUnnumberedWithRemoteCover() async throws {
        let url = try await EPUBWriter().write(makeWriterBundle(numbered: false, cover: false), baseName: "plain", to: tempDir())
        let archive = try Archive(url: url, accessMode: .read, pathEncoding: nil)
        XCTAssertTrue(try read(archive, "OEBPS/Text/chapter0001.xhtml").contains("<h1>Start</h1>"))
        XCTAssertTrue(try read(archive, "OEBPS/Text/cover.xhtml").contains(#"src="https://example.com/c.jpg""#))
        XCTAssertFalse(try read(archive, "OEBPS/content.opf").contains("cover-image"))
        XCTAssertNil(archive["OEBPS/images/cover.jpg"])
    }
}

final class HTMLWriterTests: XCTestCase {
    func testSingleFileEmbedsImages() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let url = try await HTMLWriter().write(makeWriterBundle(), baseName: "book", to: dir)
        XCTAssertEqual(url.lastPathComponent, "book.html")
        let html = try String(contentsOf: url, encoding: .utf8)
        XCTAssertTrue(html.hasPrefix("<!DOCTYPE html>"))
        XCTAssertTrue(html.contains("<title>Tower &amp; Me</title>"))
        let png = Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 1, 2, 3]).base64EncodedString()
        XCTAssertTrue(html.contains("src=\"data:image/png;base64,\(png)\""))
        XCTAssertTrue(html.contains("src=\"data:image/jpeg;base64,/9j/4A==\""), "cover is embedded")
        XCTAssertFalse(html.contains("images/0002_"), "no local file references remain")
        XCTAssertTrue(html.contains(#"src="https://example.com/r.png""#))

        // TOC links resolve to sections, notices first.
        let notice = try XCTUnwrap(html.range(of: "<h2>Notice: Hi</h2>"))
        let first = try XCTUnwrap(html.range(of: "<h2>1. Start</h2>"))
        XCTAssertLessThan(notice.lowerBound, first.lowerBound)
        XCTAssertTrue(html.contains(#"<a href="#notice-1">Notice: Hi</a>"#))
        XCTAssertTrue(html.contains(#"<section class="chapter" id="notice-1">"#))
        XCTAssertTrue(html.contains(#"<a href="#chapter-2-3">2. Pictures</a>"#))
        XCTAssertTrue(html.contains(#"id="chapter-2-3""#))
    }
}
