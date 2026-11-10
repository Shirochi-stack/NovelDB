import Foundation
import Darwin
import ZIPFoundation

public enum LibraryEpub {
    @discardableResult public static func replace(_ candidate: URL, target: URL) throws -> Int {
        let count = try chapters(candidate)
        guard count > 0, count >= (try chapters(target)) else {
            throw CocoaError(.fileReadCorruptFile, userInfo: [NSLocalizedDescriptionKey: "The refreshed EPUB has fewer chapters than the saved book"])
        }
        guard rename(candidate.path, target.path) == 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        return count
    }

    private static func chapters(_ file: URL) throws -> Int {
        let zip = try Archive(url: file, accessMode: .read)
        func text(_ path: String) throws -> String {
            guard let entry = zip[path] else { throw CocoaError(.fileReadCorruptFile) }
            var data = Data()
            _ = try zip.extract(entry) { data.append($0) }
            return String(decoding: data, as: UTF8.self)
        }
        let container = try text("META-INF/container.xml")
        let expression = try NSRegularExpression(pattern: "full-path=[\"']([^\"']+)")
        guard let match = expression.firstMatch(in: container, range: NSRange(container.startIndex..., in: container)),
              let range = Range(match.range(at: 1), in: container) else { throw CocoaError(.fileReadCorruptFile) }
        let opf = try text(String(container[range]))
        return try NSRegularExpression(pattern: "<(?:\\w+:)?itemref\\b").numberOfMatches(in: opf, range: NSRange(opf.startIndex..., in: opf))
    }
}
