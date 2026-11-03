import Foundation
import NpiaCore

/// The network of the Catalog's generic search page (`window.NovelDBTransport`): the same transport
/// the engines' page scripts use, so requests carry the web view's cookies and `{op: "window"}` runs
/// a script in a hidden page of the address (a site's bot check, HaruNeko's window scripts).
public enum CatalogTransport {
    public static func handle(_ json: String, context: SourceContext) async -> String {
        await EngineTransport.handle(json, http: context.http, web: context.web)
    }
}
