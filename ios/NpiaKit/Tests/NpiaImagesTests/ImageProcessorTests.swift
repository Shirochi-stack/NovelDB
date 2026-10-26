import CoreGraphics
import ImageIO
import XCTest
import NpiaCore
@testable import NpiaImages

/// Tiny images generated in memory; nothing is read from disk or the network.
enum TestImages {
    static func cgImage(width: Int, height: Int, alpha: Bool = false, noise: Bool = false, shade: UInt8 = 200) -> CGImage {
        var pixels = [UInt8](repeating: 0, count: width * height * 4)
        var seed: UInt32 = 12345
        for index in stride(from: 0, to: pixels.count, by: 4) {
            if noise {
                seed = seed &* 1_103_515_245 &+ 12345
                pixels[index] = UInt8(truncatingIfNeeded: seed >> 16)
                pixels[index + 1] = UInt8(truncatingIfNeeded: seed >> 8)
                pixels[index + 2] = UInt8(truncatingIfNeeded: seed >> 4)
            } else {
                pixels[index] = shade
                pixels[index + 1] = shade / 2
                pixels[index + 2] = 40
            }
            pixels[index + 3] = alpha ? 128 : 255
        }
        if alpha {
            // Premultiply for the bitmap context.
            for index in stride(from: 0, to: pixels.count, by: 4) {
                for channel in 0..<3 { pixels[index + channel] = UInt8(Int(pixels[index + channel]) * 128 / 255) }
            }
        }
        let provider = CGDataProvider(data: Data(pixels) as CFData)!
        return CGImage(
            width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: width * 4,
            space: CGColorSpace(name: CGColorSpace.sRGB)!,
            bitmapInfo: CGBitmapInfo(rawValue: (alpha ? CGImageAlphaInfo.premultipliedLast : CGImageAlphaInfo.noneSkipLast).rawValue),
            provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent
        )!
    }

    static func encode(_ images: [CGImage], type: String, quality: Double? = nil, delay: Double = 0.1) -> Data {
        let output = NSMutableData()
        let destination = CGImageDestinationCreateWithData(output, type as CFString, images.count, nil)!
        if images.count > 1, type == "com.compuserve.gif" {
            CGImageDestinationSetProperties(destination, [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFLoopCount: 0]] as CFDictionary)
        }
        for image in images {
            var props: [CFString: Any] = [:]
            if let quality { props[kCGImageDestinationLossyCompressionQuality] = quality }
            if images.count > 1 { props[kCGImagePropertyGIFDictionary] = [kCGImagePropertyGIFDelayTime: delay] }
            CGImageDestinationAddImage(destination, image, props as CFDictionary)
        }
        precondition(CGImageDestinationFinalize(destination))
        return output as Data
    }

    static func png(width: Int = 32, height: Int = 24, alpha: Bool = false, noise: Bool = false) -> Data {
        encode([cgImage(width: width, height: height, alpha: alpha, noise: noise)], type: "public.png")
    }

    static func jpeg(width: Int = 64, height: Int = 64, noise: Bool = true, quality: Double = 0.95) -> Data {
        encode([cgImage(width: width, height: height, noise: noise)], type: "public.jpeg", quality: quality)
    }

    static func animatedGIF(frames: Int = 3) -> Data {
        let images = (0..<frames).map { cgImage(width: 16, height: 16, shade: UInt8(60 + $0 * 60)) }
        return encode(images, type: "com.compuserve.gif")
    }

    static func frameCount(_ data: Data) -> Int {
        CGImageSourceCreateWithData(data as CFData, nil).map(CGImageSourceGetCount) ?? 0
    }

    static func size(_ data: Data) -> (Int, Int)? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { return nil }
        return (image.width, image.height)
    }
}

final class ImageProcessorTests: XCTestCase {
    private func processor(_ configure: (inout ImageSettings) -> Void = { _ in }) -> ImageProcessor {
        var settings = ImageSettings()
        configure(&settings)
        return ImageProcessor(settings: settings)
    }

    func testSniffExtension() {
        XCTAssertEqual(ImageProcessor.sniffExtension(TestImages.png()), "png")
        XCTAssertEqual(ImageProcessor.sniffExtension(TestImages.jpeg()), "jpg")
        XCTAssertEqual(ImageProcessor.sniffExtension(TestImages.animatedGIF()), "gif")
        XCTAssertEqual(ImageProcessor.sniffExtension(Data("RIFF\0\0\0\0WEBPVP8 ".utf8)), "webp")
        XCTAssertEqual(ImageProcessor.sniffExtension(Data([0, 0, 0, 0x1C, 0x66, 0x74, 0x79, 0x70, 0x61, 0x76, 0x69, 0x66])), "avif")
        XCTAssertEqual(ImageProcessor.sniffExtension(Data("hello".utf8)), "bin")
    }

    func testPNGToWebPRoundTrips() throws {
        let result = try processor().process(TestImages.png(width: 40, height: 30))
        XCTAssertEqual(result.fileExtension, "webp")
        XCTAssertEqual(ImageProcessor.sniffExtension(result.data), "webp")
        // ImageIO decodes what libwebp wrote.
        let size = try XCTUnwrap(TestImages.size(result.data))
        XCTAssertEqual(size.0, 40)
        XCTAssertEqual(size.1, 30)
    }

    func testTransparentPNGToWebPKeepsAlpha() throws {
        let result = try processor().process(TestImages.png(alpha: true))
        XCTAssertEqual(result.fileExtension, "webp")
        let source = try XCTUnwrap(CGImageSourceCreateWithData(result.data as CFData, nil))
        let image = try XCTUnwrap(CGImageSourceCreateImageAtIndex(source, 0, nil))
        XCTAssertTrue(ImageCodec.hasAlpha(image))
    }

    func testJPEGTargetFlattensAlpha() throws {
        let result = try processor { $0.format = .jpeg; $0.quality = 70 }.process(TestImages.png(alpha: true))
        XCTAssertEqual(result.fileExtension, "jpg")
        XCTAssertEqual(ImageProcessor.sniffExtension(result.data), "jpg")
    }

    func testPNGTarget() throws {
        let result = try processor { $0.format = .png }.process(TestImages.jpeg(width: 8, height: 8))
        XCTAssertEqual(result.fileExtension, "png")
    }

    func testCompressOffPassesThrough() throws {
        let data = TestImages.png()
        let result = try processor { $0.compress = false }.process(data)
        XCTAssertEqual(result.data, data)
        XCTAssertEqual(result.fileExtension, "png")
    }

    func testUnknownBytesPassThrough() throws {
        let data = Data("not an image".utf8)
        let result = try processor().process(data)
        XCTAssertEqual(result.data, data)
        XCTAssertEqual(result.fileExtension, "bin")
    }

    func testGIFKeptByDefault() throws {
        let gif = TestImages.animatedGIF()
        let result = try processor().process(gif)
        XCTAssertEqual(result.data, gif)
        XCTAssertEqual(result.fileExtension, "gif")
    }

    func testGIFStaticOnlyKeepsExtensionWithOneFrame() throws {
        let gif = TestImages.animatedGIF()
        XCTAssertEqual(TestImages.frameCount(gif), 3)
        let result = try processor { $0.staticOnly = true }.process(gif)
        XCTAssertEqual(result.fileExtension, "gif")
        XCTAssertEqual(TestImages.frameCount(result.data), 1)
    }

    func testConvertGIFsKeepsAnimationInWebP() throws {
        let result = try processor { $0.convertGIFs = true }.process(TestImages.animatedGIF())
        XCTAssertEqual(result.fileExtension, "webp")
        XCTAssertEqual(TestImages.frameCount(result.data), 3)
    }

    func testConvertGIFsStaticOnlyFlattens() throws {
        let result = try processor { $0.convertGIFs = true; $0.staticOnly = true }.process(TestImages.animatedGIF())
        XCTAssertEqual(result.fileExtension, "webp")
        XCTAssertEqual(TestImages.frameCount(result.data), 1)
    }

    func testConvertGIFsToPNGMakesAPNG() throws {
        let result = try processor { $0.convertGIFs = true; $0.format = .png }.process(TestImages.animatedGIF())
        XCTAssertEqual(result.fileExtension, "png")
        XCTAssertEqual(TestImages.frameCount(result.data), 3)
    }

    func testOriginalFormatKeepsStaticImages() throws {
        let data = TestImages.png()
        let result = try processor { $0.format = .original }.process(data)
        XCTAssertEqual(result.data, data)
    }

    func testOriginalFormatStaticOnlyFlattensGIF() throws {
        let result = try processor { $0.format = .original; $0.staticOnly = true; $0.convertGIFs = true }.process(TestImages.animatedGIF())
        XCTAssertEqual(result.fileExtension, "gif")
        XCTAssertEqual(TestImages.frameCount(result.data), 1)
    }

    func testCoverSettings() throws {
        let data = TestImages.png()
        let untouched = try processor { $0.compressCover = false }.process(data, isCover: true)
        XCTAssertEqual(untouched.data, data)

        let cover = try processor { $0.compressCover = true; $0.coverFormat = .jpeg; $0.coverQuality = 60 }.process(data, isCover: true)
        XCTAssertEqual(cover.fileExtension, "jpg")

        let animated = try processor { $0.compressCover = true; $0.coverFormat = .webp }.process(TestImages.animatedGIF(), isCover: true)
        XCTAssertEqual(animated.fileExtension, "webp")
        XCTAssertEqual(TestImages.frameCount(animated.data), 1)
    }

    func testSameContainerNeverGrows() throws {
        // A JPEG saved at low quality must not be re-encoded bigger at quality 100.
        let small = TestImages.jpeg(quality: 0.2)
        let result = try processor { $0.format = .jpeg; $0.quality = 100 }.process(small)
        XCTAssertEqual(result.data, small)
    }

    func testReencodeLowersSizeAndKeepsContainer() {
        let jpeg = TestImages.jpeg(width: 96, height: 96, quality: 1.0)
        let smaller = processor().reencode(jpeg, fileExtension: "jpg", quality: 30)
        XCTAssertLessThan(smaller.count, jpeg.count)
        XCTAssertEqual(ImageProcessor.sniffExtension(smaller), "jpg")

        let png = TestImages.png()
        XCTAssertEqual(processor().reencode(png, fileExtension: "png", quality: 10), png)
    }

    func testRecompressQualitySteps() {
        XCTAssertEqual(ImageProcessor.nextRecompressQuality(current: 90, size: 400, target: 100), 45)
        // Close to target still steps down by 10.
        XCTAssertEqual(ImageProcessor.nextRecompressQuality(current: 90, size: 100, target: 100), 80)
        XCTAssertEqual(ImageProcessor.nextRecompressQuality(current: 90, size: 101, target: 100), 89)
        XCTAssertEqual(ImageProcessor.nextRecompressQuality(current: 15, size: 1000, target: 1), 10)
        XCTAssertNil(ImageProcessor.nextRecompressQuality(current: 10, size: 1000, target: 1))
    }

    func testWebPRefusesOversizedImages() {
        let tall = DecodedImage(frames: [TestImages.cgImage(width: 1, height: 16_384)], delays: [0], loopCount: 0)
        XCTAssertNil(ImageCodec.encodeWebP(tall, quality: 80))
    }
}

final class ImageReferencesTests: XCTestCase {
    func testRenamed() {
        XCTAssertEqual(ImageReferences.renamed("images/0001_03.png", toExtension: "webp"), "images/0001_03.webp")
        XCTAssertEqual(ImageReferences.renamed("images/raw", toExtension: "jpg"), "images/raw.jpg")
        XCTAssertEqual(ImageReferences.renamed("dir.v2/raw", toExtension: "jpg"), "dir.v2/raw.jpg")
    }

    func testRewriteExactAndRelative() {
        let html = #"<p><img src="images/1.png"/><img alt='x' src='../images/1.png'><img src="images/2.png"><a href="images/1.png">x</a></p>"#
        let out = ImageReferences.rewrite(html, renames: ["images/1.png": "images/1.webp"])
        XCTAssertEqual(out, #"<p><img src="images/1.webp"/><img alt='x' src='../images/1.webp'><img src="images/2.png"><a href="images/1.webp">x</a></p>"#)
    }

    func testRewriteLeavesSimilarNamesAlone() {
        let html = #"<img src="images/11.png"><img src="other1.png">"#
        XCTAssertEqual(ImageReferences.rewrite(html, renames: ["1.png": "1.webp"]), html)
    }
}
