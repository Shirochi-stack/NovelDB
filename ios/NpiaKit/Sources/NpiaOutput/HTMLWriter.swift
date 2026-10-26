import Foundation
import NpiaCore

/// One self-contained `.html` file: cover, info, a linked table of contents, then every
/// chapter. Local images are embedded as base64 `data:` URIs.
public struct HTMLWriter: BookWriter {
    public let format = OutputFormat.html
    public init() {}

    public func write(_ bundle: BookBundle, baseName: String, to directory: URL) async throws -> URL {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent(baseName + ".html")
        try Data(Self.render(bundle).utf8).write(to: url, options: .atomic)
        return url
    }

    static func dataURI(_ data: Data, filename: String) -> String {
        "data:\(OutputSupport.mimeType(forFilename: filename));base64,\(data.base64EncodedString())"
    }

    static func render(_ bundle: BookBundle) -> String {
        let book = bundle.book
        let numbered = bundle.settings.effective(for: book.source).numberChapters
        let esc = OutputSupport.escape
        let chapters = OutputSupport.readingOrder(bundle.chapters)
        let localNames = Set(bundle.chapters.flatMap { $0.images.map(\.filename) })

        var html = """
        <!DOCTYPE html>
        <html lang="\(esc(book.language))">
        <head>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <title>\(esc(book.title))</title>
        <style>
        \(OutputSupport.css)
        body { max-width: 48em; margin: 0 auto; padding: 1em; line-height: 1.7; }
        .chapter { page-break-before: always; }
        </style>
        </head>
        <body>

        """

        if let cover = bundle.cover {
            html += #"<div class="cover" style="text-align:center"><img alt="Cover" src="\#(dataURI(cover.data, filename: "cover." + cover.fileExtension))"/></div>"# + "\n"
        } else if let coverURL = book.coverURL {
            html += #"<div class="cover" style="text-align:center"><img alt="Cover" src="\#(esc(coverURL.absoluteString))"/></div>"# + "\n"
        }

        html += "<header class=\"info\"><h1>\(esc(book.title))</h1>\n"
        if let author = book.author { html += "<p><b>Author:</b> \(esc(author))</p>\n" }
        if let status = book.status, !status.isEmpty { html += "<p><b>Status:</b> \(esc(status))</p>\n" }
        if !book.tags.isEmpty { html += "<p><b>Tags:</b> \(esc(book.tags.joined(separator: ", ")))</p>\n" }
        if let synopsis = book.synopsis, !synopsis.isEmpty {
            html += "<p class=\"synopsis\">\(synopsis.components(separatedBy: "\n").map(esc).joined(separator: "<br/>"))</p>\n"
        }
        html += "</header>\n"

        let anchors = chapters.enumerated().map { offset, chapter in
            chapter.ref.isNotice ? "notice-\(offset + 1)" : "chapter-\(chapter.ref.index)-\(offset + 1)"
        }
        html += "<nav class=\"toc\"><h2>Contents</h2><ol>\n"
        for (offset, chapter) in chapters.enumerated() {
            html += "<li><a href=\"#\(anchors[offset])\">\(esc(OutputSupport.displayTitle(chapter.ref, numbered: numbered)))</a></li>\n"
        }
        html += "</ol></nav>\n"

        for (offset, chapter) in chapters.enumerated() {
            let title = OutputSupport.displayTitle(chapter.ref, numbered: numbered)
            let body = OutputSupport.rewriteImages(chapter.html, localNames: localNames, available: bundle.images) { name in
                dataURI(bundle.images[name]!, filename: name)
            }
            html += "<section class=\"chapter\" id=\"\(anchors[offset])\">\n<h2>\(esc(title))</h2>\n\(body)\n</section>\n"
        }
        html += "</body>\n</html>\n"
        return html
    }
}
