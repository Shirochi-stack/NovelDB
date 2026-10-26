import Foundation
import NpiaCore

public struct ProcessedImage: Sendable {
    public var data: Data
    /// File extension without the dot (`webp`, `jpg`, `png`, `gif`).
    public var fileExtension: String

    public init(data: Data, fileExtension: String) {
        self.data = data
        self.fileExtension = fileExtension
    }
}

/// Recompresses downloaded images per `ImageSettings`.
///
/// TODO(images workstream): ImageIO decode (WebP/AVIF/GIF), libwebp encode, JPEG/PNG encode,
/// GIF-to-static conversion, cover settings, and `recompressTargetMB`. Until then images pass
/// through unchanged.
public struct ImageProcessor: Sendable {
    public var settings: ImageSettings

    public init(settings: ImageSettings) {
        self.settings = settings
    }

    public func process(_ data: Data, isCover: Bool = false) throws -> ProcessedImage {
        ProcessedImage(data: data, fileExtension: Self.sniffExtension(data))
    }

    public static func sniffExtension(_ data: Data) -> String {
        let bytes = [UInt8](data.prefix(12))
        if bytes.starts(with: [0xFF, 0xD8, 0xFF]) { return "jpg" }
        if bytes.starts(with: [0x89, 0x50, 0x4E, 0x47]) { return "png" }
        if bytes.starts(with: [0x47, 0x49, 0x46, 0x38]) { return "gif" }
        if bytes.count >= 12, Array(bytes[0...3]) == [0x52, 0x49, 0x46, 0x46], Array(bytes[8...11]) == [0x57, 0x45, 0x42, 0x50] { return "webp" }
        if bytes.count >= 12, Array(bytes[4...7]) == [0x66, 0x74, 0x79, 0x70] { return "avif" }
        return "bin"
    }
}
