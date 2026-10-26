import CoreGraphics
import CoreText
import Foundation
import ImageIO
import NpiaCore

public enum PDFWriterError: Error, Equatable {
    case cannotCreate(URL)
}

/// PDF output drawn with Core Graphics and paged with Core Text, so it runs the same on iOS
/// and macOS (no UIKit or PDFKit). Matches the desktop WeasyPrint layout: A4, 1 inch margins,
/// cover page, info page, optional TOC, one chapter per page break.
///
/// - `pdfTOC`: a table of contents after the info page; entries link to their chapters.
/// - `pdfCounterLayout`: TOC entries show dot leaders and page numbers (two-pass layout).
/// - `pdfPageNumbers`: centred page numbers in the footer of every page except the cover.
public struct PDFWriter: BookWriter {
    public let format = OutputFormat.pdf
    public init() {}

    public func write(_ bundle: BookBundle, baseName: String, to directory: URL) async throws -> URL {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent(baseName + ".pdf")
        let document = PDFDocumentPlan(bundle)

        var chapterPages: [Int]?
        if bundle.settings.text.pdfTOC && bundle.settings.text.pdfCounterLayout {
            let measuring = PDFRenderer(plan: document, context: nil, tocPageNumbers: nil)
            chapterPages = try measuring.render()
        }

        var mediaBox = CGRect(origin: .zero, size: PDFRenderer.pageSize)
        var info: [CFString: Any] = [kCGPDFContextTitle: bundle.book.title, kCGPDFContextCreator: "Npia Mobile"]
        if let author = bundle.book.author { info[kCGPDFContextAuthor] = author }
        try? FileManager.default.removeItem(at: url)
        guard let context = CGContext(url as CFURL, mediaBox: &mediaBox, info as CFDictionary) else {
            throw PDFWriterError.cannotCreate(url)
        }
        do {
            _ = try PDFRenderer(plan: document, context: context, tocPageNumbers: chapterPages).render()
            context.closePDF()
        } catch {
            context.closePDF()
            try? FileManager.default.removeItem(at: url)
            throw error
        }
        return url
    }
}

/// The book reduced to what the renderer draws.
struct PDFDocumentPlan {
    struct Chapter {
        var title: String
        var anchor: String
        var showTitle: Bool
        var blocks: [PDFBlock]
    }

    var title: String
    var author: String?
    var details: [String]
    var synopsis: String?
    var language: String
    var cover: Data?
    var chapters: [Chapter]
    var images: [String: Data]
    var imagesByLeaf: [String: Data]
    var longImageLayout: Bool
    var showTOC: Bool
    var pageNumbers: Bool
    var jpegQuality: Int

    init(_ bundle: BookBundle) {
        let book = bundle.book
        let shared = bundle.settings.effective(for: book.source)
        let longLayout = LongImageLayout.applies(to: bundle)
        title = book.title
        author = book.author
        details = [book.status, book.tags.isEmpty ? nil : book.tags.joined(separator: ", ")].compactMap { $0 }
        synopsis = book.synopsis
        language = book.language
        cover = bundle.cover?.data
        chapters = bundle.chapters.enumerated().map { offset, chapter in
            let title = shared.numberChapters && !chapter.ref.isNotice ? "\(chapter.ref.index). \(chapter.ref.title)" : chapter.ref.title
            return Chapter(
                title: title,
                anchor: "chapter-\(offset + 1)",
                showTitle: !LongImageLayout.hidesTitle(chapter, layoutApplies: longLayout),
                blocks: PDFHTMLParser.blocks(chapter.html)
            )
        }
        images = bundle.images
        var leaves: [String: Data] = [:]
        for (name, data) in bundle.images {
            leaves[(name as NSString).lastPathComponent] = data
        }
        imagesByLeaf = leaves
        longImageLayout = longLayout
        showTOC = bundle.settings.text.pdfTOC
        pageNumbers = bundle.settings.text.pdfPageNumbers
        jpegQuality = bundle.settings.images.quality
    }

    func image(for src: String) -> Data? {
        if let data = images[src] { return data }
        var trimmed = Substring(src)
        while trimmed.hasPrefix("../") { trimmed = trimmed.dropFirst(3) }
        while trimmed.hasPrefix("./") { trimmed = trimmed.dropFirst(2) }
        if let data = images[String(trimmed)] { return data }
        let leaf = (src as NSString).lastPathComponent
        return imagesByLeaf[leaf] ?? imagesByLeaf[leaf.removingPercentEncoding ?? leaf]
    }
}

/// Lays out and draws a `PDFDocumentPlan`. With a nil context it only measures, which the
/// counter layout uses to learn each chapter's first page before drawing the TOC.
final class PDFRenderer {
    static let pageSize = CGSize(width: 595.28, height: 841.89)
    static let margin: CGFloat = 72

    let plan: PDFDocumentPlan
    let context: CGContext?
    let tocPageNumbers: [Int]?

    private(set) var pageCount = 0
    private var pageOpen = false
    /// Distance already used from the top of the content area on the current page.
    private var cursor: CGFloat = 0
    private let content = CGRect(
        x: margin, y: margin,
        width: pageSize.width - 2 * margin, height: pageSize.height - 2 * margin
    )
    private let fonts: Fonts

    init(plan: PDFDocumentPlan, context: CGContext?, tocPageNumbers: [Int]?) {
        self.plan = plan
        self.context = context
        self.tocPageNumbers = tocPageNumbers
        self.fonts = Fonts(language: plan.language)
    }

    /// Draws everything and returns the first page (1-based) of each chapter.
    func render() throws -> [Int] {
        if let cover = plan.cover { drawCover(cover) }
        drawInfo()
        if plan.showTOC, !plan.chapters.isEmpty { drawTOC() }
        var starts: [Int] = []
        for chapter in plan.chapters {
            if Task.isCancelled { throw CancellationError() }
            beginPage()
            starts.append(pageCount)
            context?.addDestination(chapter.anchor as CFString, at: CGPoint(x: content.minX, y: content.maxY))
            if chapter.showTitle {
                drawText(attributed([PDFRun(text: chapter.title, bold: true, italic: false)], style: .chapterTitle))
            }
            drawBlocks(chapter.blocks)
        }
        endPage()
        return starts
    }

    // MARK: - Pages

    private func beginPage(numbered: Bool = true) {
        endPage()
        pageCount += 1
        pageOpen = true
        cursor = 0
        guard let context else { return }
        context.beginPDFPage(nil)
        context.textMatrix = .identity
        if numbered && plan.pageNumbers {
            let line = CTLineCreateWithAttributedString(attributed([PDFRun(text: "\(pageCount)", bold: false, italic: false)], style: .footer))
            let width = CTLineGetTypographicBounds(line, nil, nil, nil)
            context.textPosition = CGPoint(x: (Self.pageSize.width - width) / 2, y: Self.margin / 2)
            CTLineDraw(line, context)
        }
    }

    private func endPage() {
        guard pageOpen else { return }
        context?.endPDFPage()
        pageOpen = false
    }

    private func ensurePage() {
        if !pageOpen { beginPage() }
    }

    private var remaining: CGFloat { content.height - cursor }

    // MARK: - Sections

    private func drawCover(_ data: Data) {
        beginPage(numbered: false)
        guard let size = PDFImages.pixelSize(data), size.width > 0, size.height > 0 else { return }
        let scale = min(content.width / size.width, content.height / size.height)
        let width = size.width * scale, height = size.height * scale
        let rect = CGRect(x: content.midX - width / 2, y: content.midY - height / 2, width: width, height: height)
        if let context, let image = PDFImages.drawable(data, quality: plan.jpegQuality) {
            context.draw(image, in: rect)
        }
    }

    private func drawInfo() {
        beginPage()
        let text = NSMutableAttributedString()
        text.append(attributed([PDFRun(text: plan.title, bold: true, italic: false)], style: .bookTitle))
        if let author = plan.author, !author.isEmpty {
            text.append(attributed([PDFRun(text: author, bold: false, italic: false)], style: .author))
        }
        for detail in plan.details {
            text.append(attributed([PDFRun(text: detail, bold: false, italic: false)], style: .detail))
        }
        drawText(text)
        if let synopsis = plan.synopsis, !synopsis.isEmpty {
            cursor += 12
            let paragraphs = synopsis.components(separatedBy: .newlines).filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
            let body = NSMutableAttributedString()
            for paragraph in paragraphs {
                body.append(attributed([PDFRun(text: paragraph, bold: false, italic: false)], style: .body))
            }
            drawText(body)
        }
    }

    private func drawTOC() {
        beginPage()
        drawText(attributed([PDFRun(text: "Table of Contents", bold: true, italic: false)], style: .tocTitle))
        let lineHeight: CGFloat = 18
        let ascent = CTFontGetAscent(fonts.body)
        for (index, chapter) in plan.chapters.enumerated() {
            if remaining < lineHeight { beginPage() }
            defer { cursor += lineHeight }
            guard let context else { continue }
            let baseline = content.maxY - cursor - ascent
            var titleWidth = content.width
            var numberLine: CTLine?
            var numberWidth: CGFloat = 0
            if let numbers = tocPageNumbers, index < numbers.count {
                let line = CTLineCreateWithAttributedString(attributed([PDFRun(text: "\(numbers[index])", bold: false, italic: false)], style: .tocEntry))
                numberWidth = CGFloat(CTLineGetTypographicBounds(line, nil, nil, nil))
                numberLine = line
                titleWidth = content.width - numberWidth - 16
            }
            let fullTitle = CTLineCreateWithAttributedString(attributed([PDFRun(text: chapter.title, bold: false, italic: false)], style: .tocEntry))
            let ellipsis = CTLineCreateWithAttributedString(attributed([PDFRun(text: "\u{2026}", bold: false, italic: false)], style: .tocEntry))
            let titleLine = CTLineCreateTruncatedLine(fullTitle, Double(titleWidth), .end, ellipsis) ?? fullTitle
            let drawnTitleWidth = CGFloat(CTLineGetTypographicBounds(titleLine, nil, nil, nil))
            context.textPosition = CGPoint(x: content.minX, y: baseline)
            CTLineDraw(titleLine, context)
            if let numberLine {
                let dot = CTLineCreateWithAttributedString(attributed([PDFRun(text: " .", bold: false, italic: false)], style: .tocEntry))
                let dotWidth = max(1, CGFloat(CTLineGetTypographicBounds(dot, nil, nil, nil)))
                let gapStart = content.minX + drawnTitleWidth + 4
                let gapEnd = content.maxX - numberWidth - 4
                let count = Int((gapEnd - gapStart) / dotWidth)
                if count > 0 {
                    let leaders = CTLineCreateWithAttributedString(attributed([PDFRun(text: String(repeating: " .", count: count), bold: false, italic: false)], style: .tocEntry))
                    let leadersWidth = CGFloat(CTLineGetTypographicBounds(leaders, nil, nil, nil))
                    context.textPosition = CGPoint(x: gapEnd - leadersWidth, y: baseline)
                    CTLineDraw(leaders, context)
                }
                context.textPosition = CGPoint(x: content.maxX - numberWidth, y: baseline)
                CTLineDraw(numberLine, context)
            }
            let rect = CGRect(x: content.minX, y: content.maxY - cursor - lineHeight, width: content.width, height: lineHeight)
            context.setDestination(chapter.anchor as CFString, for: rect)
        }
    }

    private func drawBlocks(_ blocks: [PDFBlock]) {
        let pending = NSMutableAttributedString()
        func flushText() {
            guard pending.length > 0 else { return }
            drawText(pending)
            pending.setAttributedString(NSAttributedString())
        }
        for block in blocks {
            switch block {
            case .paragraph(let runs, let heading):
                pending.append(attributed(runs, style: heading.map { $0 <= 2 ? .heading : .subheading } ?? .body))
            case .image(let src):
                flushText()
                if let data = plan.image(for: src) { drawImage(data) }
            }
        }
        flushText()
    }

    // MARK: - Text

    /// Flows text from the cursor, breaking onto new pages as needed.
    private func drawText(_ text: NSAttributedString) {
        let length = text.length
        guard length > 0 else { return }
        let framesetter = CTFramesetterCreateWithAttributedString(text as CFAttributedString)
        var location = 0
        var emptyAttempts = 0
        while location < length {
            ensurePage()
            if remaining < 4 { beginPage() }
            let rect = CGRect(x: content.minX, y: content.minY, width: content.width, height: remaining)
            let frame = CTFramesetterCreateFrame(framesetter, CFRange(location: location, length: 0), CGPath(rect: rect, transform: nil), nil)
            let visible = CTFrameGetVisibleStringRange(frame)
            if visible.length == 0 {
                emptyAttempts += 1
                if emptyAttempts > 1 {
                    // Not even one line fits on an empty page; skip a character rather than loop.
                    location += 1
                    emptyAttempts = 0
                } else {
                    beginPage()
                }
                continue
            }
            emptyAttempts = 0
            if let context {
                context.textMatrix = .identity
                CTFrameDraw(frame, context)
            }
            location += visible.length
            if location < length {
                beginPage()
            } else {
                cursor += usedHeight(frame, height: rect.height)
            }
        }
    }

    private func usedHeight(_ frame: CTFrame, height: CGFloat) -> CGFloat {
        let lines = CTFrameGetLines(frame) as? [CTLine] ?? []
        guard let last = lines.last else { return 0 }
        var origin = CGPoint.zero
        CTFrameGetLineOrigins(frame, CFRange(location: lines.count - 1, length: 1), &origin)
        var descent: CGFloat = 0
        var leading: CGFloat = 0
        _ = CTLineGetTypographicBounds(last, nil, &descent, &leading)
        return min(height, height - origin.y + descent + leading + 6)
    }

    // MARK: - Images

    private func drawImage(_ data: Data) {
        guard let size = PDFImages.pixelSize(data), size.width > 0, size.height > 0 else { return }
        ensurePage()
        if plan.longImageLayout {
            drawStrip(data, pixelSize: size)
            return
        }
        // CSS px (96 dpi) to points, never wider than the text column or taller than a page.
        var width = min(content.width, size.width * 0.75)
        var height = size.height * width / size.width
        if height > content.height {
            width *= content.height / height
            height = content.height
        }
        if height > remaining { beginPage() }
        let rect = CGRect(x: content.midX - width / 2, y: content.maxY - cursor - height, width: width, height: height)
        if let context, let image = PDFImages.drawable(data, quality: plan.jpegQuality) {
            context.draw(image, in: rect)
        }
        cursor += height + 6
    }

    /// Full-width strip, sliced across pages with no gaps.
    private func drawStrip(_ data: Data, pixelSize size: CGSize) {
        let width = content.width
        let height = size.height * width / size.width
        if height <= remaining || (cursor == 0 && height <= content.height) {
            let rect = CGRect(x: content.minX, y: content.maxY - cursor - height, width: width, height: height)
            if let context, let image = PDFImages.drawable(data, quality: plan.jpegQuality) {
                context.draw(image, in: rect)
            }
            cursor += height
            return
        }
        let source = context == nil ? nil : PDFImages.decoded(data)
        var drawn: CGFloat = 0
        while drawn < height - 0.5 {
            if remaining < 1 { beginPage() }
            let slice = min(remaining, height - drawn)
            if let context, let source {
                let top = Int((drawn / height * size.height).rounded(.down))
                let bottom = min(Int(size.height), Int(((drawn + slice) / height * size.height).rounded(.up)))
                if bottom > top,
                   let cropped = source.cropping(to: CGRect(x: 0, y: top, width: Int(size.width), height: bottom - top)),
                   let image = PDFImages.jpegBacked(cropped, quality: plan.jpegQuality) {
                    let rect = CGRect(x: content.minX, y: content.maxY - cursor - slice, width: width, height: slice)
                    context.draw(image, in: rect)
                }
            }
            cursor += slice
            drawn += slice
        }
    }

    // MARK: - Styles

    enum Style {
        case body, heading, subheading, chapterTitle, bookTitle, author, detail, tocTitle, tocEntry, footer
    }

    struct Fonts {
        let body: CTFont
        let language: String

        init(language: String) {
            self.language = language
            body = CTFontCreateUIFontForLanguage(.system, 11, language as CFString)
                ?? CTFontCreateWithName("Helvetica" as CFString, 11, nil)
        }

        func font(size: CGFloat, bold: Bool, italic: Bool) -> CTFont {
            let base = CTFontCreateCopyWithAttributes(body, size, nil, nil)
            var traits: CTFontSymbolicTraits = []
            if bold { traits.insert(.traitBold) }
            if italic { traits.insert(.traitItalic) }
            guard !traits.isEmpty else { return base }
            return CTFontCreateCopyWithSymbolicTraits(base, size, nil, traits, traits) ?? base
        }
    }

    private func attributed(_ runs: [PDFRun], style: Style) -> NSAttributedString {
        let size: CGFloat
        var alignment = CTTextAlignment.natural
        var spacingAfter: CGFloat = 6
        var forceBold = false
        var gray: CGFloat = 0
        switch style {
        case .body: size = 11
        case .heading: size = 16; forceBold = true; spacingAfter = 10
        case .subheading: size = 13; forceBold = true; spacingAfter = 8
        case .chapterTitle: size = 18; forceBold = true; spacingAfter = 18
        case .bookTitle: size = 22; forceBold = true; alignment = .center; spacingAfter = 12
        case .author: size = 13; alignment = .center; spacingAfter = 8
        case .detail: size = 10; alignment = .center; spacingAfter = 4; gray = 0.35
        case .tocTitle: size = 16; forceBold = true; alignment = .center; spacingAfter = 14
        case .tocEntry: size = 11; spacingAfter = 0
        case .footer: size = 9; gray = 0.4
        }
        let paragraph = Self.paragraphStyle(alignment: alignment, spacingAfter: spacingAfter, lineSpacing: style == .body ? 3 : 1)
        let color = CGColor(gray: gray, alpha: 1)
        let output = NSMutableAttributedString()
        for run in runs {
            let font = fonts.font(size: size, bold: forceBold || run.bold, italic: run.italic)
            output.append(NSAttributedString(string: run.text, attributes: [
                NSAttributedString.Key(kCTFontAttributeName as String): font,
                NSAttributedString.Key(kCTForegroundColorAttributeName as String): color,
                NSAttributedString.Key(kCTParagraphStyleAttributeName as String): paragraph,
            ]))
        }
        if style != .tocEntry && style != .footer {
            output.append(NSAttributedString(string: "\n", attributes: [
                NSAttributedString.Key(kCTFontAttributeName as String): fonts.font(size: size, bold: false, italic: false),
                NSAttributedString.Key(kCTParagraphStyleAttributeName as String): paragraph,
            ]))
        }
        return output
    }

    private static func paragraphStyle(alignment: CTTextAlignment, spacingAfter: CGFloat, lineSpacing: CGFloat) -> CTParagraphStyle {
        var alignment = alignment
        var spacingAfter = spacingAfter
        var lineSpacing = lineSpacing
        return withUnsafeBytes(of: &alignment) { alignmentBytes in
            withUnsafeBytes(of: &spacingAfter) { afterBytes in
                withUnsafeBytes(of: &lineSpacing) { lineBytes in
                    let settings = [
                        CTParagraphStyleSetting(spec: .alignment, valueSize: MemoryLayout<CTTextAlignment>.size, value: alignmentBytes.baseAddress!),
                        CTParagraphStyleSetting(spec: .paragraphSpacing, valueSize: MemoryLayout<CGFloat>.size, value: afterBytes.baseAddress!),
                        CTParagraphStyleSetting(spec: .lineSpacingAdjustment, valueSize: MemoryLayout<CGFloat>.size, value: lineBytes.baseAddress!),
                    ]
                    return CTParagraphStyleCreate(settings, settings.count)
                }
            }
        }
    }
}

/// Image helpers that keep PDFs small: JPEG and PNG sources are embedded as they are
/// (Quartz passes JPEG data through); WebP, GIF, and AVIF are converted to JPEG first.
enum PDFImages {
    static func pixelSize(_ data: Data) -> CGSize? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = props[kCGImagePropertyPixelWidth] as? Int,
              let height = props[kCGImagePropertyPixelHeight] as? Int else { return nil }
        return CGSize(width: width, height: height)
    }

    static func decoded(_ data: Data) -> CGImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        return CGImageSourceCreateImageAtIndex(source, 0, nil)
    }

    static func drawable(_ data: Data, quality: Int) -> CGImage? {
        let bytes = [UInt8](data.prefix(8))
        let isJPEG = bytes.starts(with: [0xFF, 0xD8, 0xFF])
        let isPNG = bytes.starts(with: [0x89, 0x50, 0x4E, 0x47])
        guard let image = decoded(data) else { return nil }
        if isJPEG || isPNG { return image }
        return jpegBacked(image, quality: quality) ?? image
    }

    /// Re-encodes as JPEG (on white) so Quartz embeds compressed data instead of raw pixels.
    static func jpegBacked(_ image: CGImage, quality: Int) -> CGImage? {
        var opaque = image
        switch image.alphaInfo {
        case .none, .noneSkipFirst, .noneSkipLast:
            break
        default:
            let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
            guard let context = CGContext(
                data: nil, width: image.width, height: image.height, bitsPerComponent: 8, bytesPerRow: 0,
                space: colorSpace, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
            ) else { return nil }
            let rect = CGRect(x: 0, y: 0, width: image.width, height: image.height)
            context.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
            context.fill(rect)
            context.draw(image, in: rect)
            guard let flattened = context.makeImage() else { return nil }
            opaque = flattened
        }
        let output = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(output, "public.jpeg" as CFString, 1, nil) else { return nil }
        let props = [kCGImageDestinationLossyCompressionQuality: Double(min(100, max(10, quality))) / 100] as CFDictionary
        CGImageDestinationAddImage(destination, opaque, props)
        guard CGImageDestinationFinalize(destination) else { return nil }
        return decoded(output as Data)
    }
}
