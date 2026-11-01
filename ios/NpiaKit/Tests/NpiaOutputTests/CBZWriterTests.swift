import XCTest
import NpiaCore
import ZIPFoundation
@testable import NpiaOutput

final class CBZWriterTests: XCTestCase {
    func testEntriesFollowDesktopLayout() {
        let bundle = makeWriterBundle()
        let paths = CBZWriter.entries(for: bundle).map(\.path)
        XCTAssertEqual(paths, ["0000_cover.jpg", "ComicInfo.xml", "0002_Pictures/0001.png"])
        XCTAssertEqual(CBZWriter.chaptersWithoutImages(bundle).map(\.id), ["1"])
    }

    func testComicInfoIsEscaped() throws {
        let info = try XCTUnwrap(CBZWriter.entries(for: makeWriterBundle()).first { $0.path == "ComicInfo.xml" })
        let xml = String(decoding: info.data, as: UTF8.self)
        XCTAssertTrue(xml.contains("<Title>Tower &amp; Me</Title>"))
        XCTAssertTrue(xml.contains("<Writer>Writer &lt;Kim&gt;</Writer>"))
        XCTAssertTrue(XMLParser(data: info.data).parse())
    }

    func testImagesAreStoredUnlessZipCompressionIsOn() {
        var bundle = makeWriterBundle()
        XCTAssertFalse(CBZWriter.entries(for: bundle).first { $0.path.hasSuffix(".png") }!.compressed)
        bundle.settings.images.zipCompressImages = true
        XCTAssertTrue(CBZWriter.entries(for: bundle).first { $0.path.hasSuffix(".png") }!.compressed)
    }

    func testWritesArchiveAndSkipsBooksWithoutImages() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let url = try await CBZWriter().write(makeWriterBundle(), baseName: "book", to: directory)
        XCTAssertEqual(url.pathExtension, "cbz")
        let archive = try Archive(url: url, accessMode: .read, pathEncoding: nil)
        XCTAssertNotNil(archive["0002_Pictures/0001.png"])

        var empty = makeWriterBundle()
        empty.images = [:]
        do {
            _ = try await CBZWriter().write(empty, baseName: "empty", to: directory)
            XCTFail("expected nothingToWrite")
        } catch OutputError.nothingToWrite(let reason) {
            XCTAssertEqual(reason, "CBZ skipped: no images in this book")
        }
    }

    func testSanitizeMatchesDesktop() {
        XCTAssertEqual(CBZWriter.sanitize(" a:b?c. "), "a_b_c")
        XCTAssertEqual(CBZWriter.sanitize("..."), "novel")
    }
}
