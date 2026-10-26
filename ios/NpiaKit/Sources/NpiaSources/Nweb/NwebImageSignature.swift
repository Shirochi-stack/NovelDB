import CoreGraphics
import Foundation
import ImageIO

/// Aspect ratio plus a 64x8 grayscale thumbnail, used to recognise the publisher logo
/// banner that closes every episode of some works (`_nweb_fetch_signature`).
struct NwebImageSignature: Equatable, Sendable {
    static let width = 64
    static let height = 8
    /// Banners are much wider than tall; covers and illustrations never are.
    static let logoMinAspect = 3.5

    var aspect: Double
    var pixels: [UInt8]

    init(aspect: Double, pixels: [UInt8]) {
        self.aspect = aspect
        self.pixels = pixels
    }

    init?(imageData: Data) {
        guard let source = CGImageSourceCreateWithData(imageData as CFData, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil),
              image.height > 0 else { return nil }
        var pixels = [UInt8](repeating: 255, count: Self.width * Self.height)
        let drawn = pixels.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(
                data: buffer.baseAddress, width: Self.width, height: Self.height, bitsPerComponent: 8,
                bytesPerRow: Self.width, space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue
            ) else { return false }
            context.setFillColor(gray: 1, alpha: 1)
            context.fill(CGRect(x: 0, y: 0, width: Self.width, height: Self.height))
            context.interpolationQuality = .high
            context.draw(image, in: CGRect(x: 0, y: 0, width: Self.width, height: Self.height))
            return true
        }
        guard drawn else { return nil }
        self.init(aspect: Double(image.width) / Double(image.height), pixels: pixels)
    }

    var isBanner: Bool { aspect >= Self.logoMinAspect }

    /// Same image: same shape and the same marks on a white banner.
    func matches(_ other: NwebImageSignature) -> Bool {
        guard aspect > 0, abs(aspect - other.aspect) <= aspect * 0.08 else { return false }
        var total = 0
        var count = 0
        for (a, b) in zip(pixels, other.pixels) where a < 235 || b < 235 {
            total += abs(Int(a) - Int(b))
            count += 1
        }
        return count == 0 || Double(total) / Double(count) < 30
    }
}
