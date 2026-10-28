import Foundation
import NpiaCore

/// The sources whose sign-in is a browser session (qdn and rbooks); sfc signs in through its app API.
public enum HardSessions {
    public static var qdn: SessionSite { QdnSite.session }
    public static var rbooks: SessionSite { RbooksSite.session }

    public static var all: [SessionSite] { [qdn, rbooks] }

    public static func of(_ source: SourceID) -> SessionSite? {
        all.first { $0.source == source }
    }

    /// The sites whose login cookies live on `host` (or a parent of it), for the Browser tab's sign-out.
    public static func forHost(_ host: String) -> [SessionSite] {
        var name = host.lowercased()
        if name.hasPrefix("www.") { name = String(name.dropFirst(4)) }
        return all.filter { name == $0.domain || name.hasSuffix("." + $0.domain) || $0.domain.hasSuffix("." + name) }
    }
}
