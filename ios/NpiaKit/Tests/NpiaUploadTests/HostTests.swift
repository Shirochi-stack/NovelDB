import XCTest
import NpiaCore
@testable import NpiaUpload

final class MultipartTests: UploadTestCase {
    func testBodyIsHeadFileTail() throws {
        let file = try makeFile("소설 \"1\".epub", bytes: 3 * 1024 + 7)
        var form = MultipartForm(fileField: "fileToUpload", file: file, fields: [(name: "reqtype", value: "fileupload")])
        form.boundary = "B"
        let out = directory.appendingPathComponent("body")
        let size = try form.write(to: out, chunkSize: 1024)

        let expectedHead = "--B\r\nContent-Disposition: form-data; name=\"reqtype\"\r\n\r\nfileupload\r\n"
            + "--B\r\nContent-Disposition: form-data; name=\"fileToUpload\"; filename=\"book.epub\"\r\n"
            + "Content-Type: application/octet-stream\r\n\r\n"
        var expected = Data(expectedHead.utf8)
        expected.append(try Data(contentsOf: file))
        expected.append(Data("\r\n--B--\r\n".utf8))

        XCTAssertEqual(try Data(contentsOf: out), expected)
        XCTAssertEqual(size, Int64(expected.count))
        XCTAssertEqual(form.contentType, "multipart/form-data; boundary=B")
    }

    func testFormEncodingEscapesReservedAndUnicode() {
        let body = String(decoding: formEncoded([("a b", "x&y=z/é~")]), as: UTF8.self)
        XCTAssertEqual(body, "a%20b=x%26y%3Dz%2F%C3%A9~")
    }
}

final class HostTests: UploadTestCase {
    private func reply(_ text: String, status: Int = 200, headers: [String: String] = [:]) -> HTTPReply {
        HTTPReply(status: status, headers: headers.reduce(into: [:]) { $0[$1.key.lowercased()] = $1.value }, body: Data(text.utf8))
    }

    // MARK: temp.sh

    func testTempShUploadsMultipartAndReadsPlainLink() async throws {
        let file = try makeFile()
        StubURLProtocol.handler = { _ in .text("https://temp.sh/AbCdE/book.epub\n") }
        let progressBox = ProgressBox()
        let result = try await TempShUploader().upload(file, context: context(), progress: { progressBox.add($0) })

        XCTAssertEqual(result.link.absoluteString, "https://temp.sh/AbCdE/book.epub")
        XCTAssertEqual(result.expiresAt, fixedNow.addingTimeInterval(3 * 86_400))
        let request = try XCTUnwrap(StubURLProtocol.seen.first?.request)
        XCTAssertEqual(request.url, TempShUploader.endpoint)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertTrue(request.value(forHTTPHeaderField: "Content-Type")?.hasPrefix("multipart/form-data; boundary=") == true)
        if let body = StubURLProtocol.seen.first?.body {
            XCTAssertNotNil(String(decoding: body, as: UTF8.self).range(of: #"name="file"; filename="book.epub""#))
        }
        XCTAssertEqual(progressBox.last?.sentBytes, progressBox.last?.totalBytes)
    }

    func testTempShRejectsNonLink() {
        XCTAssertThrowsError(try TempShUploader.parse(reply("<html>error</html>")))
    }

    // MARK: Gofile

    func testGofileSendsTokenWhenPresent() async throws {
        let file = try makeFile()
        StubURLProtocol.handler = { _ in .json(#"{"status":"ok","data":{"downloadPage":"https://gofile.io/d/Xy9","id":"f"}}"#) }
        let result = try await GofileUploader().upload(file, context: context(secrets: [.gofileToken: "tok"]), progress: nil)
        XCTAssertEqual(result.link.absoluteString, "https://gofile.io/d/Xy9")
        let request = try XCTUnwrap(StubURLProtocol.seen.first?.request)
        XCTAssertEqual(request.url?.absoluteString, "https://upload.gofile.io/uploadfile")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer tok")
    }

    func testGofileErrorStatus() {
        XCTAssertThrowsError(try GofileUploader.parse(reply(#"{"status":"error-rateLimit","data":{}}"#))) { error in
            XCTAssertEqual(error as? UploadError, .rejected(host: "Gofile", message: "error-rateLimit"))
        }
    }

    // MARK: Catbox

    func testCatboxParsesLinkAndSendsUserHash() async throws {
        let file = try makeFile()
        StubURLProtocol.handler = { _ in .text("https://files.catbox.moe/abc123.epub") }
        let result = try await CatboxUploader().upload(file, context: context(secrets: [.catboxUserHash: "h"]), progress: nil)
        XCTAssertEqual(result.link.absoluteString, "https://files.catbox.moe/abc123.epub")
        XCTAssertNil(result.expiresAt)
        if let body = StubURLProtocol.seen.first?.body.map({ String(decoding: $0, as: UTF8.self) }) {
            XCTAssertNotNil(body.range(of: "name=\"reqtype\"\r\n\r\nfileupload\r\n"))
            XCTAssertNotNil(body.range(of: "name=\"userhash\"\r\n\r\nh\r\n"))
            XCTAssertNotNil(body.range(of: "name=\"fileToUpload\"; filename=\"book.epub\""))
        }
    }

    // MARK: Pixeldrain

    func testPixeldrainRequestUsesPutAndBasicAuth() throws {
        let file = directory.appendingPathComponent("My Book #1?.epub")
        let request = PixeldrainUploader.request(for: file, apiKey: "key", size: 42)
        XCTAssertEqual(request.httpMethod, "PUT")
        XCTAssertEqual(request.url?.absoluteString, "https://pixeldrain.com/api/file/My%20Book%20%231%3F.epub")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Basic " + Data(":key".utf8).base64EncodedString())
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Length"), "42")
    }

    func testPixeldrainParse() throws {
        XCTAssertEqual(try PixeldrainUploader.parse(reply(#"{"success":true,"id":"Ab12"}"#, status: 201)).absoluteString, "https://pixeldrain.com/u/Ab12")
        XCTAssertThrowsError(try PixeldrainUploader.parse(reply(#"{"success":false,"value":"file_too_large","message":"too big"}"#, status: 413))) { error in
            XCTAssertEqual(error as? UploadError, .rejected(host: "Pixeldrain", message: "too big"))
        }
        XCTAssertThrowsError(try PixeldrainUploader.parse(reply(#"{"success":false}"#, status: 401))) { error in
            XCTAssertEqual(error as? UploadError, .notSignedIn("Pixeldrain"))
        }
    }

    func testPixeldrainNeedsKey() {
        XCTAssertFalse(PixeldrainUploader().isReady(context()))
        XCTAssertTrue(PixeldrainUploader().isReady(context(secrets: [.pixeldrainAPIKey: "k"])))
    }

    func testPixeldrainUploadsRawFile() async throws {
        let file = try makeFile()
        StubURLProtocol.handler = { _ in .json(#"{"success":true,"id":"Zz"}"#, status: 201) }
        let result = try await PixeldrainUploader().upload(file, context: context(secrets: [.pixeldrainAPIKey: "k"]), progress: nil)
        XCTAssertEqual(result.link.absoluteString, "https://pixeldrain.com/u/Zz")
        if let body = StubURLProtocol.seen.first?.body {
            XCTAssertEqual(body, try Data(contentsOf: file))
        }
    }

    // MARK: tmpfiles

    func testTmpfilesUpgradesToHTTPSAndSetsExpiry() async throws {
        let file = try makeFile()
        StubURLProtocol.handler = { _ in .json(#"{"status":"success","data":{"url":"http://tmpfiles.org/123456/book.epub"}}"#) }
        let result = try await TmpfilesUploader().upload(file, context: context(), progress: nil)
        XCTAssertEqual(result.link.absoluteString, "https://tmpfiles.org/123456/book.epub")
        XCTAssertEqual(result.expiresAt, fixedNow.addingTimeInterval(172_800))
        if let body = StubURLProtocol.seen.first?.body.map({ String(decoding: $0, as: UTF8.self) }) {
            XCTAssertNotNil(body.range(of: "name=\"expire\"\r\n\r\n172800\r\n"))
        }
    }

    // MARK: KrakenFiles

    func testKrakenFilesAsksForServerThenUploads() async throws {
        let file = try makeFile()
        StubURLProtocol.handler = { request in
            if request.url == KrakenFilesUploader.serverLookup {
                return .json(#"{"data":{"url":"https://s7.krakenfiles.com/_uploader/upload/xyz","serverAccessToken":"t"}}"#)
            }
            XCTAssertEqual(request.url?.absoluteString, "https://s7.krakenfiles.com/_uploader/gallery/upload")
            return .json(#"{"files":[{"name":"book.epub","url":"/view/Qx12/file.html","hash":"Qx12"}]}"#)
        }
        let result = try await KrakenFilesUploader().upload(file, context: context(), progress: nil)
        XCTAssertEqual(result.link.absoluteString, "https://krakenfiles.com/view/Qx12/file.html")
        XCTAssertEqual(StubURLProtocol.seen.count, 2)
        if let body = StubURLProtocol.seen.last?.body.map({ String(decoding: $0, as: UTF8.self) }) {
            XCTAssertNotNil(body.range(of: "name=\"files[]\"; filename=\"book.epub\""))
        }
    }

    func testKrakenFilesFileError() {
        XCTAssertThrowsError(try KrakenFilesUploader.parse(reply(#"{"files":[{"error":"File type not allowed"}]}"#))) { error in
            XCTAssertEqual(error as? UploadError, .rejected(host: "KrakenFiles", message: "File type not allowed"))
        }
    }

    // MARK: Send.now

    func testSendNowFindsServerOnHomePage() throws {
        let html = #"<form enctype="multipart/form-data" action="https://ab12cd.send.now/cgi-bin/upload.cgi?upload_type=file" method="post">"#
        XCTAssertEqual(try SendNowUploader.uploadEndpoint(fromHomePage: reply(html)).absoluteString,
                       "https://ab12cd.send.now/cgi-bin/upload.cgi?upload_type=file&utype=anon")
        XCTAssertThrowsError(try SendNowUploader.uploadEndpoint(fromHomePage: reply("<html></html>")))
    }

    func testSendNowParse() throws {
        XCTAssertEqual(try SendNowUploader.parse(reply(#"[{"file_code":"k3j4h5","file_status":"OK"}]"#)).absoluteString, "https://send.now/k3j4h5")
        XCTAssertThrowsError(try SendNowUploader.parse(reply(#"[{"file_code":"undef","file_status":"too big"}]"#)))
    }
}

final class ProgressBox: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [UploadProgress] = []

    func add(_ value: UploadProgress) {
        lock.lock(); values.append(value); lock.unlock()
    }

    var all: [UploadProgress] {
        lock.lock(); defer { lock.unlock() }
        return values
    }

    var last: UploadProgress? { all.last }
}
