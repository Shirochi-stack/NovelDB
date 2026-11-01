import Foundation
import NpiaCore

/// Helpers shared by the EPUB and HTML writers.
enum OutputSupport {
    /// Stylesheet the desktop npia downloader embeds in every EPUB.
    static let css = """
    div.svg_outer { display: block; margin: 0; padding: 0; text-align: left; }
    div.svg_inner { display: block; text-align: center; }
    h1, h2 { text-align: center; margin-bottom: 10%; margin-top: 10%; }
    h3, h4, h5, h6 { text-align: center; margin-bottom: 15%; margin-top: 10%; }
    ol, ul { padding-left: 8%; }
    body { margin: 2%; }
    p { overflow-wrap: break-word; }
    dd, dt, dl { padding: 0; margin: 0; }
    img { display: block; min-height: 1em; max-height: 100%; max-width: 100%; padding: 0; margin: 2% auto; }
    img.inline { display: inline; min-height: 1em; margin-bottom: 0; margin-top: 0; }
    .thumbcaption { display: block; font-size: 0.9em; padding-right: 5%; padding-left: 5%; }
    hr { color: black; background-color: black; height: 2px; }
    a:link { text-decoration: none; color: #0B0080; }
    a:visited { text-decoration: none; }
    a:hover, a:active { text-decoration: underline; }
    table { width: 90%; border-collapse: collapse; }
    table, th, td { border: 1px solid black; }
    """

    static func escape(_ text: String) -> String {
        var out = ""
        out.reserveCapacity(text.count)
        for ch in text {
            switch ch {
            case "&": out += "&amp;"
            case "<": out += "&lt;"
            case ">": out += "&gt;"
            case "\"": out += "&quot;"
            case "'": out += "&#39;"
            default: out.append(ch)
            }
        }
        return out
    }

    /// Heading text: "N. title" with `numberChapters`, never for notices (matches `_numbered_title`).
    static func displayTitle(_ ref: ChapterRef, numbered: Bool) -> String {
        if ref.isNotice { return ref.title }
        if ref.title.isEmpty { return "Chapter \(ref.index)" }
        return numbered ? "\(ref.index). \(ref.title)" : ref.title
    }

    /// Notices first, otherwise the order the pipeline gave.
    static func readingOrder(_ chapters: [ChapterContent]) -> [ChapterContent] {
        chapters.filter(\.ref.isNotice) + chapters.filter { !$0.ref.isNotice }
    }

    static func mimeType(forFilename name: String) -> String {
        switch (name as NSString).pathExtension.lowercased() {
        case "jpg", "jpeg": return "image/jpeg"
        case "png": return "image/png"
        case "gif": return "image/gif"
        case "webp": return "image/webp"
        case "avif": return "image/avif"
        case "svg": return "image/svg+xml"
        default: return "application/octet-stream"
        }
    }

    /// Rewrites `<img src>` values that name local images. Images the bundle lacks (failed
    /// downloads) are dropped so the output never points at a missing file; remote URLs stay.
    static func rewriteImages(_ html: String, localNames: Set<String>, available: [String: Data], replacement: (String) -> String) -> String {
        let regex = try! NSRegularExpression(pattern: #"<img\b[^>]*?\bsrc=(["'])(.*?)\1[^>]*>"#, options: [.caseInsensitive, .dotMatchesLineSeparators])
        let ns = html as NSString
        var result = ""
        var cursor = 0
        for match in regex.matches(in: html, range: NSRange(location: 0, length: ns.length)) {
            result += ns.substring(with: NSRange(location: cursor, length: match.range.location - cursor))
            let tag = ns.substring(with: match.range)
            let srcRange = match.range(at: 2)
            let src = ns.substring(with: srcRange)
            if available[src] != nil {
                let before = ns.substring(with: NSRange(location: match.range.location, length: srcRange.location - match.range.location))
                let after = ns.substring(with: NSRange(location: srcRange.location + srcRange.length, length: match.range.location + match.range.length - srcRange.location - srcRange.length))
                result += before + replacement(src) + after
            } else if localNames.contains(src) || !(src.contains(":") || src.hasPrefix("//")) {
                // A local image that was never downloaded.
            } else {
                result += tag
            }
            cursor = match.range.location + match.range.length
        }
        result += ns.substring(from: cursor)
        return result
    }

    private static let xmlEntities: Set<String> = ["amp", "lt", "gt", "quot", "apos"]
    private static let namedEntities: [String: Int] = [
        "nbsp": 160, "middot": 183, "hellip": 8230, "lsquo": 8216, "rsquo": 8217, "ldquo": 8220,
        "rdquo": 8221, "ndash": 8211, "mdash": 8212, "copy": 169, "reg": 174, "trade": 8482,
        "laquo": 171, "raquo": 187, "times": 215, "deg": 176, "bull": 8226,
    ]

    /// Makes an HTML body fragment well-formed enough for XHTML: HTML-only entities become
    /// numeric, stray `&` is escaped, and void elements are self-closed.
    static func xhtmlFragment(_ html: String) -> String {
        let entity = try! NSRegularExpression(pattern: #"&(#[0-9]+;|#[xX][0-9a-fA-F]+;|([A-Za-z][A-Za-z0-9]*);)?"#)
        let ns = html as NSString
        var out = ""
        var cursor = 0
        for match in entity.matches(in: html, range: NSRange(location: 0, length: ns.length)) {
            out += ns.substring(with: NSRange(location: cursor, length: match.range.location - cursor))
            let whole = ns.substring(with: match.range)
            if match.range(at: 1).location == NSNotFound {
                out += "&amp;"
            } else if match.range(at: 2).location != NSNotFound {
                let name = ns.substring(with: match.range(at: 2))
                if xmlEntities.contains(name) {
                    out += whole
                } else if let code = namedEntities[name] {
                    out += "&#\(code);"
                } else {
                    out += "&amp;" + name + ";"
                }
            } else {
                out += whole
            }
            cursor = match.range.location + match.range.length
        }
        out += ns.substring(from: cursor)
        let void = try! NSRegularExpression(pattern: #"<(br|hr|img|meta|link|input|col|area|source|wbr)\b((?:[^>"']|"[^"]*"|'[^']*')*?)\s*/?>"#, options: .caseInsensitive)
        return void.stringByReplacingMatches(in: out, range: NSRange(location: 0, length: (out as NSString).length), withTemplate: "<$1$2/>")
    }
}
