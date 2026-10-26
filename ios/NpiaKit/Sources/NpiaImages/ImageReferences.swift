import Foundation

/// Rewrites image references in chapter HTML after recompression changes a file name.
public enum ImageReferences {
    /// `name.png` -> `name.webp`; names without an extension get one appended.
    public static func renamed(_ filename: String, toExtension ext: String) -> String {
        let slash = filename.lastIndex(of: "/").map { filename.index(after: $0) } ?? filename.startIndex
        let leaf = filename[slash...]
        if let dot = leaf.lastIndex(of: "."), dot != leaf.startIndex {
            return String(filename[..<dot]) + "." + ext
        }
        return filename + "." + ext
    }

    private static let attribute = try! NSRegularExpression(
        pattern: #"((?:src|href|xlink:href)\s*=\s*)(["'])(.*?)\2"#,
        options: [.caseInsensitive, .dotMatchesLineSeparators]
    )

    /// Replaces `src`/`href` values that equal an old name, or end with `/old name`
    /// (e.g. `../images/1.png` when the ref is `images/1.png` or `1.png`).
    public static func rewrite(_ html: String, renames: [String: String]) -> String {
        rewriteValues(html) { value in
            if let exact = renames[value] { return exact }
            for (old, new) in renames where value.hasSuffix("/" + old) {
                return String(value.dropLast(old.count)) + new
            }
            return nil
        }
    }

    /// Calls `transform` for every `src`/`href` value; a non-nil result replaces it.
    public static func rewriteValues(_ html: String, _ transform: (String) -> String?) -> String {
        let ns = html as NSString
        let matches = attribute.matches(in: html, range: NSRange(location: 0, length: ns.length))
        guard !matches.isEmpty else { return html }
        var output = ""
        output.reserveCapacity(ns.length)
        var cursor = 0
        for match in matches {
            let valueRange = match.range(at: 3)
            let value = ns.substring(with: valueRange)
            guard let replacement = transform(value) else { continue }
            output += ns.substring(with: NSRange(location: cursor, length: valueRange.location - cursor))
            output += replacement
            cursor = valueRange.location + valueRange.length
        }
        output += ns.substring(from: cursor)
        return output
    }
}
