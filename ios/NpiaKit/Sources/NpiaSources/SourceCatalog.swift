import Foundation
import NpiaCore

public enum SourceCatalog {
    /// Checked in order; put more specific matchers before generic ones (`reader` last).
    public static let adapters: [any SourceAdapter.Type] = [
        NpiaAdapter.self,
        GlobalNpiaAdapter.self,
        MpiaAdapter.self,
        KpageAdapter.self,
        SfcAdapter.self,
        NwebAdapter.self,
        JaraAdapter.self,
        RbooksAdapter.self,
        NtkAdapter.self,
        FlooAdapter.self,
        QdnAdapter.self,
        FenrirAdapter.self,
        XiyuwxAdapter.self,
        YedujiAdapter.self,
        MaruAdapter.self,
        KoboAdapter.self,
        ReaderAdapter.self,
    ]

    public static func adapterType(for url: URL) -> (any SourceAdapter.Type)? {
        adapters.first { $0.matches(url) }
    }

    public static func adapter(for url: URL) throws -> any SourceAdapter {
        guard let type = adapterType(for: url) else { throw SourceError.unsupportedURL(url) }
        return type.init()
    }

    public static func adapterType(for id: SourceID) -> (any SourceAdapter.Type)? {
        adapters.first { $0.id == id }
    }
}
