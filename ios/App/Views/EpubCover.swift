import Compression
import UIKit

/// An EPUB's cover image for the Library shelf: the ZIP's directory, its package file and the image it
/// names as the cover (as the Android Library's `epubCover`), decoded small. Nil when it has none.
enum EpubCover {
    private static let cache = NSCache<NSURL, UIImage>()

    static func image(for epub: URL, maxSide: CGFloat = 360) -> UIImage? {
        if let kept = cache.object(forKey: epub as NSURL) { return kept }
        guard let data = try? Data(contentsOf: epub, options: .mappedIfSafe), let zip = Zip(data),
              let container = zip.text("META-INF/container.xml"),
              let opfPath = first(#"full-path="([^"]+)""#, in: container),
              let opf = zip.text(opfPath) else { return nil }
        let base = opfPath.contains("/") ? String(opfPath[..<opfPath.lastIndex(of: "/")!]) : ""
        let items = matches(#"<item\b[^>]*>"#, in: opf)
        func attribute(_ tag: String, _ name: String) -> String? { first("\\b\(name)=\"([^\"]+)\"", in: tag) }
        let coverID = first(#"<meta[^>]+name="cover"[^>]+content="([^"]+)""#, in: opf)
        guard let item = items.first(where: { coverID != nil && attribute($0, "id") == coverID })
                ?? items.first(where: { attribute($0, "properties")?.contains("cover-image") == true })
                ?? items.first(where: { attribute($0, "media-type")?.hasPrefix("image/") == true && attribute($0, "href")?.localizedCaseInsensitiveContains("cover") == true }),
              let href = attribute(item, "href")?.removingPercentEncoding,
              let bytes = zip.data(base.isEmpty ? href : base + "/" + href),
              let image = UIImage(data: bytes) else { return nil }
        let scale = min(1, maxSide / max(image.size.width, image.size.height))
        let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
        let small = UIGraphicsImageRenderer(size: size).image { _ in image.draw(in: CGRect(origin: .zero, size: size)) }
        cache.setObject(small, forKey: epub as NSURL)
        return small
    }

    private static func matches(_ pattern: String, in text: String) -> [String] {
        guard let expression = try? NSRegularExpression(pattern: pattern) else { return [] }
        return expression.matches(in: text, range: NSRange(text.startIndex..., in: text)).compactMap { Range($0.range, in: text).map { String(text[$0]) } }
    }

    private static func first(_ pattern: String, in text: String) -> String? {
        guard let expression = try? NSRegularExpression(pattern: pattern),
              let match = expression.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)),
              let range = Range(match.range(at: 1), in: text) else { return nil }
        return String(text[range])
    }

    /// The files of a ZIP, read from its central directory; stored or deflated entries.
    private struct Zip {
        let data: Data
        var entries: [String: (method: UInt16, size: Int, original: Int, local: Int)] = [:]

        init?(_ data: Data) {
            self.data = data
            let bytes = [UInt8](data)
            var end = -1
            var i = bytes.count - 22
            while i >= max(0, bytes.count - 65_557) {
                if Self.u32(bytes, i) == 0x0605_4B50 { end = i; break }
                i -= 1
            }
            guard end >= 0 else { return nil }
            var at = Int(Self.u32(bytes, end + 16))
            for _ in 0..<Int(Self.u16(bytes, end + 10)) {
                guard at + 46 <= bytes.count, Self.u32(bytes, at) == 0x0201_4B50 else { break }
                let nameLength = Int(Self.u16(bytes, at + 28)), extra = Int(Self.u16(bytes, at + 30)), comment = Int(Self.u16(bytes, at + 32))
                let name = String(decoding: bytes[(at + 46)..<(at + 46 + nameLength)], as: UTF8.self)
                entries[name] = (Self.u16(bytes, at + 10), Int(Self.u32(bytes, at + 20)), Int(Self.u32(bytes, at + 24)), Int(Self.u32(bytes, at + 42)))
                at += 46 + nameLength + extra + comment
            }
        }

        func data(_ name: String) -> Data? {
            guard let entry = entries[name] else { return nil }
            let bytes = [UInt8](data[entry.local..<min(data.count, entry.local + 30)])
            guard bytes.count == 30 else { return nil }
            let start = entry.local + 30 + Int(Self.u16(bytes, 26)) + Int(Self.u16(bytes, 28))
            guard start + entry.size <= data.count else { return nil }
            let packed = data[start..<(start + entry.size)]
            if entry.method == 0 { return Data(packed) }
            guard entry.method == 8, entry.original > 0 else { return nil }
            var out = Data(count: entry.original)
            let written = out.withUnsafeMutableBytes { target in
                packed.withUnsafeBytes { source in
                    // Apple's ZLIB is raw DEFLATE (RFC 1951), as ZIP entries are.
                    compression_decode_buffer(target.bindMemory(to: UInt8.self).baseAddress!, entry.original,
                                              source.bindMemory(to: UInt8.self).baseAddress!, entry.size, nil, COMPRESSION_ZLIB)
                }
            }
            return written == entry.original ? out : nil
        }

        func text(_ name: String) -> String? { data(name).map { String(decoding: $0, as: UTF8.self) } }

        private static func u16(_ b: [UInt8], _ i: Int) -> UInt16 { UInt16(b[i]) | UInt16(b[i + 1]) << 8 }
        private static func u32(_ b: [UInt8], _ i: Int) -> UInt32 {
            UInt32(b[i]) | UInt32(b[i + 1]) << 8 | UInt32(b[i + 2]) << 16 | UInt32(b[i + 3]) << 24
        }
    }
}
