import Foundation
import NpiaCore

/// The network of the Catalog's generic search page (`window.NovelDBTransport`): the same transport
/// the engines' page scripts use, so requests carry the web view's cookies and `{op: "window"}` runs
/// a script in a hidden page of the address (a site's bot check, HaruNeko's window scripts).
/// `{op: "book"}` and `{op: "chapter"}` read a book with the built-in scraper of its site (`BuiltInReader`).
/// `{op: "file", url: "file:///..."}` hands over a Library EPUB (only one in `libraryFolder`) for the reader.
public enum CatalogTransport {
    /// The app's download folder: the one place the reader may open files from.
    nonisolated(unsafe) public static var libraryFolder: URL?

    static func libraryFile(_ address: String) -> String {
        func failure(_ message: String) -> String {
            let data = (try? JSONSerialization.data(withJSONObject: ["error": message])) ?? Data()
            return String(decoding: data, as: UTF8.self)
        }
        guard let folder = libraryFolder?.standardizedFileURL.resolvingSymlinksInPath() else { return failure("no Library folder") }
        guard let url = URL(string: address), url.isFileURL else { return failure("not a file address") }
        let file = url.standardizedFileURL.resolvingSymlinksInPath()
        guard file.pathExtension.lowercased() == "epub", file.path.hasPrefix(folder.path + "/") else {
            return failure("only EPUBs in the Library can be opened")
        }
        // The whole book goes to the page at once (as base64): a huge one would not fit.
        let size = (try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
        guard size <= 80 * 1024 * 1024 else { return failure("this EPUB is too big to read here (over 80 MB)") }
        guard let bytes = try? Data(contentsOf: file) else { return failure("the book could not be opened") }
        return "{\"status\":200,\"responseBase64\":\"" + bytes.base64EncodedString() + "\"}"
    }

    public static func handle(_ json: String, context: SourceContext) async -> String {
        if let data = json.data(using: .utf8),
           let details = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
           details["op"] as? String == "file" {
            return libraryFile(details["url"] as? String ?? "")
        }
        if let data = json.data(using: .utf8),
           let details = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
           let op = details["op"] as? String, op == "book" || op == "chapter" {
            let address = details["url"] as? String ?? ""
            let index = (details["index"] as? NSNumber)?.intValue ?? -1
            let engines = (details["engines"] as? Bool) ?? false
            return await BuiltInReader.shared.handle(op: op, address: address, index: index, engines: engines, context: context)
        }
        return await EngineTransport.handle(json, http: context.http, web: context.web)
    }
}
