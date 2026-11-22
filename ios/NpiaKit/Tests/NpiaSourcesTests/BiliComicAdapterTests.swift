import CommonCrypto
import XCTest
import NpiaCore
@testable import NpiaSources

final class BiliComicAdapterTests: XCTestCase {
    private let host = "https://manga.hdslb.com/bfs/manga/"

    private var jpeg: Data {
        var bytes: [UInt8] = [0xFF, 0xD8, 0xFF, 0xE0]
        bytes += (0..<40).map { UInt8($0) }
        return Data(bytes)
    }

    private var png: Data {
        var bytes: [UInt8] = [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]
        bytes += (0..<40).map { UInt8($0) }
        return Data(bytes)
    }

    private var ref: ChapterRef {
        ChapterRef(id: "https://manga.bilibili.com/mc29839/10", index: 1, title: "01 First",
                   url: URL(string: "https://manga.bilibili.com/mc29839/10"))
    }

    private func chapter(_ names: [String]) -> ChapterResult {
        var images: [ImageRef] = []
        for (index, name) in names.enumerated() {
            let url = URL(string: host + name + "?token=abc&ts=6b02b691")!
            images.append(ImageRef(url: url, filename: String(format: "images/0001_%02d.jpg", index + 1)))
        }
        return .content(ChapterContent(ref: ref, html: "<p></p>", images: images))
    }

    func testTheSitesBookAndChapterPagesBelongToTheBuiltInScraper() {
        let book = URL(string: "https://manga.bilibili.com/detail/mc29839")!
        let pages = ["https://manga.bilibili.com/detail/mc29839", "https://manga.bilibili.com/mc29839",
                     "https://manga.bilibili.com/mc29839/585738", "https://manga.bilibili.com/m/detail/mc29839?from=x"]
        for text in pages {
            let url = URL(string: text)!
            XCTAssertTrue(BiliComicAdapter.matches(url), text)
            XCTAssertEqual(SourceCatalog.adapterType(for: url)?.id, .bilicomic, text)
        }
        let others = ["https://manga.bilibili.com/", "https://manga.bilibili.com/ranking",
                      "https://www.bilibili.com/video/mc29839", "https://manga.bilibili.com.evil.test/detail/mc1"]
        for text in others {
            XCTAssertFalse(BiliComicAdapter.matches(URL(string: text)!), text)
        }
        let settings = EngineSettings(order: [.native, .haruneko, .generic])
        XCTAssertEqual(SiteEngineRouter.plan(for: book, engines: settings).first, .native)
        XCTAssertTrue(SourceCatalog.downloadablePage(book, engines: settings))
        XCTAssertTrue(SourceCatalog.supportedSite(URL(string: "https://manga.bilibili.com/")!))
    }

    func testADownloadFetchesAChaptersPagesAsSoonAsItIsRead() async throws {
        let http = RoutedHTTP()
        http.on("a.jpg", data: jpeg)
        http.on("b.jpg", data: png)
        let arrived = CollectingLogger()
        var context = makeContext(http)
        context.onImage = { ref, position, filename, bytes, _ in
            arrived.log(.debug, "\(ref.index)/\(position) \(filename) \(bytes)")
        }
        let result = try await BiliComicPages.attach(to: chapter(["a.jpg", "b.jpg"]), context: context)
        // Each page is reported as it arrives, so its log line is written while the chapters are read.
        XCTAssertEqual(arrived.lines.sorted(), ["1/0 images/0001_01.jpg \(jpeg.count)", "1/1 images/0001_02.jpg \(png.count)"])
        let images = try XCTUnwrap(content(result)).images
        XCTAssertEqual(images.count, 2)
        XCTAssertEqual(images[0].data, jpeg)
        XCTAssertEqual(images[1].data, png)
        XCTAssertEqual(images.map(\.filename), ["images/0001_01.jpg", "images/0001_02.jpg"])
        let requests = http.requests
        XCTAssertEqual(requests.count, 2)
        for request in requests {
            XCTAssertEqual(request.headers["Referer"], "https://manga.bilibili.com/")
            XCTAssertTrue(request.url.absoluteString.contains("token=abc&ts=6b02b691"))
        }
    }

    func testAPageThatCannotBeFetchedFailsTheChapterSoItIsReadAgain() async throws {
        let http = RoutedHTTP()
        http.on("a.jpg", data: jpeg)
        http.on("c.jpg", "<html>refused</html>")  // an answer that is not an image
        let missing = try await BiliComicPages.attach(to: chapter(["a.jpg", "gone.jpg"]), context: makeContext(http))
        guard case .failed(_, let message) = missing else { return XCTFail("the chapter should fail") }
        XCTAssertEqual(message, "Page 2 of 2 could not be downloaded.")
        let wrong = try await BiliComicPages.attach(to: chapter(["c.jpg"]), context: makeContext(http))
        guard case .failed(_, let other) = wrong else { return XCTFail("the chapter should fail") }
        XCTAssertEqual(other, "Page 1 of 1 could not be downloaded.")
        // A refused chapter passes through untouched.
        let refused = ChapterResult.refused(ref, reason: "locked")
        let passed = try await BiliComicPages.attach(to: refused, context: makeContext(http))
        XCTAssertTrue(isRefused(passed))
    }

    func testAProtectedPageIsDecoded() throws {
        var imageBytes = [UInt8](jpeg)
        imageBytes += (0..<25_600).map { UInt8($0 % 251) }  // longer than the encrypted start
        let image = Data(imageBytes)
        let key = [UInt8](0..<16)
        let iv = [UInt8](16..<32)
        // The first 20480 bytes are encrypted with one block of padding (20496 bytes); the rest is plain.
        var plain = Array(imageBytes[0..<20480])
        plain += [UInt8](repeating: 16, count: 16)
        var encrypted = [UInt8](repeating: 0, count: plain.count + kCCBlockSizeAES128)
        let capacity = encrypted.count
        var moved = 0
        let status = CCCrypt(CCOperation(kCCEncrypt), CCAlgorithm(kCCAlgorithmAES), CCOptions(0),
                             key, key.count, iv, plain, plain.count, &encrypted, capacity, &moved)
        XCTAssertEqual(Int(status), kCCSuccess)
        var body = Array(encrypted[0..<moved])
        body += Array(imageBytes[20480..<imageBytes.count])
        var storedBytes: [UInt8] = [1]
        storedBytes.append(UInt8((body.count >> 24) & 0xFF))
        storedBytes.append(UInt8((body.count >> 16) & 0xFF))
        storedBytes.append(UInt8((body.count >> 8) & 0xFF))
        storedBytes.append(UInt8(body.count & 0xFF))
        storedBytes += body
        storedBytes += key
        let stored = Data(storedBytes)
        var cpxBytes = [UInt8](repeating: 0, count: 60)
        cpxBytes += iv
        cpxBytes += [UInt8](repeating: 0, count: 8)
        let cpx = Data(cpxBytes).base64EncodedString()
            .replacingOccurrences(of: "=", with: "")
            .replacingOccurrences(of: "+", with: "%2B")
            .replacingOccurrences(of: "/", with: "%2F")
        let url = URL(string: host + "a.jpg?token=t&ts=6b02b691&cpx=" + cpx)!
        XCTAssertEqual(try BiliComicPages.decode(stored, url: url), image)
        XCTAssertEqual(try BiliComicPages.decode(jpeg, url: url), jpeg)  // already an image
        // No key in the address, and an unknown layout.
        XCTAssertThrowsError(try BiliComicPages.decode(stored, url: URL(string: host + "a.jpg?token=t&ts=6b02b691")!))
        var unknown = storedBytes
        unknown[0] = 2
        XCTAssertThrowsError(try BiliComicPages.decode(Data(unknown), url: url))
    }
}
