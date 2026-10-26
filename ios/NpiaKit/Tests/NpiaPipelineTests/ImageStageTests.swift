import CoreGraphics
import ImageIO
import XCTest
import NpiaCore
import NpiaImages
@testable import NpiaPipeline

private func makePNG(width: Int = 12, height: Int = 10) -> Data {
    let context = CGContext(
        data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
        space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
    )!
    context.setFillColor(CGColor(red: 0.2, green: 0.6, blue: 0.9, alpha: 1))
    context.fill(CGRect(x: 0, y: 0, width: width, height: height))
    let output = NSMutableData()
    let destination = CGImageDestinationCreateWithData(output, "public.png" as CFString, 1, nil)!
    CGImageDestinationAddImage(destination, context.makeImage()!, nil)
    CGImageDestinationFinalize(destination)
    return output as Data
}

/// Serves fixed bytes per URL and records how many requests overlap.
private final class StubHTTP: HTTPFetching, @unchecked Sendable {
    private let lock = NSLock()
    private var responses: [URL: (Int, Data)]
    private var active = 0
    private(set) var maxActive = 0
    private(set) var requests: [URL] = []

    init(_ responses: [URL: (Int, Data)]) {
        self.responses = responses
    }

    func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        lock.withLock {
            active += 1
            maxActive = max(maxActive, active)
            requests.append(request.url)
        }
        try await Task.sleep(nanoseconds: 20_000_000)
        let (status, body) = lock.withLock { () -> (Int, Data) in
            active -= 1
            return responses[request.url] ?? (404, Data())
        }
        return HTTPResponse(url: request.url, status: status, headers: [:], body: body)
    }
}

final class ImageStageTests: XCTestCase {
    private func url(_ name: String) -> URL { URL(string: "https://img.example.com/\(name)")! }

    private func chapter(_ index: Int, images: [ImageRef]) -> ChapterContent {
        let html = images.map { "<p><img src=\"../\($0.filename)\" alt=\"\"/></p>" }.joined()
        return ChapterContent(ref: ChapterRef(id: "\(index)", index: index, title: "Ch \(index)"), html: html, images: images)
    }

    func testRenamesToWebPAndRewritesHTML() async throws {
        let png = makePNG()
        let http = StubHTTP([url("a"): (200, png), url("b"): (200, png)])
        let chapters = [
            chapter(1, images: [ImageRef(url: url("a"), filename: "images/0001_01.png")]),
            chapter(2, images: [ImageRef(url: url("b"), filename: "images/0002_01.png"), ImageRef(url: url("a"), filename: "images/0001_01.png")]),
        ]
        let output = try await ImageStage(http: http, settings: AppSettings(), source: .reader).run(chapters)

        XCTAssertEqual(Set(output.images.keys), ["images/0001_01.webp", "images/0002_01.webp"])
        XCTAssertEqual(output.failed, 0)
        XCTAssertEqual(output.chapters[0].images.map(\.filename), ["images/0001_01.webp"])
        XCTAssertTrue(output.chapters[0].html.contains("src=\"../images/0001_01.webp\""))
        XCTAssertTrue(output.chapters[1].html.contains("../images/0002_01.webp"))
        XCTAssertTrue(output.chapters[1].html.contains("../images/0001_01.webp"))
        XCTAssertFalse(output.chapters[1].html.contains(".png"))
        // The shared image is downloaded once.
        XCTAssertEqual(http.requests.filter { $0 == url("a") }.count, 1)
    }

    func testKeepsNamesWhenExtensionMatches() async throws {
        let http = StubHTTP([url("a"): (200, makePNG())])
        var settings = AppSettings()
        settings.images.compress = false
        let output = try await ImageStage(http: http, settings: settings, source: .reader)
            .run([chapter(1, images: [ImageRef(url: url("a"), filename: "images/1.png")])])
        XCTAssertEqual(Array(output.images.keys), ["images/1.png"])
        XCTAssertTrue(output.chapters[0].html.contains("../images/1.png"))
    }

    func testExtensionlessNamesGainExtension() async throws {
        let http = StubHTTP([url("a"): (200, makePNG())])
        var settings = AppSettings()
        settings.images.compress = false
        let output = try await ImageStage(http: http, settings: settings, source: .reader)
            .run([chapter(1, images: [ImageRef(url: url("a"), filename: "images/1")])])
        XCTAssertEqual(Array(output.images.keys), ["images/1.png"])
        XCTAssertTrue(output.chapters[0].html.contains("../images/1.png"))
    }

    func testRenameCollisionGetsSuffix() {
        let processed = ["a.png": ProcessedImage(data: Data(), fileExtension: "webp")]
        let renames = ImageStage.renames(for: ["a.png", "a.webp"], processed: processed)
        XCTAssertEqual(renames, ["a.png": "a_2.webp"])
    }

    func testFailedDownloadsAreDroppedAndCounted() async throws {
        let http = StubHTTP([url("ok"): (200, makePNG())])
        let output = try await ImageStage(http: http, settings: AppSettings(), source: .reader).run([
            chapter(1, images: [ImageRef(url: url("ok"), filename: "1.png"), ImageRef(url: url("missing"), filename: "2.png")]),
        ])
        XCTAssertEqual(output.failed, 1)
        XCTAssertEqual(output.chapters[0].images.map(\.filename), ["1.webp"])
    }

    func testInlineBytesSkipDownload() async throws {
        let http = StubHTTP([:])
        let output = try await ImageStage(http: http, settings: AppSettings(), source: .reader).run([
            chapter(1, images: [ImageRef(url: url("x"), filename: "1.png", data: makePNG())]),
        ])
        XCTAssertTrue(http.requests.isEmpty)
        XCTAssertNotNil(output.images["1.webp"])
        XCTAssertNil(output.chapters[0].images[0].data)
    }

    func testConcurrencyIsBoundedByImageWorkers() async throws {
        var responses: [URL: (Int, Data)] = [:]
        let png = makePNG(width: 2, height: 2)
        var refs: [ImageRef] = []
        for index in 0..<12 {
            responses[url("\(index)")] = (200, png)
            refs.append(ImageRef(url: url("\(index)"), filename: "\(index).png"))
        }
        let http = StubHTTP(responses)
        var settings = AppSettings()
        settings.shared.imageWorkers = 3
        let progress = ProgressLog()
        let output = try await ImageStage(http: http, settings: settings, source: .reader).run([chapter(1, images: refs)]) { done, total in
            progress.append(done, total)
        }
        XCTAssertEqual(output.images.count, 12)
        XCTAssertLessThanOrEqual(http.maxActive, 3)
        XCTAssertGreaterThan(http.maxActive, 1)
        XCTAssertEqual(progress.last?.0, 12)
        XCTAssertEqual(progress.last?.1, 12)
    }

    func testSaveImageURLsOnlyPointsAtRemote() async throws {
        let http = StubHTTP([:])
        var settings = AppSettings()
        settings.images.saveImageURLsOnly = true
        let output = try await ImageStage(http: http, settings: settings, source: .reader)
            .run([chapter(1, images: [ImageRef(url: url("a"), filename: "images/1.png")])])
        XCTAssertTrue(http.requests.isEmpty)
        XCTAssertTrue(output.images.isEmpty)
        XCTAssertTrue(output.chapters[0].images.isEmpty)
        XCTAssertTrue(output.chapters[0].html.contains("src=\"https://img.example.com/a\""))
    }

    func testAsyncGateLimits() async {
        let gate = AsyncGate(limit: 2)
        let counter = ProgressLog()
        await withTaskGroup(of: Void.self) { group in
            for _ in 0..<6 {
                group.addTask {
                    await gate.acquire()
                    counter.enter()
                    try? await Task.sleep(nanoseconds: 10_000_000)
                    counter.leave()
                    await gate.release()
                }
            }
        }
        XCTAssertLessThanOrEqual(counter.peak, 2)
    }
}

private final class ProgressLog: @unchecked Sendable {
    private let lock = NSLock()
    private var entries: [(Int, Int)] = []
    private var active = 0
    private(set) var peak = 0

    var last: (Int, Int)? { lock.withLock { entries.last } }

    func append(_ done: Int, _ total: Int) {
        lock.withLock { entries.append((done, total)) }
    }

    func enter() {
        lock.withLock {
            active += 1
            peak = max(peak, active)
        }
    }

    func leave() {
        lock.withLock { active -= 1 }
    }
}
