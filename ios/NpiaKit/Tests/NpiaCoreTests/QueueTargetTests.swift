import XCTest
@testable import NpiaCore

final class QueueTargetTests: XCTestCase {
    func testASingleBookOpensItsOwnJob() {
        let only = UUID()
        XCTAssertEqual(QueueTarget.forRun([only]), .job(only))
        // The same book retried is still one book.
        XCTAssertEqual(QueueTarget.forRun([only, only]), .job(only))
    }

    func testSeveralBooksOpenTheCombinedLog() {
        XCTAssertEqual(QueueTarget.forRun([UUID(), UUID()]), .log)
        XCTAssertEqual(QueueTarget.forRun([UUID(), UUID(), UUID()]), .log)
    }

    func testAnEmptyRunOpensTheLog() {
        XCTAssertEqual(QueueTarget.forRun([]), .log)
    }

    func testARemovedJobFallsBackToTheLog() {
        let kept = UUID()
        let removed = UUID()
        XCTAssertEqual(QueueTarget.job(kept).resolved(existing: [kept]), .job(kept))
        XCTAssertEqual(QueueTarget.job(removed).resolved(existing: [kept]), .log)
        XCTAssertEqual(QueueTarget.log.resolved(existing: []), .log)
    }

    func testUserInfoRoundTrips() {
        let id = UUID()
        for target in [QueueTarget.log, .job(id)] {
            // A notification's userInfo arrives as [AnyHashable: Any].
            let info: [AnyHashable: Any] = Dictionary(uniqueKeysWithValues: target.userInfo.map { (AnyHashable($0.key), $0.value as Any) })
            XCTAssertEqual(QueueTarget(userInfo: info), target)
        }
        XCTAssertEqual(QueueTarget.log.userInfo, [QueueTarget.targetKey: "log"])
        XCTAssertEqual(QueueTarget.job(id).userInfo[QueueTarget.jobKey], id.uuidString)
    }

    func testForeignOrBrokenUserInfoIsIgnored() {
        XCTAssertNil(QueueTarget(userInfo: [:]))
        XCTAssertNil(QueueTarget(userInfo: ["other": "log"]))
        XCTAssertNil(QueueTarget(userInfo: [QueueTarget.targetKey: "somewhere"]))
        XCTAssertNil(QueueTarget(userInfo: [QueueTarget.targetKey: "job"]))
        XCTAssertNil(QueueTarget(userInfo: [QueueTarget.targetKey: "job", QueueTarget.jobKey: "not-a-uuid"]))
    }

    func testRunSummaryText() {
        XCTAssertEqual(QueueRunSummary(finished: 1, failed: 0).title, "Downloads finished")
        XCTAssertEqual(QueueRunSummary(finished: 1, failed: 0).body, "1 book done")
        XCTAssertEqual(QueueRunSummary(finished: 3, failed: 1).title, "Downloads ended")
        XCTAssertEqual(QueueRunSummary(finished: 3, failed: 1).body, "3 books done, 1 failed")
        XCTAssertEqual(QueueRunSummary(finished: 0, failed: 0, cancelled: 2).body, "2 cancelled")
        XCTAssertEqual(QueueRunSummary(finished: 0, failed: 0).body, "Nothing was downloaded")
        XCTAssertEqual(QueueRunSummary(finished: 2, failed: 1, cancelled: 1).total, 4)
    }
}
