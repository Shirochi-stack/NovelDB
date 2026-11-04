import Foundation
import NpiaCore

/// The network of the Catalog's generic search page (`window.NovelDBTransport`): the same transport
/// the engines' page scripts use, so requests carry the web view's cookies and `{op: "window"}` runs
/// a script in a hidden page of the address (a site's bot check, HaruNeko's window scripts).
/// `{op: "book"}` and `{op: "chapter"}` read a book with the built-in scraper of its site (`BuiltInReader`).
public enum CatalogTransport {
    public static func handle(_ json: String, context: SourceContext) async -> String {
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
