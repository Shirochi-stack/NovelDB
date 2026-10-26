import CoreGraphics
import ImageIO
import XCTest
import NpiaCore
@testable import NpiaOutput
#if canImport(PDFKit)
import PDFKit
#endif

final class PDFWriterTests: XCTestCase {
    private func png(width: Int, height: Int) -> Data {
        let context = CGContext(
            data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
        )!
        context.setFillColor(CGColor(red: 0.9, green: 0.3, blue: 0.1, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        let output = NSMutableData()
        let destination = CGImageDestinationCreateWithData(output, "public.png" as CFString, 1, nil)!
        CGImageDestinationAddImage(destination, context.makeImage()!, nil)
        CGImageDestinationFinalize(destination)
        return output as Data
    }

    private func bundle(chapters: [ChapterContent], images: [String: Data] = [:], cover: Data? = nil, _ configure: (inout AppSettings) -> Void = { _ in }) -> BookBundle {
        var settings = AppSettings()
        configure(&settings)
        let book = BookInfo(
            source: .reader, id: "7", url: URL(string: "https://example.com/7")!, title: "Test Book",
            author: "Writer", synopsis: "A short synopsis.\nSecond line.", tags: ["fantasy"], language: "en"
        )
        return BookBundle(book: book, chapters: chapters, images: images, cover: cover.map { ProcessedCover(data: $0, fileExtension: "png") }, settings: settings)
    }

    private func pageCount(_ url: URL) -> Int {
        CGPDFDocument(url as CFURL)?.numberOfPages ?? 0
    }

    private func textChapters(_ count: Int, paragraphs: Int = 3) -> [ChapterContent] {
        (1...count).map { index in
            let body = (0..<paragraphs).map { "<p>Paragraph \($0) of chapter \(index) &amp; more text.</p>" }.joined()
            return ChapterContent(ref: ChapterRef(id: "\(index)", index: index, title: "Chapter \(index)"), html: body)
        }
    }

    private var tempDir: URL {
        FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    }

    func testBasicLayoutPages() async throws {
        let url = try await PDFWriter().write(bundle(chapters: textChapters(3)), baseName: "book", to: tempDir)
        XCTAssertEqual(url.pathExtension, "pdf")
        // Info page + one page per chapter.
        XCTAssertEqual(pageCount(url), 4)
    }

    func testCoverAndTOCPages() async throws {
        let b = bundle(chapters: textChapters(3), cover: png(width: 60, height: 90)) { $0.text.pdfTOC = true }
        let url = try await PDFWriter().write(b, baseName: "book", to: tempDir)
        // Cover + info + TOC + 3 chapters.
        XCTAssertEqual(pageCount(url), 6)
    }

    func testCounterLayoutListsChapterPages() async throws {
        let b = bundle(chapters: textChapters(2)) {
            $0.text.pdfTOC = true
            $0.text.pdfCounterLayout = true
            $0.text.pdfPageNumbers = true
        }
        let plan = PDFDocumentPlan(b)
        let starts = try PDFRenderer(plan: plan, context: nil, tocPageNumbers: nil).render()
        // Info is page 1, TOC page 2, chapters start on 3 and 4.
        XCTAssertEqual(starts, [3, 4])

        let url = try await PDFWriter().write(b, baseName: "book", to: tempDir)
        XCTAssertEqual(pageCount(url), 4)
        #if canImport(PDFKit)
        let document = try XCTUnwrap(PDFDocument(url: url))
        let toc = document.page(at: 1)?.string ?? ""
        XCTAssertTrue(toc.contains("Table of Contents"))
        XCTAssertTrue(toc.contains("Chapter 1"))
        XCTAssertTrue(toc.contains("3"))
        let first = document.page(at: 0)?.string ?? ""
        XCTAssertTrue(first.contains("Test Book"))
        XCTAssertTrue(first.contains("Writer"))
        let chapter = document.page(at: 2)?.string ?? ""
        XCTAssertTrue(chapter.contains("Paragraph 0 of chapter 1 & more text."))
        #endif
    }

    func testLongTextFlowsOntoMorePages() async throws {
        let url = try await PDFWriter().write(bundle(chapters: textChapters(1, paragraphs: 400)), baseName: "book", to: tempDir)
        XCTAssertGreaterThan(pageCount(url), 4)
    }

    func testImagesAreEmbedded() async throws {
        let chapter = ChapterContent(
            ref: ChapterRef(id: "1", index: 1, title: "Pictures"),
            html: #"<p>Before</p><p><img src="../images/1.png"/></p><p>After</p>"#,
            images: [ImageRef(url: URL(string: "https://example.com/1.png")!, filename: "images/1.png")]
        )
        let image = png(width: 200, height: 300)
        let withImage = try await PDFWriter().write(bundle(chapters: [chapter], images: ["images/1.png": image]), baseName: "a", to: tempDir)
        let without = try await PDFWriter().write(bundle(chapters: [chapter]), baseName: "b", to: tempDir)
        let sizeWith = try withImage.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        let sizeWithout = try without.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        XCTAssertGreaterThan(sizeWith, sizeWithout)
    }

    func testLongImageLayoutSlicesStrips() async throws {
        let strip = png(width: 100, height: 3_000)
        let chapter = ChapterContent(
            ref: ChapterRef(id: "1", index: 1, title: "Episode"),
            html: #"<div class="ntk-webtoon-page"><img src="images/1.png"/></div>"#,
            images: [ImageRef(url: URL(string: "https://example.com/1.png")!, filename: "images/1.png")]
        )
        let normal = try await PDFWriter().write(bundle(chapters: [chapter], images: ["images/1.png": strip]), baseName: "n", to: tempDir)
        // Info page, then the title and the page-sized image on its own page.
        XCTAssertEqual(pageCount(normal), 3)

        let long = try await PDFWriter().write(bundle(chapters: [chapter], images: ["images/1.png": strip]) { $0.images.longImageLayout = true }, baseName: "l", to: tempDir)
        // 3000 px scaled to the 451 pt column is about 13,500 pt: 21 pages of 698 pt.
        XCTAssertGreaterThanOrEqual(pageCount(long), 20)
        XCTAssertTrue(LongImageLayout.applies(to: bundle(chapters: [chapter]) { $0.images.longImageLayout = true }))
    }

    func testParserBlocks() {
        let html = #"<h2>Title</h2><p>Hello <b>bold</b>  and<br/>next &#x41;&#66;&lt;</p><ruby>漢<rt>kan</rt></ruby><img src='x.png'><script>bad()</script><!-- note -->"#
        let blocks = PDFHTMLParser.blocks(html)
        XCTAssertEqual(blocks, [
            .paragraph([PDFRun(text: "Title", bold: false, italic: false)], heading: 2),
            .paragraph([
                PDFRun(text: "Hello ", bold: false, italic: false),
                PDFRun(text: "bold", bold: true, italic: false),
                PDFRun(text: " and\u{2028}next AB<", bold: false, italic: false),
            ], heading: nil),
            .paragraph([PDFRun(text: "漢", bold: false, italic: false)], heading: nil),
            .image("x.png"),
        ])
    }

    func testImageLookupFallsBackToLeafName() {
        let b = bundle(chapters: [], images: ["images/a.webp": Data([1])])
        let plan = PDFDocumentPlan(b)
        XCTAssertEqual(plan.image(for: "../images/a.webp"), Data([1]))
        XCTAssertEqual(plan.image(for: "../Images/a.webp"), Data([1]))
        XCTAssertNil(plan.image(for: "b.webp"))
    }
}
