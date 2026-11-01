import CoreGraphics
import Foundation
import ImageIO
import libwebp

/// Frames decoded by ImageIO. Animated sources keep every composited frame.
struct DecodedImage {
    var frames: [CGImage]
    /// Seconds per frame, parallel to `frames`.
    var delays: [Double]
    /// 0 loops forever.
    var loopCount: Int

    var isAnimated: Bool { frames.count > 1 }
    var width: Int { frames.first?.width ?? 0 }
    var height: Int { frames.first?.height ?? 0 }
    var hasAlpha: Bool { frames.first.map(ImageCodec.hasAlpha) ?? false }
}

enum ImageCodec {
    /// Decodes WebP, AVIF, GIF, PNG/APNG, JPEG, and anything else ImageIO reads.
    static func decode(_ data: Data, firstFrameOnly: Bool) -> DecodedImage? {
        let options = [kCGImageSourceShouldCache: false] as CFDictionary
        guard let source = CGImageSourceCreateWithData(data as CFData, options) else { return nil }
        let count = CGImageSourceGetCount(source)
        guard count > 0 else { return nil }
        let wanted = firstFrameOnly ? 1 : count
        var frames: [CGImage] = []
        var delays: [Double] = []
        let frameOptions = [kCGImageSourceShouldCacheImmediately: true] as CFDictionary
        for index in 0..<wanted {
            guard let frame = CGImageSourceCreateImageAtIndex(source, index, frameOptions) else {
                if frames.isEmpty { return nil }
                break
            }
            frames.append(frame)
            delays.append(frameDelay(source, index))
        }
        return DecodedImage(frames: frames, delays: delays, loopCount: loopCount(source))
    }

    static func frameCount(_ data: Data) -> Int {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else { return 0 }
        return CGImageSourceGetCount(source)
    }

    /// Pixel size without decoding.
    static func pixelSize(_ data: Data) -> CGSize? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = props[kCGImagePropertyPixelWidth] as? Int,
              let height = props[kCGImagePropertyPixelHeight] as? Int else { return nil }
        return CGSize(width: width, height: height)
    }

    static func hasAlpha(_ image: CGImage) -> Bool {
        switch image.alphaInfo {
        case .none, .noneSkipFirst, .noneSkipLast: return false
        default: return true
        }
    }

    private static func frameDelay(_ source: CGImageSource, _ index: Int) -> Double {
        guard let props = CGImageSourceCopyPropertiesAtIndex(source, index, nil) as? [CFString: Any] else { return 0.1 }
        let containers: [(CFString, CFString, CFString)] = [
            (kCGImagePropertyGIFDictionary, kCGImagePropertyGIFUnclampedDelayTime, kCGImagePropertyGIFDelayTime),
            (kCGImagePropertyPNGDictionary, kCGImagePropertyAPNGUnclampedDelayTime, kCGImagePropertyAPNGDelayTime),
            (kCGImagePropertyWebPDictionary, kCGImagePropertyWebPUnclampedDelayTime, kCGImagePropertyWebPDelayTime),
        ]
        for (container, unclamped, clamped) in containers {
            guard let dict = props[container] as? [CFString: Any] else { continue }
            let value = (dict[unclamped] as? Double) ?? (dict[clamped] as? Double) ?? 0
            // Browsers treat near-zero delays as 100 ms; so does the desktop (Pillow default).
            return value > 0.011 ? value : 0.1
        }
        return 0.1
    }

    private static func loopCount(_ source: CGImageSource) -> Int {
        guard let props = CGImageSourceCopyProperties(source, nil) as? [CFString: Any] else { return 0 }
        let keys: [(CFString, CFString)] = [
            (kCGImagePropertyGIFDictionary, kCGImagePropertyGIFLoopCount),
            (kCGImagePropertyPNGDictionary, kCGImagePropertyAPNGLoopCount),
            (kCGImagePropertyWebPDictionary, kCGImagePropertyWebPLoopCount),
        ]
        for (container, key) in keys {
            if let dict = props[container] as? [CFString: Any], let loops = dict[key] as? Int { return loops }
        }
        return 0
    }

    // MARK: - Pixels

    /// sRGB RGBA8 pixels; straight (non-premultiplied) alpha when `straightAlpha` is set.
    static func rgbaPixels(_ image: CGImage, straightAlpha: Bool) -> [UInt8]? {
        let width = image.width, height = image.height
        guard width > 0, height > 0 else { return nil }
        var pixels = [UInt8](repeating: 0, count: width * height * 4)
        let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
        let drawn = pixels.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(
                data: buffer.baseAddress, width: width, height: height, bitsPerComponent: 8,
                bytesPerRow: width * 4, space: colorSpace,
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            ) else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard drawn else { return nil }
        if straightAlpha {
            var index = 0
            while index < pixels.count {
                let alpha = Int(pixels[index + 3])
                if alpha > 0 && alpha < 255 {
                    pixels[index] = UInt8(min(255, Int(pixels[index]) * 255 / alpha))
                    pixels[index + 1] = UInt8(min(255, Int(pixels[index + 1]) * 255 / alpha))
                    pixels[index + 2] = UInt8(min(255, Int(pixels[index + 2]) * 255 / alpha))
                }
                index += 4
            }
        }
        return pixels
    }

    /// Composites onto white so JPEG output does not turn transparent areas black.
    static func flattened(_ image: CGImage) -> CGImage? {
        guard hasAlpha(image) else { return image }
        let width = image.width, height = image.height
        let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
        guard let context = CGContext(
            data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
            space: colorSpace, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
        ) else { return nil }
        let rect = CGRect(x: 0, y: 0, width: width, height: height)
        context.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
        context.fill(rect)
        context.draw(image, in: rect)
        return context.makeImage()
    }

    // MARK: - ImageIO encoders

    enum ContainerType {
        case jpeg, png, gif

        var identifier: CFString {
            switch self {
            case .jpeg: return "public.jpeg" as CFString
            case .png: return "public.png" as CFString
            case .gif: return "com.compuserve.gif" as CFString
            }
        }
    }

    /// JPEG (first frame only), PNG/APNG, or GIF through ImageIO.
    static func encode(_ decoded: DecodedImage, as type: ContainerType, quality: Int) -> Data? {
        var frames = decoded.frames
        if type == .jpeg {
            guard let first = frames.first.flatMap(flattened) else { return nil }
            frames = [first]
        }
        guard !frames.isEmpty else { return nil }
        let output = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(output, type.identifier, frames.count, nil) else { return nil }
        let animated = frames.count > 1
        if animated {
            let container: [CFString: Any]
            switch type {
            case .gif: container = [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFLoopCount: decoded.loopCount]]
            case .png: container = [kCGImagePropertyPNGDictionary: [kCGImagePropertyAPNGLoopCount: decoded.loopCount]]
            case .jpeg: container = [:]
            }
            CGImageDestinationSetProperties(destination, container as CFDictionary)
        }
        for (index, frame) in frames.enumerated() {
            var props: [CFString: Any] = [:]
            if type == .jpeg {
                props[kCGImageDestinationLossyCompressionQuality] = Double(min(100, max(1, quality))) / 100
            }
            if animated {
                let delay = index < decoded.delays.count ? decoded.delays[index] : 0.1
                switch type {
                case .gif: props[kCGImagePropertyGIFDictionary] = [kCGImagePropertyGIFDelayTime: delay]
                case .png: props[kCGImagePropertyPNGDictionary] = [kCGImagePropertyAPNGDelayTime: delay]
                case .jpeg: break
                }
            }
            CGImageDestinationAddImage(destination, frame, props as CFDictionary)
        }
        guard CGImageDestinationFinalize(destination) else { return nil }
        return output as Data
    }

    // MARK: - libwebp

    /// WebP's format limit; larger images (long webtoon strips) keep their source bytes.
    static let webpMaxDimension = 16_383

    static func encodeWebP(_ decoded: DecodedImage, quality: Int) -> Data? {
        guard decoded.width > 0, decoded.height > 0,
              decoded.width <= webpMaxDimension, decoded.height <= webpMaxDimension else { return nil }
        if decoded.isAnimated {
            return encodeAnimatedWebP(decoded, quality: quality)
        }
        guard let image = decoded.frames.first,
              let pixels = rgbaPixels(image, straightAlpha: hasAlpha(image)) else { return nil }
        var output: UnsafeMutablePointer<UInt8>?
        let width = Int32(image.width), height = Int32(image.height)
        let size = pixels.withUnsafeBufferPointer { buffer in
            WebPEncodeRGBA(buffer.baseAddress, width, height, width * 4, Float(min(100, max(0, quality))), &output)
        }
        guard size > 0, let output else { return nil }
        defer { WebPFree(output) }
        return Data(bytes: output, count: size)
    }

    private static func encodeAnimatedWebP(_ decoded: DecodedImage, quality: Int) -> Data? {
        let width = Int32(decoded.width), height = Int32(decoded.height)
        var options = WebPAnimEncoderOptions()
        guard WebPAnimEncoderOptionsInit(&options) != 0 else { return nil }
        options.anim_params.loop_count = Int32(decoded.loopCount)
        guard let encoder = WebPAnimEncoderNew(width, height, &options) else { return nil }
        defer { WebPAnimEncoderDelete(encoder) }

        var config = WebPConfig()
        guard WebPConfigInit(&config) != 0 else { return nil }
        config.quality = Float(min(100, max(0, quality)))

        var timestamp: Int32 = 0
        for (index, frame) in decoded.frames.enumerated() {
            guard frame.width == decoded.width, frame.height == decoded.height,
                  let pixels = rgbaPixels(frame, straightAlpha: true) else { return nil }
            var picture = WebPPicture()
            guard WebPPictureInit(&picture) != 0 else { return nil }
            picture.width = width
            picture.height = height
            picture.use_argb = 1
            let imported = pixels.withUnsafeBufferPointer { WebPPictureImportRGBA(&picture, $0.baseAddress, width * 4) }
            defer { WebPPictureFree(&picture) }
            guard imported != 0, WebPAnimEncoderAdd(encoder, &picture, timestamp, &config) != 0 else { return nil }
            let delay = index < decoded.delays.count ? decoded.delays[index] : 0.1
            timestamp += Int32((delay * 1000).rounded())
        }
        guard WebPAnimEncoderAdd(encoder, nil, timestamp, nil) != 0 else { return nil }
        var assembled = WebPData()
        WebPDataInit(&assembled)
        defer { WebPDataClear(&assembled) }
        guard WebPAnimEncoderAssemble(encoder, &assembled) != 0, let bytes = assembled.bytes else { return nil }
        return Data(bytes: bytes, count: assembled.size)
    }
}
