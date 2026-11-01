import XCTest
@testable import NpiaCore

final class LogBufferTests: XCTestCase {
    private var directory: URL!

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent("npia-log-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: directory)
    }

    private var logFile: URL { directory.appendingPathComponent("queue-log.jsonl") }

    /// Whole milliseconds, so a line survives the file's date format unchanged.
    private func moment(_ offset: Double) -> Date {
        Date(timeIntervalSince1970: 1_800_000_000 + offset)
    }

    func testLinesKeepTimeOrderEvenWhenTheyArriveLate() {
        let buffer = LogBuffer()
        buffer.append(.info, "first", date: moment(1))
        buffer.append(.info, "third", date: moment(3))
        buffer.append(.info, "second", date: moment(2))
        buffer.append(.info, "third again", date: moment(3))
        XCTAssertEqual(buffer.all.map(\.message), ["first", "second", "third", "third again"])
        let ids = buffer.all.map(\.id)
        XCTAssertEqual(Set(ids).count, ids.count, "every line has its own id")
    }

    func testTheBufferKeepsOnlyTheLastCapacityLines() {
        let buffer = LogBuffer(capacity: 5)
        for number in 1...12 {
            buffer.append(.info, "line \(number)", date: moment(Double(number)))
        }
        XCTAssertEqual(buffer.count, 5)
        XCTAssertEqual(buffer.all.map(\.message), ["line 8", "line 9", "line 10", "line 11", "line 12"])
    }

    func testDefaultCapacityIsFiveThousand() {
        XCTAssertEqual(LogBuffer.defaultCapacity, 5_000)
        XCTAssertEqual(LogBuffer().capacity, 5_000)
    }

    func testFilterByJobAndDetails() {
        let first = UUID()
        let second = UUID()
        let buffer = LogBuffer()
        buffer.append(.info, "a1", jobID: first, jobTitle: "First", date: moment(1))
        buffer.append(.debug, "a2 detail", jobID: first, jobTitle: "First", date: moment(2))
        buffer.append(.warning, "b1", jobID: second, jobTitle: "Second", date: moment(3))
        buffer.append(.debug, "b2 detail", jobID: second, jobTitle: "Second", date: moment(4))
        buffer.append(.error, "no job", date: moment(5))

        XCTAssertEqual(buffer.entries().map(\.message), ["a1", "a2 detail", "b1", "b2 detail", "no job"])
        XCTAssertEqual(buffer.entries(includeDetails: false).map(\.message), ["a1", "b1", "no job"])
        XCTAssertEqual(buffer.entries(jobID: first).map(\.message), ["a1", "a2 detail"])
        XCTAssertEqual(buffer.entries(jobID: second, includeDetails: false).map(\.message), ["b1"])
        XCTAssertEqual(buffer.entries(jobID: UUID()), [])
    }

    func testJobsListNewestActivityFirstWithTheirLastTitle() {
        let first = UUID()
        let second = UUID()
        let buffer = LogBuffer()
        buffer.append(.info, "x", jobID: first, jobTitle: "https://example.test/novel/1", date: moment(1))
        buffer.append(.info, "y", jobID: second, jobTitle: "Second", date: moment(2))
        buffer.append(.info, "z", jobID: first, jobTitle: "First book", date: moment(3))
        let jobs = buffer.jobs()
        XCTAssertEqual(jobs.map(\.id), [first, second])
        XCTAssertEqual(jobs.map(\.title), ["First book", "Second"])
    }

    func testTheLogSurvivesARelaunch() {
        let job = UUID()
        let buffer = LogBuffer(fileURL: logFile, capacity: 100)
        buffer.append(.info, "Opened the book", jobID: job, jobTitle: "A title", date: moment(1))
        buffer.append(.warning, "Chapter 2 skipped: \"paid\"\nsecond line", jobID: job, jobTitle: "A title", date: moment(2))
        buffer.append(.error, "소설 제목 failed", date: moment(3))
        buffer.flush()

        let again = LogBuffer(fileURL: logFile, capacity: 100)
        XCTAssertEqual(again.all, buffer.all)
        XCTAssertEqual(again.all[1].message, "Chapter 2 skipped: \"paid\"\nsecond line")
        XCTAssertEqual(again.all[1].level, .warning)
        XCTAssertEqual(again.all[1].jobID, job)
        // New lines continue the numbering instead of reusing ids.
        let next = again.append(.info, "after relaunch", date: moment(4))
        XCTAssertEqual(next.id, (buffer.all.map(\.id).max() ?? 0) + 1)
        XCTAssertEqual(again.all.last?.message, "after relaunch")
    }

    func testReloadKeepsOnlyTheLastCapacityLinesAndSkipsATornLine() throws {
        let buffer = LogBuffer(fileURL: logFile, capacity: 100)
        for number in 1...10 {
            buffer.append(.info, "line \(number)", date: moment(Double(number)))
        }
        buffer.flush()
        // A crash can leave half a line at the end.
        let handle = try FileHandle(forWritingTo: logFile)
        try handle.seekToEnd()
        try handle.write(contentsOf: Data(#"{"id":99,"date":1800"#.utf8))
        try handle.close()

        let small = LogBuffer(fileURL: logFile, capacity: 4)
        XCTAssertEqual(small.all.map(\.message), ["line 7", "line 8", "line 9", "line 10"])
    }

    func testTheFileIsRewrittenWhenItGrowsPastTwiceTheCapacity() throws {
        let buffer = LogBuffer(fileURL: logFile, capacity: 5)
        for number in 1...30 {
            buffer.append(.info, "line \(number)", date: moment(Double(number)))
            if number % 4 == 0 { buffer.flush() }
        }
        buffer.flush()
        let lines = try String(contentsOf: logFile, encoding: .utf8).split(separator: "\n")
        XCTAssertLessThanOrEqual(lines.count, 10 + 4, "the file stays near twice the capacity")
        XCTAssertEqual(LogBuffer(fileURL: logFile, capacity: 5).all.map(\.message), (26...30).map { "line \($0)" })
    }

    func testClearEmptiesMemoryAndFile() {
        let buffer = LogBuffer(fileURL: logFile, capacity: 10)
        buffer.append(.info, "one", date: moment(1))
        buffer.flush()
        XCTAssertTrue(FileManager.default.fileExists(atPath: logFile.path))
        buffer.clear()
        XCTAssertEqual(buffer.count, 0)
        XCTAssertFalse(FileManager.default.fileExists(atPath: logFile.path))
        XCTAssertEqual(LogBuffer(fileURL: logFile, capacity: 10).count, 0)
        buffer.append(.info, "two", date: moment(2))
        buffer.flush()
        XCTAssertEqual(LogBuffer(fileURL: logFile, capacity: 10).all.map(\.message), ["two"])
    }

    func testTextExportHasTimeLevelJobAndMessage() throws {
        let buffer = LogBuffer()
        buffer.append(.warning, "Chapter 3 skipped", jobID: UUID(), jobTitle: "My Book", date: moment(0))
        buffer.append(.info, "Done", date: moment(61))
        let utc = try XCTUnwrap(TimeZone(identifier: "UTC"))
        let text = LogBuffer.text(for: buffer.all, timeZone: utc)
        let lines = text.components(separatedBy: "\n")
        XCTAssertEqual(lines.count, 2)
        XCTAssertEqual(lines[0], "2027-01-15 08:00:00 WARNING [My Book] Chapter 3 skipped")
        XCTAssertEqual(lines[1], "2027-01-15 08:01:01 INFO Done")
    }

    func testShortTitles() {
        XCTAssertEqual(LogEntry.shortTitle("Short"), "Short")
        XCTAssertEqual(LogEntry.shortTitle("https://www.example.test/novel/419368"), "example.test/novel/41\u{2026}")
        XCTAssertEqual(LogEntry.shortTitle("A very long book title that goes on and on", limit: 10), "A very lo\u{2026}")
        XCTAssertEqual(LogEntry.shortTitle("  spaced  "), "spaced")
        XCTAssertEqual(LogEntry(jobTitle: "소설 제목", message: "x").shortTitle, "소설 제목")
    }
}
