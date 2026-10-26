import XCTest
import NpiaCore
@testable import NpiaUpload

final class UploaderCatalogTests: XCTestCase {
    func testIDsAreUnique() {
        let ids = UploaderCatalog.all.map(\.id)
        XCTAssertEqual(Set(ids).count, ids.count)
    }

    func testDefaultHostsExist() {
        for id in UploadSettings().hosts {
            XCTAssertNotNil(UploaderCatalog.uploader(id: id), id)
        }
    }

    func testTooLargeFileIsRejectedBeforeUpload() throws {
        let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try Data(count: 2048).write(to: file)
        let tiny = PendingUploader(id: "tiny", displayName: "Tiny", maxBytes: 1024, retention: nil, requiresAccount: false)
        XCTAssertThrowsError(try tiny.check(file)) { error in
            XCTAssertEqual(error as? UploadError, .tooLarge(host: "Tiny", limitBytes: 1024))
        }
    }
}
