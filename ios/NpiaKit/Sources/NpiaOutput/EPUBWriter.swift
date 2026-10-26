import Foundation
import NpiaCore
import ZIPFoundation

/// EPUB 3 (with an EPUB 2 `toc.ncx` for older readers), laid out like the desktop
/// `epub_generator.py`: `OEBPS/content.opf`, `Styles/style.css`, `Text/cover.xhtml`,
/// an info page, then `Text/chapter_notice0001.xhtml`... and `Text/chapter0001.xhtml`...
/// Images keep their `ImageRef.filename` under `OEBPS/`.
public struct EPUBWriter: BookWriter {
    public let format = OutputFormat.epub
    public init() {}

    struct Page {
        var id: String
        var filename: String
        var title: String
        var xhtml: String
    }

    public func write(_ bundle: BookBundle, baseName: String, to directory: URL) async throws -> URL {
        let fm = FileManager.default
        try fm.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent(baseName + ".epub")
        if fm.fileExists(atPath: url.path) { try fm.removeItem(at: url) }

        let book = bundle.book
        let numbered = bundle.settings.effective(for: book.source).numberChapters
        let identifier = "urn:uuid:" + UUID().uuidString.lowercased()
        let coverName = bundle.cover.map { "images/cover." + $0.fileExtension.lowercased() }

        // Chapters.
        let localNames = Set(bundle.chapters.flatMap { $0.images.map(\.filename) })
        var pages: [Page] = []
        var used = Set<String>()
        var noticeNumber = 0
        for chapter in OutputSupport.readingOrder(bundle.chapters) {
            var filename: String
            if chapter.ref.isNotice {
                noticeNumber += 1
                filename = String(format: "chapter_notice%04d.xhtml", noticeNumber)
            } else {
                var number = max(1, chapter.ref.index)
                filename = String(format: "chapter%04d.xhtml", number)
                while used.contains(filename) {
                    number += 1
                    filename = String(format: "chapter%04d.xhtml", number)
                }
            }
            used.insert(filename)
            let title = OutputSupport.displayTitle(chapter.ref, numbered: numbered)
            let body = OutputSupport.rewriteImages(chapter.html, localNames: localNames, available: bundle.images) { "../" + $0 }
            let xhtml = Self.document(title: title, body: "<h1>\(OutputSupport.escape(title))</h1>" + OutputSupport.xhtmlFragment(body))
            pages.append(Page(id: (filename as NSString).deletingPathExtension, filename: filename, title: title, xhtml: xhtml))
        }

        let archive = try Archive(url: url, accessMode: .create, pathEncoding: nil)
        // The mimetype entry must come first and be stored uncompressed.
        try add(archive, "mimetype", Data("application/epub+zip".utf8), compressed: false)
        try add(archive, "META-INF/container.xml", Data(Self.container.utf8))
        try add(archive, "OEBPS/Styles/style.css", Data(OutputSupport.css.utf8))

        let coverImage = coverName.map { #"<div style="text-align:center"><img alt="Cover" src="../\#($0)"/></div>"# }
            ?? book.coverURL.map { #"<div style="text-align:center"><img alt="Cover" src="\#(OutputSupport.escape($0.absoluteString))"/></div>"# }
            ?? ""
        try add(archive, "OEBPS/Text/cover.xhtml", Data(Self.document(title: "Cover", body: coverImage).utf8))
        try add(archive, "OEBPS/Text/info.xhtml", Data(Self.document(title: book.title, body: Self.infoBody(book)).utf8))
        for page in pages {
            try add(archive, "OEBPS/Text/" + page.filename, Data(page.xhtml.utf8))
        }

        let compressImages = bundle.settings.images.zipCompressImages
        if let cover = bundle.cover, let coverName {
            try add(archive, "OEBPS/" + coverName, cover.data, compressed: compressImages)
        }
        let imageNames = bundle.images.keys.filter { $0 != coverName }.sorted()
        for name in imageNames {
            try add(archive, "OEBPS/" + name, bundle.images[name]!, compressed: compressImages)
        }

        try add(archive, "OEBPS/nav.xhtml", Data(Self.nav(pages, book: book).utf8))
        try add(archive, "OEBPS/toc.ncx", Data(Self.ncx(pages, book: book, identifier: identifier).utf8))
        try add(archive, "OEBPS/content.opf", Data(Self.opf(pages, book: book, identifier: identifier, coverName: coverName, imageNames: imageNames).utf8))
        return url
    }

    private func add(_ archive: Archive, _ path: String, _ data: Data, compressed: Bool = true) throws {
        try archive.addEntry(
            with: path, type: .file, uncompressedSize: Int64(data.count),
            compressionMethod: compressed ? .deflate : .none,
            provider: { position, size in data.subdata(in: Int(position)..<Int(position) + size) }
        )
    }

    // MARK: - Documents

    static let container = """
    <?xml version="1.0" encoding="UTF-8"?>
    <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
        <rootfiles>
            <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
        </rootfiles>
    </container>
    """

    static func document(title: String, body: String) -> String {
        """
        <?xml version="1.0" encoding="utf-8"?>
        <!DOCTYPE html>
        <html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
        <head><title>\(OutputSupport.escape(title))</title><link href="../Styles/style.css" type="text/css" rel="stylesheet"/></head>
        <body>\(body)</body></html>
        """
    }

    static func infoBody(_ book: BookInfo) -> String {
        var html = "<h1>\(OutputSupport.escape(book.title))</h1>"
        if let author = book.author { html += "<p><b>Author:</b> \(OutputSupport.escape(author))</p>" }
        if let status = book.status, !status.isEmpty { html += "<p><b>Status:</b> \(OutputSupport.escape(status))</p>" }
        if !book.tags.isEmpty { html += "<p><b>Tags:</b> \(OutputSupport.escape(book.tags.joined(separator: ", ")))</p>" }
        html += "<p><b>Source:</b> \(OutputSupport.escape(book.url.absoluteString))</p>"
        if let synopsis = book.synopsis, !synopsis.isEmpty {
            let lines = synopsis.components(separatedBy: "\n").map(OutputSupport.escape).joined(separator: "<br/>")
            html += "<hr/><p>\(lines)</p>"
        }
        return html
    }

    static func nav(_ pages: [Page], book: BookInfo) -> String {
        let items = pages.map { #"<li><a href="Text/\#($0.filename)">\#(OutputSupport.escape($0.title))</a></li>"# }
        return """
        <?xml version="1.0" encoding="utf-8"?>
        <!DOCTYPE html>
        <html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
        <head><title>\(OutputSupport.escape(book.title))</title></head>
        <body>
        <nav epub:type="toc" id="toc"><h1>Contents</h1><ol>
        \(items.joined(separator: "\n"))
        </ol></nav>
        <nav epub:type="landmarks" hidden="hidden"><ol>
        <li><a epub:type="cover" href="Text/cover.xhtml">Cover</a></li>
        <li><a epub:type="toc" href="#toc">Contents</a></li>
        </ol></nav>
        </body></html>
        """
    }

    static func ncx(_ pages: [Page], book: BookInfo, identifier: String) -> String {
        let points = pages.enumerated().map { offset, page in
            """
                <navPoint id="navPoint-\(offset + 1)" playOrder="\(offset + 1)">
                    <navLabel><text>\(OutputSupport.escape(page.title))</text></navLabel>
                    <content src="Text/\(page.filename)"/>
                </navPoint>
            """
        }
        return """
        <?xml version="1.0" encoding="UTF-8"?>
        <ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
        <head>
            <meta name="dtb:uid" content="\(identifier)"/>
            <meta name="dtb:depth" content="1"/>
            <meta name="dtb:totalPageCount" content="0"/>
            <meta name="dtb:maxPageNumber" content="0"/>
        </head>
        <docTitle><text>\(OutputSupport.escape(book.title))</text></docTitle>
        <navMap>
        \(points.joined(separator: "\n"))
        </navMap>
        </ncx>
        """
    }

    static func opf(_ pages: [Page], book: BookInfo, identifier: String, coverName: String?, imageNames: [String]) -> String {
        var manifest = [
            #"<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>"#,
            #"<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>"#,
            #"<item id="css" href="Styles/style.css" media-type="text/css"/>"#,
            #"<item id="coverpage" href="Text/cover.xhtml" media-type="application/xhtml+xml"/>"#,
            #"<item id="info" href="Text/info.xhtml" media-type="application/xhtml+xml"/>"#,
        ]
        var spine = [#"<itemref idref="coverpage"/>"#, #"<itemref idref="info"/>"#]
        for page in pages {
            manifest.append(#"<item id="\#(page.id)" href="Text/\#(page.filename)" media-type="application/xhtml+xml"/>"#)
            spine.append(#"<itemref idref="\#(page.id)"/>"#)
        }
        if let coverName {
            manifest.append(#"<item id="cover-image" href="\#(OutputSupport.escape(coverName))" media-type="\#(OutputSupport.mimeType(forFilename: coverName))" properties="cover-image"/>"#)
        }
        for (offset, name) in imageNames.enumerated() {
            manifest.append(#"<item id="img_\#(offset + 1)" href="\#(OutputSupport.escape(name))" media-type="\#(OutputSupport.mimeType(forFilename: name))"/>"#)
        }

        var metadata = [
            "<dc:identifier id=\"BookId\">\(identifier)</dc:identifier>",
            "<dc:title>\(OutputSupport.escape(book.title))</dc:title>",
            "<dc:language>\(OutputSupport.escape(book.language))</dc:language>",
        ]
        if let author = book.author { metadata.append("<dc:creator>\(OutputSupport.escape(author))</dc:creator>") }
        if let synopsis = book.synopsis, !synopsis.isEmpty { metadata.append("<dc:description>\(OutputSupport.escape(synopsis))</dc:description>") }
        for tag in book.tags { metadata.append("<dc:subject>\(OutputSupport.escape(tag))</dc:subject>") }
        metadata.append("<dc:source>\(OutputSupport.escape(book.url.absoluteString))</dc:source>")
        let modified = ISO8601DateFormatter().string(from: Date())
        metadata.append(#"<meta property="dcterms:modified">\#(modified)</meta>"#)
        if coverName != nil { metadata.append(#"<meta name="cover" content="cover-image"/>"#) }

        return """
        <?xml version="1.0" encoding="utf-8"?>
        <package version="3.0" unique-identifier="BookId" xmlns="http://www.idpf.org/2007/opf">
        <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
            \(metadata.joined(separator: "\n    "))
        </metadata>
        <manifest>
            \(manifest.joined(separator: "\n    "))
        </manifest>
        <spine toc="ncx">
            \(spine.joined(separator: "\n    "))
        </spine>
        <guide>
            <reference type="cover" title="Cover" href="Text/cover.xhtml"/>
        </guide>
        </package>
        """
    }
}
