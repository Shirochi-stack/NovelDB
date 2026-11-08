import CoreGraphics
import Foundation
import ImageIO
import zlib

/// Streams a full chapter to PNG without allocating a chapter-sized bitmap.
enum PNGStrip {
    static func write(_ panels: [Data], to url: URL) throws -> Bool {
        var width = 0
        var height = 0
        for panel in panels {
            guard let source = CGImageSourceCreateWithData(panel as CFData, nil),
                  let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
                  let w = properties[kCGImagePropertyPixelWidth] as? Int, w > 0,
                  let h = properties[kCGImagePropertyPixelHeight] as? Int, h > 0 else { return false }
            width = max(width, w)
            height += h
        }
        guard width <= (Int(Int32.max) - 1) / 4, height <= Int(Int32.max) else {
            throw OutputError.nothingToWrite("This chapter exceeds PNG's maximum dimensions.")
        }

        guard FileManager.default.createFile(atPath: url.path, contents: nil) else {
            throw CocoaError(.fileWriteUnknown)
        }
        let file = try FileHandle(forWritingTo: url)
        defer { try? file.close() }
        func chunk(_ type: String, _ data: Data = Data()) throws {
            let name = Data(type.utf8)
            func checksum(_ bytes: Data, seed: UInt) -> UInt {
                if bytes.isEmpty { return seed }
                return bytes.withUnsafeBytes {
                    crc32(seed, $0.bindMemory(to: UInt8.self).baseAddress, UInt32(bytes.count))
                }
            }
            try file.write(contentsOf: number(UInt32(data.count)) + name)
            try file.write(contentsOf: data)
            try file.write(contentsOf: number(UInt32(checksum(data, seed: checksum(name, seed: 0)))))
        }
        try file.write(contentsOf: Data([137, 80, 78, 71, 13, 10, 26, 10]))
        try chunk("IHDR", number(UInt32(width)) + number(UInt32(height)) + Data([8, 6, 0, 0, 0]))

        var stream = z_stream()
        guard deflateInit_(&stream, 3, ZLIB_VERSION, Int32(MemoryLayout<z_stream>.size)) == Z_OK else {
            throw CocoaError(.fileWriteUnknown)
        }
        defer { deflateEnd(&stream) }
        let capacity = 64 * 1024
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: capacity)
        defer { buffer.deallocate() }
        func compress(_ input: UnsafeMutablePointer<UInt8>?, count: Int, flush: Int32) throws {
            stream.next_in = input
            stream.avail_in = UInt32(count)
            repeat {
                stream.next_out = buffer
                stream.avail_out = UInt32(capacity)
                let status = deflate(&stream, flush)
                if status == Z_BUF_ERROR && flush == Z_NO_FLUSH && stream.avail_in == 0 { break }
                guard status == Z_OK || status == Z_STREAM_END else { throw CocoaError(.fileWriteUnknown) }
                let size = capacity - Int(stream.avail_out)
                if size > 0 { try chunk("IDAT", Data(bytes: buffer, count: size)) }
                if status == Z_STREAM_END { break }
            } while stream.avail_in != 0 || stream.avail_out == 0 || flush == Z_FINISH
        }

        let rowSize = width * 4
        let row = UnsafeMutablePointer<UInt8>.allocate(capacity: rowSize + 1)
        defer { row.deallocate() }
        row[0] = 0  // PNG filter type None.
        let bitmap = CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue
        for panel in panels {
            try Task.checkCancellation()
            try autoreleasepool {
                guard let source = CGImageSourceCreateWithData(panel as CFData, nil),
                      let image = CGImageSourceCreateImageAtIndex(source, 0, nil),
                      let canvas = CGContext(data: nil, width: width, height: image.height,
                                             bitsPerComponent: 8, bytesPerRow: 0,
                                             space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: bitmap),
                      let pixels = canvas.data else { throw CocoaError(.fileReadCorruptFile) }
                canvas.setFillColor(CGColor(gray: 1, alpha: 1))
                canvas.fill(CGRect(x: 0, y: 0, width: width, height: image.height))
                canvas.draw(image, in: CGRect(x: (width - image.width) / 2, y: 0,
                                              width: image.width, height: image.height))
                for y in 0..<image.height {
                    UnsafeMutableRawPointer(row.advanced(by: 1)).copyMemory(
                        from: pixels.advanced(by: y * canvas.bytesPerRow), byteCount: rowSize)
                    try compress(row, count: rowSize + 1, flush: Z_NO_FLUSH)
                }
            }
        }
        try compress(nil, count: 0, flush: Z_FINISH)
        try chunk("IEND")
        return true
    }

    private static func number(_ value: UInt32) -> Data {
        var bigEndian = value.bigEndian
        return withUnsafeBytes(of: &bigEndian) { Data($0) }
    }
}
