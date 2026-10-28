import Foundation

/// Names for uploaded files. Hosts such as temp.sh drop every non-ASCII character from the
/// multipart file name, so a Korean title arrives as ".epub". Those files go up as
/// "<id>.<ext>" instead, with the real name sent alongside for hosts that understand it.
public enum UploadNaming {
    /// Letters, digits, "-" and "_" of `value` (other runs become "-"), at most 60 characters;
    /// nil when nothing usable is left.
    public static func sanitizedID(_ value: String?) -> String? {
        guard let value else { return nil }
        var out = ""
        for scalar in value.unicodeScalars {
            let isASCIIAlphanumeric = scalar.isASCII && CharacterSet.alphanumerics.contains(scalar)
            if isASCIIAlphanumeric || scalar == "_" {
                out.unicodeScalars.append(scalar)
            } else if !out.hasSuffix("-") {
                out.append("-")
            }
        }
        let trimmed = String(out.trimmingCharacters(in: CharacterSet(charactersIn: "-")).prefix(60))
        return trimmed.isEmpty ? nil : trimmed
    }

    /// The id for a job that has no adapter book id: the last numeric path segment of its URL
    /// (the novel number), else a short ASCII slug of the path, else "book".
    public static func bookID(fromURL url: URL) -> String {
        let segments = url.pathComponents.filter { $0 != "/" && !$0.isEmpty }
        if let number = segments.reversed().first(where: { $0.allSatisfy { ("0"..."9").contains($0) } }) {
            return number
        }
        let slug = sanitizedID(String(segments.joined(separator: "-").prefix(40)))
        return slug ?? "book"
    }

    /// The id to name a job's files with: the adapter book id when it is usable, else the id
    /// taken from the job URL. With the source's codename it reads `<source>_<book id>`
    /// (`npia_419368`), the stand-in name for a title that has nothing usable left in ASCII.
    public static func nameID(source: String? = nil, bookID: String?, url: URL) -> String {
        let id = sanitizedID(bookID) ?? Self.bookID(fromURL: url)
        guard let prefix = sanitizedID(source) else { return id }
        return prefix + "_" + id
    }

    /// The fewest ASCII letters and digits a title must keep to name an upload.
    public static let minimumTitleCharacters = 3

    /// The ASCII-safe form of a title (without its extension): characters outside printable
    /// ASCII become spaces, empty brackets and loose punctuation at the ends are dropped, runs of
    /// spaces collapse, and it is cut at 100 characters. nil when fewer than
    /// `minimumTitleCharacters` ASCII letters or digits are left, so a Korean title never turns
    /// into ".epub" or "-_".
    public static func asciiTitle(_ title: String) -> String? {
        var text = ""
        var lastWasSpace = false
        for scalar in title.precomposedStringWithCanonicalMapping.unicodeScalars {
            if (0x20...0x7E).contains(scalar.value) {
                let isSpace = scalar == " "
                if isSpace && lastWasSpace { continue }
                text.unicodeScalars.append(scalar)
                lastWasSpace = isSpace
            } else if !lastWasSpace {
                text.append(" ")
                lastWasSpace = true
            }
        }
        // Brackets that held only removed characters.
        var previous = ""
        while previous != text {
            previous = text
            for pair in ["()", "[]", "{}", "<>"] {
                let open = String(pair.first!), close = String(pair.last!)
                text = text.replacingOccurrences(of: open + " " + close, with: " ")
                text = text.replacingOccurrences(of: open + close, with: " ")
            }
            while text.contains("  ") { text = text.replacingOccurrences(of: "  ", with: " ") }
        }
        let loose = CharacterSet(charactersIn: " -_.,;:~")
        text = text.trimmingCharacters(in: loose)
        if text.count > 100 { text = String(text.prefix(100)).trimmingCharacters(in: loose) }
        let kept = text.unicodeScalars.filter { $0.isASCII && CharacterSet.alphanumerics.contains($0) }.count
        return kept >= minimumTitleCharacters ? text : nil
    }

    /// RFC 5987 `ext-value` percent-encoding for the `filename*` parameter.
    public static func rfc5987(_ value: String) -> String {
        let allowed = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!#$&+-.^_`|~")
        return value.addingPercentEncoding(withAllowedCharacters: allowed) ?? ""
    }
}

/// True for names made only of printable ASCII (space to tilde).
func isPlainPrintableASCII(_ name: String) -> Bool {
    !name.isEmpty && name.unicodeScalars.allSatisfy { (0x20...0x7E).contains($0.value) }
}

/// The name hosts get in `filename="..."`. A title that is plain ASCII is kept as it is; one
/// with other characters in it keeps its ASCII part (`UploadNaming.asciiTitle`); a title with
/// fewer than three ASCII letters or digits left becomes `id` (for example `npia_419368`), else
/// "book". The extension is kept when it is ASCII.
func asciiUploadName(_ file: URL, id: String?) -> String {
    let original = uploadName(file)
    let ext = file.pathExtension.precomposedStringWithCanonicalMapping
    let usableExtension = !ext.isEmpty && ext.unicodeScalars.allSatisfy { $0.isASCII && CharacterSet.alphanumerics.contains($0) }
    let stem = usableExtension || ext.isEmpty
        ? (original as NSString).deletingPathExtension
        : original
    let base: String
    if let title = UploadNaming.asciiTitle(stem) {
        base = title
    } else {
        base = UploadNaming.sanitizedID(id) ?? "book"
    }
    return usableExtension ? "\(base).\(ext)" : base
}

extension UploadContext {
    /// The name hosts get for `file` (see `asciiUploadName`).
    func hostFileName(for file: URL) -> String {
        asciiUploadName(file, id: nameID)
    }

    /// A multipart form whose file part carries the host-safe name and the original name.
    func form(fileField: String, file: URL, fields: [(name: String, value: String)] = []) -> MultipartForm {
        MultipartForm(fileField: fileField, file: file, fileName: hostFileName(for: file), displayName: uploadName(file), fields: fields)
    }
}
