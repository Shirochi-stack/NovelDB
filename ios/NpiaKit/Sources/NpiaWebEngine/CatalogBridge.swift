import Foundation

/// The scripts the Browser and Catalog tabs inject into pages. They are plain files in `Resources/`
/// (`catalog-bridge.js` is byte-identical to the Android and desktop copies, which a test checks).
enum PageScripts {
    /// The text of `Resources/<name>.js`; empty when the file is missing (a packaging mistake that
    /// the tests catch).
    static func text(_ name: String) -> String {
        guard let url = url(name), let text = try? String(contentsOf: url, encoding: .utf8) else { return "" }
        return text
    }

    static func url(_ name: String) -> URL? {
        let folders: [String?] = ["Resources", nil]
        for folder in folders {
            if let found = Bundle.module.url(forResource: name, withExtension: "js", subdirectory: folder) {
                return found
            }
        }
        return nil
    }
}

/// The catalog website's bridge script, injected at document start into the page of the Catalog tab.
///
/// It reports `{type: 'location', url, title}` on start and whenever the address changes (single-page
/// sites included), makes NovelDB's cards open View Details, adds the link-cart buttons, and answers
/// `window.NovelDBApp.setMode({cartLinks, download: {url, title} | null})` and `setCart(urls)`.
/// Messages arrive as JSON text on the `noveldb` script message handler.
public enum CatalogBridge {
    /// `window.webkit.messageHandlers.<name>` receives the bridge's messages.
    public static let handlerName = "noveldb"

    public static let source: String = PageScripts.text("catalog-bridge")

    /// The script file, for checks on its exact bytes.
    public static var resourceURL: URL? { PageScripts.url("catalog-bridge") }
}
