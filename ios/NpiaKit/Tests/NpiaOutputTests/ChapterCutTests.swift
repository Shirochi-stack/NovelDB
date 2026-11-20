import XCTest
import NpiaCore
import ZIPFoundation
@testable import NpiaOutput

final class ChapterCutTests: XCTestCase {
    private var root: URL!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: root)
    }

    private func add(_ archive: Archive, _ path: String, _ text: String) throws {
        let data = Data(text.utf8)
        try archive.addEntry(with: path, type: .file, uncompressedSize: Int64(data.count), compressionMethod: .none,
                             provider: { position, size in data.subdata(in: Int(position)..<Int(position) + size) })
    }

    private func names(_ url: URL) throws -> [String] {
        try Archive(url: url, accessMode: .read).map(\.path)
    }

    private func text(_ url: URL, _ path: String) throws -> String {
        let archive = try Archive(url: url, accessMode: .read)
        var data = Data()
        _ = try archive.extract(try XCTUnwrap(archive[path])) { data.append($0) }
        return String(decoding: data, as: UTF8.self)
    }

    /// An EPUB, a CBZ and a chapter folder the way a download saves them.
    private func book(_ name: String, _ chapters: [Int]) throws -> [URL] {
        func pad(_ n: Int) -> String { String(format: "%04d", n) }
        let epub = root.appendingPathComponent(name + ".epub")
        let archive = try Archive(url: epub, accessMode: .create, pathEncoding: nil)
        try add(archive, "mimetype", "application/epub+zip")
        try add(archive, "META-INF/container.xml", #"<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>"#)
        try add(archive, "OEBPS/images/cover.jpg", "cover")
        try add(archive, "OEBPS/Text/info.xhtml", #"<html><body><img src="../images/cover.jpg"/></body></html>"#)
        var items = "", refs = "", points = "", list = ""
        for n in chapters {
            try add(archive, "OEBPS/images/\(pad(n))_01.webp", "page \(n)")
            try add(archive, "OEBPS/Text/chapter\(pad(n)).xhtml", "<html><body><img src=\"../images/\(pad(n))_01.webp\"/></body></html>")
            items += "<item id=\"chapter\(pad(n))\" href=\"Text/chapter\(pad(n)).xhtml\" media-type=\"application/xhtml+xml\"/>"
            items += "<item id=\"i\(n)\" href=\"images/\(pad(n))_01.webp\" media-type=\"image/webp\"/>"
            refs += "<itemref idref=\"chapter\(pad(n))\"/>"
            points += "<navPoint id=\"n\(n)\"><navLabel><text>\(n)</text></navLabel><content src=\"Text/chapter\(pad(n)).xhtml\"/></navPoint>"
            list += "<li><a href=\"Text/chapter\(pad(n)).xhtml\">\(n)</a></li>"
        }
        let fixed = #"<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>"#
            + #"<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>"#
            + #"<item id="cover-image" href="images/cover.jpg" media-type="image/jpeg"/>"#
            + #"<item id="info" href="Text/info.xhtml" media-type="application/xhtml+xml"/>"#
        try add(archive, "OEBPS/content.opf", "<package><manifest>\(fixed)\(items)</manifest><spine><itemref idref=\"info\"/>\(refs)</spine></package>")
        try add(archive, "OEBPS/toc.ncx", "<ncx><navMap>\(points)</navMap></ncx>")
        try add(archive, "OEBPS/nav.xhtml", "<html><body><nav><ol>\(list)</ol></nav></body></html>")

        let cbz = root.appendingPathComponent(name + ".cbz")
        let comic = try Archive(url: cbz, accessMode: .create, pathEncoding: nil)
        try add(comic, "0000_cover.jpg", "cover")
        for n in chapters { try add(comic, "\(pad(n))_Chapter/0001.png", "page \(n)") }

        let folder = root.appendingPathComponent(name + ".folder", isDirectory: true)
        for n in chapters {
            let chapter = folder.appendingPathComponent("\(pad(n)) Chapter \(n)", isDirectory: true)
            try FileManager.default.createDirectory(at: chapter, withIntermediateDirectories: true)
            try Data("page \(n)".utf8).write(to: chapter.appendingPathComponent("001.webp"))
        }
        return [epub, cbz, folder]
    }

    func testCutsTheChaptersOutOfTheSavedFiles() throws {
        let files = try book("Book (1,3-5)", [1, 3, 4, 5])
        let made = files.compactMap { ChapterCut.cut($0, from: 3, to: 4, list: "") }
        XCTAssertEqual(made.map(\.lastPathComponent), ["Book (3-4).epub", "Book (3-4).cbz", "Book (3-4).folder"])

        let entries = try names(made[0])
        XCTAssertEqual(entries.first, "mimetype")
        XCTAssertEqual(entries.filter { $0.hasPrefix("OEBPS/Text/") },
                       ["OEBPS/Text/info.xhtml", "OEBPS/Text/chapter0003.xhtml", "OEBPS/Text/chapter0004.xhtml"])
        XCTAssertEqual(Set(entries.filter { $0.contains("/images/") }),
                       ["OEBPS/images/cover.jpg", "OEBPS/images/0003_01.webp", "OEBPS/images/0004_01.webp"])
        let opf = try text(made[0], "OEBPS/content.opf")
        XCTAssertFalse(opf.contains("chapter0001") || opf.contains("0005_01"))
        XCTAssertTrue(opf.contains("chapter0003") && opf.contains(#"<itemref idref="info"/>"#))
        XCTAssertEqual(try text(made[0], "OEBPS/toc.ncx").components(separatedBy: "<navPoint").count - 1, 2)
        XCTAssertEqual(try text(made[0], "OEBPS/nav.xhtml").components(separatedBy: "<li>").count - 1, 2)

        XCTAssertEqual(try names(made[1]), ["0000_cover.jpg", "0003_Chapter/0001.png", "0004_Chapter/0001.png"])
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: made[2].path).sorted(), ["0003 Chapter 3", "0004 Chapter 4"])
    }

    func testMissingChaptersUndividedFilesAndFilesThatAlreadyAreTheRange() throws {
        let files = try book("Book (1-2)", [1, 2])
        XCTAssertTrue(files.allSatisfy { ChapterCut.cut($0, from: 7, to: 7, list: "") == nil })
        XCTAssertFalse(try FileManager.default.contentsOfDirectory(atPath: root.path).contains { $0.contains("(7)") })
        let pdf = root.appendingPathComponent("Book.pdf")
        try Data("%PDF".utf8).write(to: pdf)
        XCTAssertNil(ChapterCut.cut(pdf, from: 1, to: 1, list: ""))
        XCTAssertEqual(ChapterCut.cut(files[0], from: 1, to: 2, list: "")?.lastPathComponent, "Book (1-2).epub")
        let whole = try book("Other", [1, 2, 4])
        XCTAssertEqual(ChapterCut.cut(whole[0], from: 1, to: 4, list: "1,4")?.lastPathComponent, "Other (1,4).epub")
        XCTAssertEqual(ChapterCut.baseName("Book (4-end)"), "Book")
        XCTAssertEqual(ChapterCut.baseName("Book (Remastered)"), "Book (Remastered)")
    }
}
