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

/// Recompresses downloaded images per `ImageSettings`, following the desktop
/// `_encode_image_bytes` rules:
///
/// - GIFs stay byte-for-byte unless `convertGIFs` is on; with `staticOnly` they keep the
///   `.gif` extension but drop to the first frame.
/// - Animated sources keep their animation for WebP and PNG (APNG) targets; JPEG keeps the
///   first frame. `staticOnly` flattens every animated source.
/// - Covers use `compressCover`, `coverQuality`, and `coverFormat`, and are always static.
/// - Anything that fails to decode or encode keeps its original bytes.
public struct ImageProcessor: Sendable {
    public var settings: ImageSettings

    public init(settings: ImageSettings) {
        self.settings = settings
    }

    public func process(_ data: Data, isCover: Bool = false) throws -> ProcessedImage {
        let sourceExtension = Self.sniffExtension(data)
        let original = ProcessedImage(data: data, fileExtension: sourceExtension)
        let compress = isCover ? settings.compressCover : settings.compress
        guard compress, sourceExtension != "bin" else { return original }
        let format = isCover ? settings.coverFormat : settings.format
        let quality = isCover ? settings.coverQuality : settings.quality
        let staticOnly = settings.staticOnly || isCover

        if sourceExtension == "gif" && !settings.convertGIFs && !isCover {
            guard settings.staticOnly else { return original }
            guard let first = ImageCodec.decode(data, firstFrameOnly: true),
                  let gif = ImageCodec.encode(first, as: .gif, quality: quality) else { return original }
            return ProcessedImage(data: gif, fileExtension: "gif")
        }

        let target: ImageFormat
        if format == .original {
            // Only re-encode when animation has to be dropped.
            guard staticOnly, ImageCodec.frameCount(data) > 1 else { return original }
            switch sourceExtension {
            case "jpg": target = .jpeg
            case "png": target = .png
            case "gif":
                guard let first = ImageCodec.decode(data, firstFrameOnly: true),
                      let gif = ImageCodec.encode(first, as: .gif, quality: quality) else { return original }
                return ProcessedImage(data: gif, fileExtension: "gif")
            default: target = .webp
            }
        } else {
            target = format
        }

        let firstFrameOnly = staticOnly || target == .jpeg
        guard let decoded = ImageCodec.decode(data, firstFrameOnly: firstFrameOnly),
              let encoded = Self.encode(decoded, as: target, quality: quality) else { return original }
        // Re-encoding into the same container must not make the file bigger.
        if encoded.fileExtension == sourceExtension, encoded.data.count >= data.count {
            return original
        }
        return encoded
    }

    /// Re-encodes an already processed image at a lower quality for `recompressTargetMB`,
    /// keeping its container so file names in the book stay valid. GIF and PNG are
    /// returned unchanged, as on the desktop.
    public func reencode(_ data: Data, fileExtension: String, quality: Int) -> Data {
        let target: ImageFormat
        switch fileExtension.lowercased() {
        case "jpg", "jpeg": target = .jpeg
        case "webp": target = .webp
        default: return data
        }
        guard let decoded = ImageCodec.decode(data, firstFrameOnly: settings.staticOnly || target == .jpeg),
              let encoded = Self.encode(decoded, as: target, quality: quality),
              encoded.data.count < data.count else { return data }
        return encoded.data
    }

    static func encode(_ decoded: DecodedImage, as format: ImageFormat, quality: Int) -> ProcessedImage? {
        switch format {
        case .webp:
            return ImageCodec.encodeWebP(decoded, quality: quality).map { ProcessedImage(data: $0, fileExtension: "webp") }
        case .jpeg:
            return ImageCodec.encode(decoded, as: .jpeg, quality: quality).map { ProcessedImage(data: $0, fileExtension: "jpg") }
        case .png:
            return ImageCodec.encode(decoded, as: .png, quality: quality).map { ProcessedImage(data: $0, fileExtension: "png") }
        case .original:
            return nil
        }
    }

    /// Next quality for one `recompressTargetMB` pass, or nil when it cannot go lower.
    /// Mirrors the desktop heuristic: quality * sqrt(target / size), at least 10 lower per
    /// pass, floor 10.
    public static func nextRecompressQuality(current: Int, size: Int, target: Int) -> Int? {
        let minimum = 10
        let ratio = Double(target) / Double(max(size, 1))
        var next = max(minimum, Int(Double(current) * ratio.squareRoot()))
        if next >= current { next = max(minimum, current - 10) }
        return next == current ? nil : next
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

    /// Whether ImageIO reports more than one frame.
    public static func isAnimated(_ data: Data) -> Bool {
        ImageCodec.frameCount(data) > 1
    }

    /// Pixel width and height without a full decode.
    public static func pixelSize(_ data: Data) -> (width: Int, height: Int)? {
        ImageCodec.pixelSize(data).map { (Int($0.width), Int($0.height)) }
    }
}
