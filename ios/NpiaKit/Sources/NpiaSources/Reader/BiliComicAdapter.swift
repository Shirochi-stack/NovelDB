import CommonCrypto
import Foundation
import NpiaCore

/// Bilibili Manga: the desktop's `bilicomic` engine (the site's app API) in the Python engines' page.
///
/// The engine returns a chapter's page addresses, which the site signs for about fifteen minutes. The
/// image step runs after every chapter is read, far later than that, so a download fetches a chapter's
/// pages as soon as the chapter is read (`BiliComicDownload`) and the chapter carries their bytes.
/// Each page is reported as it arrives (`SourceContext.onImage`), so the log shows the pages coming in
/// between the chapter lines; the other chapters' addresses are read meanwhile.
/// The Catalog reader shows a chapter at once and takes the addresses (`parseChapter`).
///
/// A chapter the signed-in account has not bought is refused; nothing is bought. Android's
/// `BiliComicAdapter` works the same way.
struct BiliComicAdapter: SourceAdapter {
    static let id: SourceID = .bilicomic
    static let tier: SourceTier = .lightBrowser
    static let sites: Set<String> = ["manga.bilibili.com"]

    static func matches(_ url: URL) -> Bool { PythonEngines.biliComicMatches(url) }

    func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        try await ReaderSessions.shared.openBuiltIn(url, context: context, source: Self.id).book
    }

    /// The chapter with its page addresses only.
    func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        let session = try await ReaderSessions.shared.session(for: book, context: context)
        return try await session.chapter(ref)
    }

    func parseChapters(_ refs: [ChapterRef], book: BookInfo, context: SourceContext) async throws -> [ChapterResult] {
        try await BiliComicDownload().parseChaptersSpaced(refs, book: book, context: context)
    }
}

/// A download's reading of a chapter: its page addresses, then the pages themselves.
struct BiliComicDownload: SourceAdapter {
    static let id: SourceID = .bilicomic
    static let tier: SourceTier = .lightBrowser

    static func matches(_ url: URL) -> Bool { false }

    func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        try await BiliComicAdapter().parseBook(url, context: context)
    }

    func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        let result = try await BiliComicAdapter().parseChapter(ref, book: book, context: context)
        return try await BiliComicPages.attach(to: result, context: context)
    }
}

/// A chapter's pages: fetched from their signed addresses, and decoded when the site protects them
/// ("cpx" in the address; the desktop's `decrypt_page`).
enum BiliComicPages {
    enum Failure: Error {
        case notAnImage
        case layout
        case key
        case cipher(Int32)
    }

    /// The encrypted start of a protected page; the rest is stored as it is.
    private static let head = 20496

    /// `result` with every page's bytes; `.failed` when a page cannot be fetched (the chapter is read again).
    static func attach(to result: ChapterResult, context: SourceContext) async throws -> ChapterResult {
        guard case .content(var content) = result, content.images.contains(where: { $0.data == nil }) else { return result }
        let images = content.images
        let ref = content.ref
        let limit = max(1, min(6, context.shared(for: .bilicomic).imageWorkers))
        var fetched = [Data?](repeating: nil, count: images.count)
        var next = 0
        func start(_ index: Int, in group: inout ThrowingTaskGroup<(Int, Data?), Error>) {
            let image = images[index]
            group.addTask {
                let began = ProcessInfo.processInfo.systemUptime
                let data = try await BiliComicPages.page(image, context: context)
                // Reported as it arrives: the log shows the pages coming in while the chapters are read.
                if let data, image.data == nil {
                    context.onImage(ref, index, images.count, image.filename, data.count, ProcessInfo.processInfo.systemUptime - began)
                }
                return (index, data)
            }
        }
        try await withThrowingTaskGroup(of: (Int, Data?).self) { group in
            while next < min(limit, images.count) {
                start(next, in: &group)
                next += 1
            }
            while let (index, data) = try await group.next() {
                fetched[index] = data
                if next < images.count {
                    start(next, in: &group)
                    next += 1
                }
            }
        }
        if let missing = fetched.firstIndex(where: { $0 == nil }) {
            return .failed(content.ref, message: "Page \(missing + 1) of \(images.count) could not be downloaded.")
        }
        for index in content.images.indices {
            content.images[index].data = fetched[index]
        }
        return .content(content)
    }

    /// One page's image bytes, or nil: two tries (the image host needs no sign-in).
    static func page(_ image: ImageRef, context: SourceContext) async throws -> Data? {
        if let data = image.data { return data }
        for attempt in 0..<2 {
            try Task.checkCancellation()
            if let response = try? await context.http.get(image.url, headers: ["Referer": "https://manga.bilibili.com/"]),
               (200..<300).contains(response.status), !response.body.isEmpty,
               let decoded = try? decode(response.body, url: image.url) {
                return mapped(decoded)
            }
            if attempt == 0 { try await Task.sleep(nanoseconds: 1_000_000_000) }
        }
        return nil
    }

    /// The bytes backed by a file instead of memory: a book's pages are gigabytes, and they wait for
    /// the image step. The file is unlinked at once; its space is freed when the bytes are let go.
    static func mapped(_ data: Data) -> Data {
        let file = FileManager.default.temporaryDirectory.appendingPathComponent("nd-bilicomic-\(UUID().uuidString)")
        guard (try? data.write(to: file)) != nil else { return data }
        defer { try? FileManager.default.removeItem(at: file) }
        return (try? Data(contentsOf: file, options: .alwaysMapped)) ?? data
    }

    static func isImage(_ data: Data) -> Bool {
        let start = [UInt8](data.prefix(12))
        func has(_ bytes: [UInt8], at offset: Int = 0) -> Bool {
            start.count >= offset + bytes.count && Array(start[offset..<(offset + bytes.count)]) == bytes
        }
        return has([0xFF, 0xD8, 0xFF]) || has([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
            || (has([0x52, 0x49, 0x46, 0x46]) && has([0x57, 0x45, 0x42, 0x50], at: 8))
            || has([0x47, 0x49, 0x46, 0x38]) || has([0x66, 0x74, 0x79, 0x70, 0x61, 0x76, 0x69], at: 4)
    }

    /// The image in `data`. Throws when the bytes are neither an image nor a protected page that decodes to one.
    static func decode(_ data: Data, url: URL) throws -> Data {
        if isImage(data) { return data }
        guard let cpx = cpx(url) else { throw Failure.notAnImage }
        // 0x01, the image's length (4 bytes, big-endian), the image with its first 20496 bytes AES-CBC
        // encrypted, then the key. The IV is bytes 60-76 of the decoded "cpx" value.
        let bytes = [UInt8](data)
        guard bytes.count >= 5, bytes[0] == 1 else { throw Failure.layout }
        let length = (Int(bytes[1]) << 24) | (Int(bytes[2]) << 16) | (Int(bytes[3]) << 8) | Int(bytes[4])
        let end = 5 + length
        guard length > 0, end <= bytes.count else { throw Failure.layout }
        let key = Array(bytes[end..<bytes.count])
        let count = min(head, length)
        guard cpx.count >= 76, [kCCKeySizeAES128, kCCKeySizeAES192, kCCKeySizeAES256].contains(key.count),
              count % kCCBlockSizeAES128 == 0 else { throw Failure.key }
        let iv = Array(cpx[60..<76])
        let input = Array(bytes[5..<(5 + count)])
        var plain = [UInt8](repeating: 0, count: count + kCCBlockSizeAES128)
        let capacity = plain.count
        var moved = 0
        let status = CCCrypt(CCOperation(kCCDecrypt), CCAlgorithm(kCCAlgorithmAES), CCOptions(0),
                             key, key.count, iv, input, input.count, &plain, capacity, &moved)
        guard Int(status) == kCCSuccess else { throw Failure.cipher(status) }
        plain = Array(plain[0..<moved])
        if let pad = plain.last, pad >= 1, pad <= 16, plain.count >= Int(pad),
           plain[(plain.count - Int(pad))..<plain.count].allSatisfy({ $0 == pad }) {
            plain.removeLast(Int(pad))
        }
        let image = Data(plain + Array(bytes[(5 + count)..<end]))
        guard isImage(image) else { throw Failure.notAnImage }
        return image
    }

    /// The decoded "cpx" value of a page address, or nil when it has none.
    static func cpx(_ url: URL) -> [UInt8]? {
        guard let query = URLComponents(url: url, resolvingAgainstBaseURL: false)?.percentEncodedQuery,
              let item = query.split(separator: "&").first(where: { $0.hasPrefix("cpx=") }) else { return nil }
        let raw = String(item.dropFirst(4))
        guard !raw.isEmpty else { return nil }
        let allowed = Set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")
        let text = String((raw.removingPercentEncoding ?? raw).filter { allowed.contains($0) })
        let padded = text + String(repeating: "=", count: (4 - text.count % 4) % 4)
        guard let decoded = Data(base64Encoded: padded) else { return nil }
        return [UInt8](decoded)
    }
}
