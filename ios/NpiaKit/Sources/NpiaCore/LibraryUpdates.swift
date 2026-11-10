import Foundation

public struct LibraryUpdateEntry: Codable, Sendable, Equatable {
    public var url: String
    public var ranged: Bool
    public var autoUpdate: Bool?
    public var enabled: Bool { !url.isEmpty && (autoUpdate ?? !ranged) }

    public init(url: String = "", ranged: Bool = true, autoUpdate: Bool? = nil) {
        self.url = url
        self.ranged = ranged
        self.autoUpdate = autoUpdate
    }
}

public struct LibraryUpdateState: Codable, Sendable, Equatable {
    public var enabled = true
    public var background = false
    public var uploadDrive = false
    public var books: [String: LibraryUpdateEntry] = [:]
    public init() {}
}

public func librarySourceKey(_ url: URL, source: SourceID? = nil) -> String {
    if let source, source != .reader { return source.rawValue }
    let host = (url.host ?? url.absoluteString).lowercased()
    return host.split(separator: ".").suffix(2).joined(separator: ".")
}
