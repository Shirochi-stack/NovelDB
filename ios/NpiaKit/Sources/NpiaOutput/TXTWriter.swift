import Foundation
import NpiaCore

/// Plain-text output. With `separateTXT`, writes one file per chapter into a folder.
public struct TXTWriter: BookWriter {
    public let format = OutputFormat.txt
    public init() {}

    public func write(_ bundle: BookBundle, baseName: String, to directory: URL) async throws -> URL {
        let shared = bundle.settings.effective(for: bundle.book.source)
        let text = bundle.settings.text
        let fm = FileManager.default
        try fm.createDirectory(at: directory, withIntermediateDirectories: true)

        func render(_ chapter: ChapterContent) -> String {
            let title = shared.numberChapters && !chapter.ref.isNotice ? "\(chapter.ref.index). \(chapter.ref.title)" : chapter.ref.title
            var body = HTMLText.plainText(chapter.html)
            if text.stripLeadingSpaces {
                body = body.split(separator: "\n", omittingEmptySubsequences: false)
                    .map { $0.drop(while: { $0 == " " || $0 == "\u{3000}" || $0 == "\t" }) }
                    .joined(separator: "\n")
            }
            if text.removeNewlines {
                body = body.split(separator: "\n").filter { !$0.allSatisfy(\.isWhitespace) }.joined(separator: "\n")
            } else if text.blankParagraphLines {
                body = body.split(separator: "\n").filter { !$0.allSatisfy(\.isWhitespace) }.joined(separator: "\n\n")
            }
            return title + "\n\n" + body + "\n"
        }

        if shared.separateTXT {
            let folder = directory.appendingPathComponent(baseName, isDirectory: true)
            try fm.createDirectory(at: folder, withIntermediateDirectories: true)
            for chapter in bundle.chapters {
                let name = String(format: "%04d", chapter.ref.index) + ".txt"
                try Data(render(chapter).utf8).write(to: folder.appendingPathComponent(name), options: .atomic)
            }
            return folder
        }

        var header = bundle.book.title + "\n"
        if let author = bundle.book.author { header += author + "\n" }
        if let synopsis = bundle.book.synopsis { header += "\n" + synopsis + "\n" }
        let joined = ([header] + bundle.chapters.map(render)).joined(separator: "\n\n")
        let url = directory.appendingPathComponent(baseName + ".txt")
        try Data(joined.utf8).write(to: url, options: .atomic)
        return url
    }
}

public enum HTMLText {
    /// Rough HTML-to-text for chapter bodies: block tags become newlines, tags are dropped,
    /// and common entities are decoded.
    public static func plainText(_ html: String) -> String {
        var text = html
        let blockBreaks = #"(?i)<\s*(br|/p|/div|/h[1-6]|/li|/blockquote)\b[^>]*>"#
        text = text.replacingOccurrences(of: blockBreaks, with: "\n", options: .regularExpression)
        text = text.replacingOccurrences(of: #"(?is)<(script|style)\b.*?</\1>"#, with: "", options: .regularExpression)
        text = text.replacingOccurrences(of: #"<[^>]+>"#, with: "", options: .regularExpression)
        let entities = ["&nbsp;": " ", "&lt;": "<", "&gt;": ">", "&quot;": "\"", "&#39;": "'", "&apos;": "'", "&amp;": "&"]
        for (entity, value) in entities {
            text = text.replacingOccurrences(of: entity, with: value)
        }
        text = text.replacingOccurrences(of: "\r\n", with: "\n")
        text = text.replacingOccurrences(of: #"\n{3,}"#, with: "\n\n", options: .regularExpression)
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
