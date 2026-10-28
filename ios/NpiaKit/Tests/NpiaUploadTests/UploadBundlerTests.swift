import XCTest
import NpiaCore
import ZIPFoundation
@testable import NpiaUpload

/// Packing, ZIP names and the ZIP file itself. Large files are sparse, so nothing big is
/// written or read.
final class UploadBundlerTests: UploadTestCase {
    private let megabyte = Int64(1_048_576)

    private func sparse(_ name: String, megabytes: Int, in folder: URL? = nil) throws -> URL {
        let url = (folder ?? directory).appendingPathComponent(name)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        XCTAssertTrue(FileManager.default.createFile(atPath: url.path, contents: nil))
        let handle = try FileHandle(forWritingTo: url)
        try handle.truncate(atOffset: UInt64(megabytes) * UInt64(megabyte))
        try handle.close()
        return url
    }

    private func item(_ name: String, megabytes: Int) throws -> UploadItem {
        UploadItem(file: try sparse(name, megabytes: megabytes), title: (name as NSString).deletingPathExtension)
    }

    // MARK: - Packing

    func testFirstFitDecreasingUsesTheFewestZips() {
        // Largest first: 60 + 40 fill one ZIP, 50 + 30 + 20 the other.
        let groups = UploadBundler.pack(costs: [20, 60, 30, 50, 40], limit: 100)
        XCTAssertEqual(groups, [[1, 4], [0, 2, 3]])
    }

    func testAFileThatCannotFitStaysAlone() {
        let groups = UploadBundler.pack(costs: [500, 10, 20], limit: 100)
        XCTAssertEqual(groups, [[0], [1, 2]])
        XCTAssertEqual(UploadBundler.pack(costs: [], limit: 100), [])
        XCTAssertEqual(UploadBundler.pack(costs: [5], limit: 100), [[0]])
    }

    func testEveryFileIsPackedOnceAndNoZipPassesTheLimit() {
        var seed: UInt64 = 12345
        func next() -> Int64 {
            seed = seed &* 6364136223846793005 &+ 1442695040888963407
            return Int64((seed >> 33) % 90) + 1
        }
        let costs = (0..<60).map { _ in next() }
        let limit: Int64 = 100
        let groups = UploadBundler.pack(costs: costs, limit: limit)
        XCTAssertEqual(groups.flatMap { $0 }.sorted(), Array(costs.indices))
        for group in groups {
            XCTAssertLessThanOrEqual(group.reduce(Int64(0)) { $0 + costs[$1] }, limit)
            XCTAssertEqual(group, group.sorted(), "entries keep the order they were given in")
        }
        // At most one ZIP is half empty, so the count stays under twice the total over the limit.
        let lowerBound = Int((costs.reduce(0, +) + limit - 1) / limit)
        XCTAssertLessThanOrEqual(groups.count, lowerBound * 2)
    }

    func testTheHostLimitBeatsTheSetting() {
        XCTAssertEqual(UploadBundler.limitBytes(zipMaxMB: 1024, hostMaxBytes: nil), 1024 * megabyte)
        XCTAssertEqual(UploadBundler.limitBytes(zipMaxMB: 1024, hostMaxBytes: 200_000_000), 200_000_000)
        XCTAssertEqual(UploadBundler.limitBytes(zipMaxMB: 100, hostMaxBytes: 4_000_000_000), 100 * megabyte)
        XCTAssertEqual(UploadBundler.limitBytes(zipMaxMB: 1, hostMaxBytes: nil), 50 * megabyte, "the setting is held to 50-4096")
        XCTAssertEqual(UploadBundler.limitBytes(zipMaxMB: 99_999, hostMaxBytes: nil), 4096 * megabyte)
    }

    func testTheCatalogTakesTheLimitOfTheFirstHostThatCanUpload() {
        var settings = UploadSettings()
        settings.hosts = ["catbox", "tempsh"]
        XCTAssertEqual(UploaderCatalog.hostMaxBytes(settings: settings, context: context(settings: settings)), 200_000_000)
        // An account host without credentials is skipped.
        settings.hosts = ["pixeldrain", "tmpfiles"]
        XCTAssertEqual(UploaderCatalog.hostMaxBytes(settings: settings, context: context(settings: settings)), 100_000_000)
        settings.hosts = ["gofile", "catbox"]
        XCTAssertNil(UploaderCatalog.hostMaxBytes(settings: settings, context: context(settings: settings)), "no published limit")
        settings.hosts = []
        XCTAssertNil(UploaderCatalog.hostMaxBytes(settings: settings, context: context(settings: settings)))
    }

    func testEntriesCostTheirSizeAndAnAllowanceForHeaders() {
        XCTAssertEqual(UploadBundler.cost(size: 1000, name: "a.epub"), 1000 + 1024)
        // A very long Korean name appears twice in the ZIP, so it costs more than 1 KB.
        let long = String(repeating: "가", count: 180) + ".epub"
        XCTAssertGreaterThan(UploadBundler.cost(size: 0, name: long), 1024)
    }

    // MARK: - Plans

    func testPlanPacksBiggestFirstAndNamesEachZipByItsCount() throws {
        let items = [
            try item("a.epub", megabytes: 20), try item("b.epub", megabytes: 60), try item("c.epub", megabytes: 30),
            try item("d.epub", megabytes: 50), try item("e.epub", megabytes: 40),
        ]
        // 101 MB leaves room for the entry headers of two or three files.
        let plan = UploadBundler.plan(items, zipBundle: true, limit: 101 * megabyte)
        XCTAssertEqual(plan.map { $0.items.map(\.fileName) }, [["b.epub", "e.epub"], ["a.epub", "c.epub", "d.epub"]])
        XCTAssertEqual(plan.map(\.zipName), ["NovelDB_2_EPUBs.zip", "NovelDB_3_EPUBs.zip"])
        XCTAssertTrue(plan.allSatisfy { $0.isZip })
    }

    func testAZipNeverGoesOverTheLimitBecauseOfItsHeaders() throws {
        // 50 + 50 MB is exactly 100 MB of data, but the headers push it over.
        let items = [try item("a.epub", megabytes: 50), try item("b.epub", megabytes: 50)]
        let plan = UploadBundler.plan(items, zipBundle: true, limit: 100 * megabyte)
        XCTAssertEqual(plan.count, 2)
        XCTAssertTrue(plan.allSatisfy { !$0.isZip })
    }

    func testAFileOverTheLimitIsUploadedByItself() throws {
        let items = [try item("huge.epub", megabytes: 300), try item("a.epub", megabytes: 10), try item("b.epub", megabytes: 20)]
        let plan = UploadBundler.plan(items, zipBundle: true, limit: 101 * megabyte)
        XCTAssertEqual(plan.count, 2)
        XCTAssertEqual(plan[0].items.map(\.fileName), ["huge.epub"])
        XCTAssertNil(plan[0].zipName, "not zipped")
        XCTAssertEqual(plan[1].items.map(\.fileName), ["a.epub", "b.epub"])
        XCTAssertEqual(plan[1].zipName, "NovelDB_2_EPUBs.zip")
    }

    func testABundleOfOneFileIsNotZipped() throws {
        let items = [try item("a.epub", megabytes: 80), try item("b.epub", megabytes: 80), try item("c.epub", megabytes: 10)]
        let plan = UploadBundler.plan(items, zipBundle: true, limit: 101 * megabyte)
        XCTAssertEqual(plan.map { $0.items.map(\.fileName) }, [["a.epub", "c.epub"], ["b.epub"]])
        XCTAssertEqual(plan.map(\.zipName), ["NovelDB_2_EPUBs.zip", nil])
    }

    func testSameNamedZipsGetNumbers() throws {
        let items = try (1...6).map { try item("book\($0).epub", megabytes: 40) }
        let plan = UploadBundler.plan(items, zipBundle: true, limit: 90 * megabyte)
        XCTAssertEqual(plan.map(\.zipName), ["NovelDB_2_EPUBs.zip", "NovelDB_2_EPUBs_2.zip", "NovelDB_2_EPUBs_3.zip"])
    }

    func testZipNamesCountEntriesAndSayEPUBsOnlyWhenEveryEntryIsOne() {
        let names = UploadBundler.zipNames([
            (count: 4, allEPUB: true), (count: 4, allEPUB: true), (count: 4, allEPUB: true),
            (count: 3, allEPUB: false), (count: 4, allEPUB: false), (count: 3, allEPUB: false),
        ])
        XCTAssertEqual(names, [
            "NovelDB_4_EPUBs.zip", "NovelDB_4_EPUBs_2.zip", "NovelDB_4_EPUBs_3.zip",
            "NovelDB_3_files.zip", "NovelDB_4_files.zip", "NovelDB_3_files_2.zip",
        ])
        XCTAssertTrue(names.allSatisfy { $0.unicodeScalars.allSatisfy(\.isASCII) })
    }

    func testAMixedZipIsNamedFiles() throws {
        let items = [try item("a.epub", megabytes: 1), try item("b.txt", megabytes: 1), try item("c.EPUB", megabytes: 1)]
        let plan = UploadBundler.plan(items, zipBundle: true, limit: 100 * megabyte)
        XCTAssertEqual(plan.map(\.zipName), ["NovelDB_3_files.zip"])
        let epubs = UploadBundler.plan([items[0], items[2]], zipBundle: true, limit: 100 * megabyte)
        XCTAssertEqual(epubs.map(\.zipName), ["NovelDB_2_EPUBs.zip"], "the extension is matched without case")
    }

    func testWithBundlingOffEveryFileGoesUpAlone() throws {
        let items = [try item("a.epub", megabytes: 1), try item("b.epub", megabytes: 1), try item("c.epub", megabytes: 1)]
        let plan = UploadBundler.plan(items, zipBundle: false, limit: 100 * megabyte)
        XCTAssertEqual(plan.map { $0.items.map(\.fileName) }, [["a.epub"], ["b.epub"], ["c.epub"]])
        XCTAssertTrue(plan.allSatisfy { !$0.isZip })
    }

    func testASingleFileIsNeverZipped() throws {
        let plan = UploadBundler.plan([try item("a.epub", megabytes: 1)], zipBundle: true, limit: 100 * megabyte)
        XCTAssertEqual(plan.count, 1)
        XCTAssertNil(plan[0].zipName)
        XCTAssertEqual(UploadBundler.plan([], zipBundle: true, limit: 100), [])
    }

    func testARepeatedPathCountsOnceAndFoldersAreNotPacked() throws {
        let a = try item("a.epub", megabytes: 1)
        let b = try item("b.epub", megabytes: 1)
        let folder = directory.appendingPathComponent("Book TXT", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let plan = UploadBundler.plan([a, a, b, UploadItem(file: folder)], zipBundle: true, limit: 100 * megabyte)
        XCTAssertEqual(plan.count, 2)
        XCTAssertEqual(plan[0].items.map(\.fileName), ["a.epub", "b.epub"])
        XCTAssertEqual(plan[0].zipName, "NovelDB_2_EPUBs.zip")
        XCTAssertEqual(plan[1].items.map(\.fileName), ["Book TXT"])
        XCTAssertNil(plan[1].zipName)
        // A folder next to one file: nothing to pack.
        XCTAssertTrue(UploadBundler.plan([a, UploadItem(file: folder)], zipBundle: true, limit: 100 * megabyte).allSatisfy { !$0.isZip })
    }

    func testHostLimitSizesTheZipsOfARealPlan() throws {
        let items = [try item("a.epub", megabytes: 60), try item("b.epub", megabytes: 60)]
        var settings = UploadSettings()
        settings.hosts = ["tempsh"]
        var plan = UploaderCatalog.planBatch(items, settings: settings, context: context(settings: settings))
        XCTAssertEqual(plan.map(\.zipName), ["NovelDB_2_EPUBs.zip"], "1 GB fits both")

        settings.hosts = ["tmpfiles"]
        plan = UploaderCatalog.planBatch(items, settings: settings, context: context(settings: settings))
        XCTAssertEqual(plan.count, 2, "tmpfiles takes 100 MB, so 120 MB cannot be one ZIP")
        XCTAssertTrue(plan.allSatisfy { !$0.isZip })

        settings.hosts = ["tempsh"]
        settings.zipMaxMB = 100
        plan = UploaderCatalog.planBatch(items, settings: settings, context: context(settings: settings))
        XCTAssertEqual(plan.count, 2, "the setting can be lower than the host")
    }

    // MARK: - The ZIP

    func testEntryNamesKeepTheOriginalNamesAndNeverRepeat() throws {
        let one = directory.appendingPathComponent("one", isDirectory: true)
        let two = directory.appendingPathComponent("two", isDirectory: true)
        let items = [
            UploadItem(file: try sparse("소설.epub", megabytes: 0, in: one)),
            UploadItem(file: try sparse("소설.epub", megabytes: 0, in: two)),
            UploadItem(file: try sparse("notes", megabytes: 0, in: one)),
            UploadItem(file: try sparse("Notes", megabytes: 0, in: two)),
        ]
        XCTAssertEqual(UploadBundler.entryNames(for: items), ["소설.epub", "소설 (2).epub", "notes", "Notes (2)"])
    }

    func testStoredAndDeflatedEntriesByType() {
        for name in ["a.epub", "a.EPUB", "a.cbz", "a.pdf", "a.zip"] {
            XCTAssertTrue(UploadBundler.isStored(name), name)
        }
        for name in ["a.txt", "a.html", "a.json", "noextension"] {
            XCTAssertFalse(UploadBundler.isStored(name), name)
        }
    }

    func testARealZipIsWrittenAndReadBack() throws {
        let epubOne = try makeFile("소설 제목.epub", bytes: 5_000)
        let epubTwo = try makeFile("Second Book.epub", bytes: 7_000)
        let text = try makeFile("노트 notes.txt", bytes: 0)
        let page = try makeFile("page.html", bytes: 0)
        let repeated = Data(String(repeating: "한 줄의 반복되는 글 line of repeated text\n", count: 600).utf8)
        try repeated.write(to: text)
        try repeated.write(to: page)
        let items = [epubOne, epubTwo, text, page].map { UploadItem(file: $0, title: $0.deletingPathExtension().lastPathComponent) }
        let bundle = UploadBundle(items: items, zipName: "NovelDB_4_files.zip")

        let zip = try UploadBundler.writeZip(bundle, in: directory)
        defer { try? FileManager.default.removeItem(at: zip.deletingLastPathComponent()) }
        XCTAssertEqual(zip.lastPathComponent, "NovelDB_4_files.zip")
        XCTAssertTrue(zip.deletingLastPathComponent().path.hasPrefix(directory.path), "built in the temporary folder")

        // Every entry holds the file's bytes under the file's own name, no folders.
        let archive = try Archive(url: zip, accessMode: .read, pathEncoding: nil)
        var found: [String: Data] = [:]
        for entry in archive {
            var data = Data()
            _ = try archive.extract(entry) { data.append($0) }
            found[entry.path] = data
        }
        XCTAssertEqual(Set(found.keys), ["소설 제목.epub", "Second Book.epub", "노트 notes.txt", "page.html"])
        for url in [epubOne, epubTwo, text, page] {
            XCTAssertEqual(found[uploadName(url)], try Data(contentsOf: url), url.lastPathComponent)
        }
        XCTAssertFalse(found.keys.contains { $0.contains("/") })

        // The central directory says UTF-8 names, stored EPUBs, deflated text and HTML.
        let records = try ZipInspector.records(of: zip)
        XCTAssertEqual(records.map(\.name), ["소설 제목.epub", "Second Book.epub", "노트 notes.txt", "page.html"])
        XCTAssertTrue(records.allSatisfy { $0.flags & 0x0800 != 0 }, "the UTF-8 name flag is set")
        for record in records where record.name.hasSuffix(".epub") {
            XCTAssertEqual(record.method, 0, "\(record.name) is stored")
            XCTAssertEqual(record.compressedSize, record.size)
        }
        for record in records where record.name.hasSuffix(".txt") || record.name.hasSuffix(".html") {
            XCTAssertEqual(record.method, 8, "\(record.name) is deflated")
            XCTAssertLessThan(record.compressedSize, record.size)
        }
    }

    func testAZipWithoutAnAssignedNameIsRefused() throws {
        let bundle = UploadBundle(items: [UploadItem(file: try makeFile("a.epub"))], zipName: nil)
        XCTAssertThrowsError(try UploadBundler.writeZip(bundle, in: directory))
    }

    func testAMissingFileLeavesNoStagingFolderBehind() throws {
        let gone = directory.appendingPathComponent("gone.epub")
        let bundle = UploadBundle(items: [UploadItem(file: try makeFile("a.epub")), UploadItem(file: gone)], zipName: "NovelDB_2_EPUBs.zip")
        XCTAssertThrowsError(try UploadBundler.writeZip(bundle, in: directory))
        let leftovers = try FileManager.default.contentsOfDirectory(atPath: directory.path).filter { $0.hasPrefix("npia-zip-") }
        XCTAssertEqual(leftovers, [])
    }
}

/// Reads a ZIP's central directory without ZIPFoundation, so the tests check the bytes.
enum ZipInspector {
    struct Record {
        var name: String
        var flags: UInt16
        var method: UInt16
        var compressedSize: UInt32
        var size: UInt32
    }

    enum Failure: Error { case noEndRecord, badRecord }

    static func records(of url: URL) throws -> [Record] {
        let data = try Data(contentsOf: url)
        func u16(_ offset: Int) -> UInt16 {
            let low = UInt16(data[offset])
            let high = UInt16(data[offset + 1])
            return low | (high << 8)
        }
        func u32(_ offset: Int) -> UInt32 {
            let low = UInt32(u16(offset))
            let high = UInt32(u16(offset + 2))
            return low | (high << 16)
        }
        var end = data.count - 22
        while end >= 0, u32(end) != 0x0605_4b50 { end -= 1 }
        guard end >= 0 else { throw Failure.noEndRecord }
        let count = Int(u16(end + 10))
        var offset = Int(u32(end + 16))
        var records: [Record] = []
        for _ in 0..<count {
            guard u32(offset) == 0x0201_4b50 else { throw Failure.badRecord }
            let nameLength = Int(u16(offset + 28))
            let extraLength = Int(u16(offset + 30))
            let commentLength = Int(u16(offset + 32))
            let nameBytes = data[(offset + 46)..<(offset + 46 + nameLength)]
            records.append(Record(
                name: String(decoding: nameBytes, as: UTF8.self),
                flags: u16(offset + 8), method: u16(offset + 10),
                compressedSize: u32(offset + 20), size: u32(offset + 24)
            ))
            offset += 46 + nameLength + extraLength + commentLength
        }
        return records
    }
}
