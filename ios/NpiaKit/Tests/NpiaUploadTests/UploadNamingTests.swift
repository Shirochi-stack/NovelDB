import XCTest
import NpiaCore
@testable import NpiaUpload

final class UploadNamingTests: UploadTestCase {
    private func url(_ name: String) -> URL {
        URL(fileURLWithPath: "/tmp/" + name)
    }

    func testPlainASCIINamesAreUnchanged() {
        XCTAssertEqual(asciiUploadName(url("My Book #1 (v2).epub"), id: "419368"), "My Book #1 (v2).epub")
        XCTAssertEqual(asciiUploadName(url("book.epub"), id: nil), "book.epub")
    }

    func testNonASCIINamesBecomeIDAndExtension() {
        XCTAssertEqual(asciiUploadName(url("소설 제목.epub"), id: "419368"), "419368.epub")
        XCTAssertEqual(asciiUploadName(url("소설 제목.txt"), id: "419368"), "419368.txt")
        // No id known: a plain stand-in. A non-ASCII extension is dropped.
        XCTAssertEqual(asciiUploadName(url("소설.epub"), id: nil), "book.epub")
        XCTAssertEqual(asciiUploadName(url("소설.확장"), id: "7"), "7")
        // An id with odd characters is cleaned up.
        XCTAssertEqual(asciiUploadName(url("소설.epub"), id: "one|u1"), "one-u1.epub")
        // Decomposed Hangul (what the file system hands back) is composed first, then renamed.
        let decomposed = "소설.epub".decomposedStringWithCanonicalMapping
        XCTAssertEqual(asciiUploadName(url(decomposed), id: "5"), "5.epub")
    }

    func testIDsFromAdapterAndURL() {
        XCTAssertEqual(UploadNaming.sanitizedID("419368"), "419368")
        XCTAssertEqual(UploadNaming.sanitizedID("one|u1"), "one-u1")
        XCTAssertNil(UploadNaming.sanitizedID("한글"))
        XCTAssertNil(UploadNaming.sanitizedID(nil))

        XCTAssertEqual(UploadNaming.bookID(fromURL: URL(string: "https://example.test/novel/419368")!), "419368")
        XCTAssertEqual(UploadNaming.bookID(fromURL: URL(string: "https://example.test/novel/419368/")!), "419368")
        XCTAssertEqual(UploadNaming.bookID(fromURL: URL(string: "https://example.test/series/abc/12/chapters")!), "12")
        XCTAssertEqual(UploadNaming.bookID(fromURL: URL(string: "https://example.test/books/my-story/")!), "books-my-story")
        XCTAssertEqual(UploadNaming.bookID(fromURL: URL(string: "https://example.test/")!), "book")
        XCTAssertEqual(UploadNaming.bookID(fromURL: URL(string: "https://example.test/%ED%95%9C%EA%B8%80")!), "book")

        let url = URL(string: "https://example.test/novel/419368")!
        XCTAssertEqual(UploadNaming.nameID(bookID: "abc", url: url), "abc")
        XCTAssertEqual(UploadNaming.nameID(bookID: "한글", url: url), "419368")
        XCTAssertEqual(UploadNaming.nameID(bookID: nil, url: url), "419368")
    }

    func testNameIDCarriesTheSourceCodename() {
        let url = URL(string: "https://example.test/novel/419368")!
        XCTAssertEqual(UploadNaming.nameID(source: "npia", bookID: "419368", url: url), "npia_419368")
        XCTAssertEqual(UploadNaming.nameID(source: "npia", bookID: nil, url: url), "npia_419368")
        XCTAssertEqual(UploadNaming.nameID(source: "npia", bookID: "한글", url: url), "npia_419368")
        XCTAssertEqual(UploadNaming.nameID(source: "global_npia", bookID: "77", url: url), "global_npia_77")
        XCTAssertEqual(UploadNaming.nameID(source: nil, bookID: "77", url: url), "77")
        XCTAssertEqual(UploadNaming.nameID(source: "", bookID: "77", url: url), "77")
    }

    func testASCIITitleKeepsTheLatinPartOfAMixedTitle() {
        XCTAssertEqual(UploadNaming.asciiTitle("Solo Leveling"), "Solo Leveling")
        XCTAssertEqual(UploadNaming.asciiTitle("Solo Leveling 나 혼자만 레벨업"), "Solo Leveling")
        XCTAssertEqual(UploadNaming.asciiTitle("[완결] Solo Leveling (1-50)"), "Solo Leveling (1-50)")
        XCTAssertEqual(UploadNaming.asciiTitle("My Book #1 (v2)"), "My Book #1 (v2)")
        XCTAssertEqual(UploadNaming.asciiTitle("Title (한글)"), "Title")
        XCTAssertEqual(UploadNaming.asciiTitle("시리즈 Vol 12 완결"), "Vol 12")
    }

    func testASCIITitleNeedsThreeLettersOrDigits() {
        // The cases from the phone: Korean titles used to shrink to almost nothing.
        XCTAssertNil(UploadNaming.asciiTitle("소설 제목"))
        XCTAssertNil(UploadNaming.asciiTitle("-_"))
        XCTAssertNil(UploadNaming.asciiTitle(""))
        XCTAssertNil(UploadNaming.asciiTitle("VR게임"), "two letters are not enough")
        XCTAssertNil(UploadNaming.asciiTitle("a-b"), "separators do not count")
        XCTAssertEqual(UploadNaming.asciiTitle("VR 게임 7"), "VR 7")
        XCTAssertEqual(UploadNaming.asciiTitle("100화"), "100")
        XCTAssertEqual(UploadNaming.minimumTitleCharacters, 3)
    }

    func testASCIITitleIsCutAtOneHundredCharacters() throws {
        let long = String(repeating: "word ", count: 40)
        let title = try XCTUnwrap(UploadNaming.asciiTitle(long))
        XCTAssertLessThanOrEqual(title.count, 100)
        XCTAssertFalse(title.hasSuffix(" "))
    }

    func testUploadedFilesGetAUsefulASCIIName() {
        // Plain titles stay; a Latin part is kept; otherwise <source>_<book id>.
        XCTAssertEqual(asciiUploadName(url("Solo Leveling 나 혼자만 레벨업.epub"), id: "npia_419368"), "Solo Leveling.epub")
        XCTAssertEqual(asciiUploadName(url("소설 제목.epub"), id: "npia_419368"), "npia_419368.epub")
        XCTAssertEqual(asciiUploadName(url("VR게임.epub"), id: "npia_419368"), "npia_419368.epub")
        XCTAssertEqual(asciiUploadName(url("100화.txt"), id: "npia_419368"), "100.txt")
        XCTAssertEqual(asciiUploadName(url("a.epub"), id: "npia_419368"), "npia_419368.epub", "an ASCII title this short is replaced too")
        XCTAssertEqual(asciiUploadName(url("NovelDB_3_EPUBs.zip"), id: nil), "NovelDB_3_EPUBs.zip")
        // What the Android phone showed (".epub" with the Korean part stripped, "-_", "VR_"): none
        // of these reaches a host as a name.
        XCTAssertEqual(asciiUploadName(url("-_.epub"), id: "npia_9"), "npia_9.epub")
        XCTAssertEqual(asciiUploadName(url("VR_.epub"), id: "npia_9"), "npia_9.epub")
        XCTAssertEqual(asciiUploadName(url("_.epub"), id: "npia_9"), "npia_9.epub")
    }

    func testRFC5987Encoding() {
        XCTAssertEqual(UploadNaming.rfc5987("소설 \"1\".epub"), "%EC%86%8C%EC%84%A4%20%221%22.epub")
        XCTAssertEqual(UploadNaming.rfc5987("a-b_c.d~e"), "a-b_c.d~e")
        XCTAssertEqual(UploadNaming.rfc5987("50%"), "50%25")
    }

    func testMultipartHeaderCarriesBothNames() throws {
        let file = try makeFile("소설 제목.epub", bytes: 10)
        var ctx = context()
        ctx.nameID = "419368"
        var form = ctx.form(fileField: "file", file: file)
        form.boundary = "B"
        let head = String(decoding: form.head, as: UTF8.self)
        XCTAssertTrue(head.contains(#"filename="419368.epub""#), head)
        XCTAssertTrue(
            head.contains("filename*=UTF-8''%EC%86%8C%EC%84%A4%20%EC%A0%9C%EB%AA%A9.epub"), head
        )
    }

    func testPlainNamesGetNoExtendedParameter() throws {
        let file = try makeFile("book.epub", bytes: 10)
        let head = String(decoding: context().form(fileField: "file", file: file).head, as: UTF8.self)
        XCTAssertTrue(head.contains(#"filename="book.epub""#))
        XCTAssertFalse(head.contains("filename*="))
    }

    func testHostsReceiveTheASCIINameButTheResultKeepsTheTitle() async throws {
        let file = try makeFile("소설 제목.epub")
        StubURLProtocol.handler = { _ in .text("https://temp.sh/AbCdE/419368.epub\n") }
        var ctx = context()
        ctx.nameID = "419368"

        let result = try await TempShUploader().upload(file, context: ctx, progress: nil)

        XCTAssertEqual(result.fileName, "소설 제목.epub", "the Links list shows the real name")
        if let body = StubURLProtocol.seen.first?.body {
            let text = String(decoding: body, as: UTF8.self)
            XCTAssertNotNil(text.range(of: #"filename="419368.epub""#))
            XCTAssertNotNil(text.range(of: "filename*=UTF-8''"))
        }
    }

    func testPixeldrainUsesTheASCIINameInTheURL() async throws {
        let file = try makeFile("소설 제목.epub")
        XCTAssertEqual(
            PixeldrainUploader.request(for: file, apiKey: "k", size: 1, name: "419368.epub").url?.absoluteString,
            "https://pixeldrain.com/api/file/419368.epub"
        )
        // Without an id the stand-in name is used rather than percent-encoded Korean.
        XCTAssertEqual(PixeldrainUploader.request(for: file, apiKey: "k", size: 1).url?.lastPathComponent, "book.epub")

        StubURLProtocol.handler = { _ in .json(#"{"success":true,"id":"Zz"}"#, status: 201) }
        var ctx = context(secrets: [.pixeldrainAPIKey: "k"])
        ctx.nameID = "419368"
        let result = try await PixeldrainUploader().upload(file, context: ctx, progress: nil)
        XCTAssertEqual(StubURLProtocol.seen.first?.request.url?.lastPathComponent, "419368.epub")
        XCTAssertEqual(result.fileName, "소설 제목.epub")
    }

    func testCatalogPassesTheNameIDToHosts() async throws {
        let file = try makeFile("소설 제목.epub")
        StubURLProtocol.handler = { _ in .text("https://temp.sh/x/419368.epub") }
        var settings = UploadSettings()
        settings.hosts = ["tempsh"]

        let result = try await UploaderCatalog.upload(
            file, settings: settings, context: context(settings: settings), history: nil, nameID: "419368"
        )

        XCTAssertEqual(result.fileName, "소설 제목.epub")
        if let body = StubURLProtocol.seen.first?.body {
            XCTAssertNotNil(String(decoding: body, as: UTF8.self).range(of: #"filename="419368.epub""#))
        }
    }
}
