import Foundation

public enum OutputFormat: String, Codable, CaseIterable, Sendable {
    case epub, txt, pdf, html, cbz, folder, zip

    public var label: String { self == .folder ? "Folder" : rawValue.uppercased() }
}

public enum ImageFormat: String, Codable, CaseIterable, Sendable {
    case webp = "WEBP"
    case jpeg = "JPEG"
    case png = "PNG"
    case original = "ORIGINAL"
}

public enum NamingMode: String, Codable, CaseIterable, Sendable {
    case title, id
}

/// Settings both desktop downloaders had, under one name. Per-source differences go in
/// `AppSettings.overrides`.
public struct SharedSettings: Codable, Sendable, Equatable {
    public var threads = 4
    public var intervalMin = 2.0
    public var intervalMax = 5.0
    public var maxRetries = 10
    /// How long a request waits for the server, at least (sources that need longer keep their own); 10...600 s.
    public var requestTimeoutSeconds = SharedSettings.defaultTimeoutSeconds
    /// How long a hidden browser page may take to load, and waits for its content may last, at least; 10...600 s.
    public var pageLoadTimeoutSeconds = SharedSettings.defaultTimeoutSeconds
    public var formats: [OutputFormat] = [.epub]
    public var rangeFrom: Int?
    public var rangeTo: Int?
    public var useCache = true
    public var cacheImages = true
    public var numberChapters = false
    public var separateTXT = false
    public var skipPaid = false
    public var imageWorkers = 6
    public var generateOnStop = false
    public var chapterTiming = true
    public var namingMode: NamingMode = .title
    public var appendRange = false
    /// Auto purchase: paid chapters inside `purchaseFrom...purchaseTo` are unlocked with the account's
    /// free tickets (a gift, a wait-for-free ticket) before they are downloaded. Never spends cash;
    /// without a range nothing is purchased.
    public var autoPurchase = false
    public var purchaseFrom: Int?
    public var purchaseTo: Int?
    /// A comma list typed in a range field ("4,7", "1-3,9"), kept beside the range it spans
    /// (`rangeFrom...rangeTo`, `purchaseFrom...purchaseTo`); "" for a plain range.
    public var rangeList = ""
    public var purchaseList = ""

    public init() {}

    /// The request timeout and page load wait the app starts with, in seconds (Settings > Network).
    public static let defaultTimeoutSeconds = 60

    private enum CodingKeys: String, CodingKey {
        case threads, intervalMin, intervalMax, maxRetries, requestTimeoutSeconds, pageLoadTimeoutSeconds, formats
        case rangeFrom, rangeTo, useCache, cacheImages, numberChapters, separateTXT, skipPaid, imageWorkers
        case generateOnStop, chapterTiming, namingMode, appendRange, autoPurchase, purchaseFrom, purchaseTo
        case rangeList, purchaseList
    }

    /// Every key is optional, so settings stored before a field existed (the queue keeps a copy
    /// with each job) still decode.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        threads = try c.decodeIfPresent(Int.self, forKey: .threads) ?? threads
        intervalMin = try c.decodeIfPresent(Double.self, forKey: .intervalMin) ?? intervalMin
        intervalMax = try c.decodeIfPresent(Double.self, forKey: .intervalMax) ?? intervalMax
        maxRetries = try c.decodeIfPresent(Int.self, forKey: .maxRetries) ?? maxRetries
        requestTimeoutSeconds = try c.decodeIfPresent(Int.self, forKey: .requestTimeoutSeconds) ?? requestTimeoutSeconds
        pageLoadTimeoutSeconds = try c.decodeIfPresent(Int.self, forKey: .pageLoadTimeoutSeconds) ?? pageLoadTimeoutSeconds
        formats = try c.decodeIfPresent([OutputFormat].self, forKey: .formats) ?? formats
        rangeFrom = try c.decodeIfPresent(Int.self, forKey: .rangeFrom)
        rangeTo = try c.decodeIfPresent(Int.self, forKey: .rangeTo)
        useCache = try c.decodeIfPresent(Bool.self, forKey: .useCache) ?? useCache
        cacheImages = try c.decodeIfPresent(Bool.self, forKey: .cacheImages) ?? cacheImages
        numberChapters = try c.decodeIfPresent(Bool.self, forKey: .numberChapters) ?? numberChapters
        separateTXT = try c.decodeIfPresent(Bool.self, forKey: .separateTXT) ?? separateTXT
        skipPaid = try c.decodeIfPresent(Bool.self, forKey: .skipPaid) ?? skipPaid
        imageWorkers = try c.decodeIfPresent(Int.self, forKey: .imageWorkers) ?? imageWorkers
        chapterTiming = try c.decodeIfPresent(Bool.self, forKey: .chapterTiming) ?? chapterTiming
        generateOnStop = try c.decodeIfPresent(Bool.self, forKey: .generateOnStop) ?? generateOnStop
        namingMode = try c.decodeIfPresent(NamingMode.self, forKey: .namingMode) ?? namingMode
        appendRange = try c.decodeIfPresent(Bool.self, forKey: .appendRange) ?? appendRange
        autoPurchase = try c.decodeIfPresent(Bool.self, forKey: .autoPurchase) ?? autoPurchase
        purchaseFrom = try c.decodeIfPresent(Int.self, forKey: .purchaseFrom)
        purchaseTo = try c.decodeIfPresent(Int.self, forKey: .purchaseTo)
        rangeList = try c.decodeIfPresent(String.self, forKey: .rangeList) ?? rangeList
        purchaseList = try c.decodeIfPresent(String.self, forKey: .purchaseList) ?? purchaseList
    }

    /// Desktop `_normalize_interval_range`: each bound clamped to 0...300 s, swapped when
    /// the maximum is below the minimum.
    public var normalizedInterval: ClosedRange<Double> {
        func clamp(_ value: Double) -> Double { value.isFinite ? Swift.max(0, Swift.min(300, value)) : 0 }
        let low = clamp(intervalMin), high = clamp(intervalMax)
        return Swift.min(low, high)...Swift.max(low, high)
    }

    /// One random chapter delay in seconds (desktop `_random_interval_delay`); fixed when
    /// min equals max.
    public func chapterDelay() -> Double {
        let range = normalizedInterval
        guard range.upperBound > 0 else { return 0 }
        return range.lowerBound == range.upperBound ? range.lowerBound : Double.random(in: range)
    }

    /// Sleeps one chapter delay (desktop `_sleep_interval`), honouring cancellation.
    public func sleepChapterDelay() async throws {
        let delay = chapterDelay()
        if delay > 0 { try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000)) }
    }
}

/// Optional replacements for `SharedSettings` fields on one source; nil keeps the shared value.
public struct SharedOverrides: Codable, Sendable, Equatable {
    public var threads: Int?
    public var intervalMin: Double?
    public var intervalMax: Double?
    public var formats: [OutputFormat]?
    public var rangeFrom: Int?
    public var rangeTo: Int?
    public var useCache: Bool?
    public var cacheImages: Bool?
    public var numberChapters: Bool?
    public var separateTXT: Bool?

    public init() {}

    public var isEmpty: Bool { self == SharedOverrides() }

    func apply(to base: SharedSettings) -> SharedSettings {
        var value = base
        if let threads { value.threads = threads }
        if let intervalMin { value.intervalMin = intervalMin }
        if let intervalMax { value.intervalMax = intervalMax }
        if let formats { value.formats = formats }
        if let rangeFrom { value.rangeFrom = rangeFrom }
        if let rangeTo { value.rangeTo = rangeTo }
        if let useCache { value.useCache = useCache }
        if let cacheImages { value.cacheImages = cacheImages }
        if let numberChapters { value.numberChapters = numberChapters }
        if let separateTXT { value.separateTXT = separateTXT }
        return value
    }
}

public struct ImageSettings: Codable, Sendable, Equatable {
    // Optional on disk so settings saved before stitching still decode.
    public var webtoonExport: Bool?
    public var stitchImages: Bool?
    public var stitchHeight: Int?  // Zero means Unlimited.
    public var stitchChapterFolders: Bool?

    public var stitchingEnabled: Bool { stitchImages ?? true }
    public var stitchMaxHeight: Int { min(32_000, max(0, stitchHeight ?? 16_000)) }
    public var chapterFoldersEnabled: Bool { stitchChapterFolders ?? false }
    public var compressAfterStitch: Bool { webtoonExport == true && stitchingEnabled && compress && !saveImageURLsOnly }
    public var exportStitchHeight: Int {
        let limit: Int
        guard compressAfterStitch else { return stitchMaxHeight }
        switch format {
        case .webp: limit = 16_383
        case .jpeg: limit = 65_500
        default: return stitchMaxHeight
        }
        return min(stitchMaxHeight == 0 ? limit : stitchMaxHeight, limit)
    }

    public var compress = true
    public var quality = 90
    public var format: ImageFormat = .webp
    public var convertGIFs = false
    public var staticOnly = false
    public var recompressTargetMB: Double?
    public var compressCover = false
    public var coverQuality = 90
    public var coverFormat: ImageFormat = .jpeg
    public var compressionWorkers = 4
    public var saveImageURLsOnly = false

    public init() {}
}

public struct TextSettings: Codable, Sendable, Equatable {
    public var stripLeadingSpaces = false
    public var removeNewlines = false
    public var blankParagraphLines = false
    public var saveHTML = false
    public var pdfTOC = false
    public var pdfPageNumbers = false
    public var pdfCounterLayout = false

    public init() {}
}

public struct NpiaSettings: Codable, Sendable, Equatable {
    /// Search terms the "scrape all novels" sweep has (`NpiaSearchTerms.defaultQueryCount`).
    public static let maxSearchQueries = 165

    public var autoLogin = false
    public var includeNotices = true
    public var useBrowserFallback = true
    public var tagAgeFilter = "15"
    public var tagSelectedTags: [String] = []
    public var tagCustomTags = ""
    /// Query builder groups: tags inside a group are OR'd, the groups are AND'd.
    public var tagQueryGroups: [[String]] = []
    public var tagScrapeAll = false
    /// Search terms the sweep uses, 1...`maxSearchQueries`.
    public var scrapeMaxQueries = NpiaSettings.maxSearchQueries {
        didSet {
            let clamped = Self.clampQueries(scrapeMaxQueries)
            if clamped != scrapeMaxQueries { scrapeMaxQueries = clamped }
        }
    }

    public init() {}

    public static func clampQueries(_ value: Int) -> Int {
        max(1, min(value, maxSearchQueries))
    }

    private enum CodingKeys: String, CodingKey {
        case autoLogin, includeNotices, useBrowserFallback, tagAgeFilter, tagSelectedTags
        case tagCustomTags, tagQueryGroups, tagScrapeAll, scrapeMaxQueries
    }

    /// Every key is optional, so settings stored before a field existed still decode.
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        autoLogin = try container.decodeIfPresent(Bool.self, forKey: .autoLogin) ?? autoLogin
        includeNotices = try container.decodeIfPresent(Bool.self, forKey: .includeNotices) ?? includeNotices
        useBrowserFallback = try container.decodeIfPresent(Bool.self, forKey: .useBrowserFallback) ?? useBrowserFallback
        tagAgeFilter = try container.decodeIfPresent(String.self, forKey: .tagAgeFilter) ?? tagAgeFilter
        tagSelectedTags = try container.decodeIfPresent([String].self, forKey: .tagSelectedTags) ?? tagSelectedTags
        tagCustomTags = try container.decodeIfPresent(String.self, forKey: .tagCustomTags) ?? tagCustomTags
        tagQueryGroups = try container.decodeIfPresent([[String]].self, forKey: .tagQueryGroups) ?? tagQueryGroups
        tagScrapeAll = try container.decodeIfPresent(Bool.self, forKey: .tagScrapeAll) ?? tagScrapeAll
        let queries = try container.decodeIfPresent(Int.self, forKey: .scrapeMaxQueries) ?? scrapeMaxQueries
        scrapeMaxQueries = Self.clampQueries(queries)
    }
}

public struct KpageSettings: Codable, Sendable, Equatable {
    public var skipLastPage = false
    public var keepFiller = false
    public var dedupeImages = false
    public var dedupeLeadingImages = 2

    public init() {}
}

public struct SourceOptions: Codable, Sendable, Equatable {
    public var kpage = KpageSettings()
    /// ntk: use the npia cover when the novel exists there.
    public var ntkNpiaCover = false
    /// nweb: prefer the store cover image.
    public var nwebStoreCover = false
    public var koboHorizontalLayout = false

    public init() {}
}

public struct UploadSettings: Codable, Sendable, Equatable {
    /// The range "Largest ZIP" can take, in MB (the desktop and Android apps use the same one).
    public static let zipMaxMBRange = 50...4096
    public static let defaultZipMaxMB = 1024

    public var autoUpload = false
    /// `Uploader.id` values, tried in order.
    public var hosts: [String] = ["tempsh", "gofile"]
    public var googleDriveFolderID: String?
    public var copyLinkToClipboard = true
    /// Pack two or more files of one upload into ZIPs of at most `zipMaxMB` (`upload_zip_bundle`).
    public var zipBundle = true
    /// Largest ZIP in MB, 50...4096 (`upload_zip_max_mb`).
    public var zipMaxMB = UploadSettings.defaultZipMaxMB {
        didSet {
            let clamped = Self.clampZipMaxMB(zipMaxMB)
            if clamped != zipMaxMB { zipMaxMB = clamped }
        }
    }

    public init() {}

    public static func clampZipMaxMB(_ value: Int) -> Int {
        max(zipMaxMBRange.lowerBound, min(value, zipMaxMBRange.upperBound))
    }

    private enum CodingKeys: String, CodingKey {
        case autoUpload, hosts, googleDriveFolderID, copyLinkToClipboard, zipBundle, zipMaxMB
    }

    /// Every key is optional, so settings stored before a field existed (the queue keeps a copy
    /// with each job) still decode.
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        autoUpload = try container.decodeIfPresent(Bool.self, forKey: .autoUpload) ?? autoUpload
        hosts = try container.decodeIfPresent([String].self, forKey: .hosts) ?? hosts
        googleDriveFolderID = try container.decodeIfPresent(String.self, forKey: .googleDriveFolderID)
        copyLinkToClipboard = try container.decodeIfPresent(Bool.self, forKey: .copyLinkToClipboard) ?? copyLinkToClipboard
        zipBundle = try container.decodeIfPresent(Bool.self, forKey: .zipBundle) ?? zipBundle
        let megabytes = try container.decodeIfPresent(Int.self, forKey: .zipMaxMB) ?? zipMaxMB
        zipMaxMB = Self.clampZipMaxMB(megabytes)
    }
}

public struct AppSettings: Codable, Sendable, Equatable {
    public var schemaVersion = 1
    public var shared = SharedSettings()
    /// Keyed by `SourceID.rawValue`.
    public var overrides: [String: SharedOverrides] = [:]
    public var images = ImageSettings()
    public var text = TextSettings()
    public var npia = NpiaSettings()
    public var sources = SourceOptions()
    public var upload = UploadSettings()
    /// Which site engines run, in what order, and which rule engines update themselves.
    public var engines = EngineSettings()
    /// Look for a newer build of the app itself at start and when it returns to the foreground,
    /// at most once a day. On unless the user turns it off.
    public var appUpdateCheck = true

    public init() {}

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, shared, overrides, images, text, npia, sources, upload, engines, appUpdateCheck
    }

    /// Every key is optional, so settings stored before a field existed (the queue keeps a copy
    /// with each job) still decode.
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? schemaVersion
        shared = try container.decodeIfPresent(SharedSettings.self, forKey: .shared) ?? shared
        overrides = try container.decodeIfPresent([String: SharedOverrides].self, forKey: .overrides) ?? overrides
        images = try container.decodeIfPresent(ImageSettings.self, forKey: .images) ?? images
        text = try container.decodeIfPresent(TextSettings.self, forKey: .text) ?? text
        npia = try container.decodeIfPresent(NpiaSettings.self, forKey: .npia) ?? npia
        sources = try container.decodeIfPresent(SourceOptions.self, forKey: .sources) ?? sources
        upload = try container.decodeIfPresent(UploadSettings.self, forKey: .upload) ?? upload
        engines = try container.decodeIfPresent(EngineSettings.self, forKey: .engines) ?? engines
        appUpdateCheck = try container.decodeIfPresent(Bool.self, forKey: .appUpdateCheck) ?? appUpdateCheck
    }

    public func effective(for source: SourceID) -> SharedSettings {
        overrides[source.rawValue]?.apply(to: shared) ?? shared
    }
}

extension SharedSettings {
    /// The chapters the range keeps (unset ends open). A "From" after "To" is a typo, so it keeps
    /// just the "From" chapter rather than nothing.
    public var chapterRange: ClosedRange<Int> {
        let from = rangeFrom ?? Int.min
        let to = rangeTo ?? Int.max
        if let rangeFrom, let rangeTo, rangeFrom > rangeTo { return from...from }
        return from...to
    }

    /// The chapters auto purchase may unlock, or nil when it is off or has no range.
    public var purchaseRange: ClosedRange<Int>? {
        guard autoPurchase, purchaseFrom != nil || purchaseTo != nil else { return nil }
        let from = purchaseFrom ?? 1
        return from...Swift.max(purchaseTo ?? Int.max, from)
    }

    /// True when the chapter range (and its comma list) keeps chapter `index`.
    public func keepsChapter(_ index: Int) -> Bool {
        chapterRange.contains(index) && ChapterRangeText.inList(from: rangeFrom, to: rangeTo, list: rangeList, index: index)
    }

    /// True when auto purchase may unlock chapter `index`.
    public func purchasesChapter(_ index: Int) -> Bool {
        guard let range = purchaseRange else { return false }
        return range.contains(index) && ChapterRangeText.inList(from: purchaseFrom, to: purchaseTo, list: purchaseList, index: index)
    }
}

/// A chapter range typed in one field, the way a print dialog takes pages: `4` or `4-7` (desktop
/// `noveldb.core.chapter_range`, Android `ChapterRangeText`). Text that is not a range keeps its
/// first number: `7-3` is chapter 7 and `3-` chapter 3. No number at all is no range.
public enum ChapterRangeText {
    /// The first and last chapter `text` names, or nil for both when it names none.
    public static func parse(_ text: String) -> (from: Int?, to: Int?) {
        let scalars = Array(text.unicodeScalars)
        func isDigit(_ scalar: Unicode.Scalar) -> Bool { scalar.value >= 48 && scalar.value <= 57 }
        func number(at start: Int) -> (value: Int?, end: Int) {
            var end = start
            var digits = ""
            while end < scalars.count, isDigit(scalars[end]) {
                digits.unicodeScalars.append(scalars[end])
                end += 1
            }
            return (Int(digits), end)
        }
        func skipSpaces(_ start: Int) -> Int {
            var index = start
            while index < scalars.count, scalars[index].properties.isWhitespace { index += 1 }
            return index
        }
        guard let start = scalars.firstIndex(where: isDigit) else { return (nil, nil) }
        let first = number(at: start)
        guard let low = first.value, low >= 1 else { return (nil, nil) }
        var high = low
        var index = skipSpaces(first.end)
        // A hyphen, an en dash, an em dash or a tilde joins the two numbers.
        if index < scalars.count, [0x2D, 0x2013, 0x2014, 0x7E].contains(scalars[index].value) {
            index = skipSpaces(index + 1)
            if index < scalars.count, isDigit(scalars[index]), let second = number(at: index).value {
                high = second
            }
        }
        return (low, Swift.max(high, low))
    }

    /// The field's text for a stored range. An open end (older settings) shows as `4-` or `1-7`.
    public static func text(from: Int?, to: Int?) -> String {
        switch (from, to) {
        case (nil, nil): return ""
        case (nil, let to?): return "1-\(to)"
        case (let from?, nil): return "\(from)-"
        case (let from?, let to?): return to <= from ? "\(from)" : "\(from)-\(to)"
        }
    }

    /// The range as file names and logs show it: `4`, `4-7`, `4-end`; "" for no range.
    public static func label(from: Int?, to: Int?) -> String {
        if from == nil, to == nil { return "" }
        let low = from ?? 1
        guard let to else { return "\(low)-end" }
        return to <= low ? "\(low)" : "\(low)-\(to)"
    }

    /// What a typed field means: the span it names and, for separate ranges (`4,7`), their comma
    /// list (else "").
    public struct Typed: Equatable, Sendable {
        public var from: Int?
        public var to: Int?
        public var list: String

        public init(from: Int?, to: Int?, list: String) {
            self.from = from
            self.to = to
            self.list = list
        }
    }

    /// Every range in a comma list such as `4,7` or `1-3,9`, in order, with overlapping and
    /// touching ones joined. Each part is read like a single range.
    public static func parseList(_ text: String) -> [ClosedRange<Int>] {
        let separators: Set<Character> = [",", ";", "\u{FF0C}", "\u{3001}"]
        var ranges: [ClosedRange<Int>] = []
        for part in text.split(whereSeparator: { separators.contains($0) }) {
            let parsed = parse(String(part))
            if let low = parsed.from, let high = parsed.to { ranges.append(low...high) }
        }
        ranges.sort { ($0.lowerBound, $0.upperBound) < ($1.lowerBound, $1.upperBound) }
        var joined: [ClosedRange<Int>] = []
        for range in ranges {
            if let last = joined.last, range.lowerBound <= last.upperBound + 1 {
                joined[joined.count - 1] = last.lowerBound...Swift.max(last.upperBound, range.upperBound)
            } else {
                joined.append(range)
            }
        }
        return joined
    }

    /// `4,7` or `1-3,9` for two or more ranges; "" for one or none (a plain range needs no list).
    public static func listText(_ ranges: [ClosedRange<Int>]) -> String {
        guard ranges.count > 1 else { return "" }
        return ranges.map { text(from: $0.lowerBound, to: $0.upperBound) }.joined(separator: ",")
    }

    public static func read(_ text: String) -> Typed {
        let ranges = parseList(text)
        guard let first = ranges.first, let last = ranges.last else { return Typed(from: nil, to: nil, list: "") }
        return Typed(from: first.lowerBound, to: last.upperBound, list: listText(ranges))
    }

    /// The ranges of `list` when it belongs to `from...to`. It is stored beside the range and is
    /// stale once the range was changed without it; then it counts for nothing.
    private static func listed(from: Int?, to: Int?, list: String) -> [ClosedRange<Int>] {
        let ranges = list.isEmpty ? [] : parseList(list)
        guard ranges.count > 1, ranges.first?.lowerBound == from, ranges.last?.upperBound == to else { return [] }
        return ranges
    }

    /// True when chapter `index` is named by the comma list; a range without a list names all of its span.
    public static func inList(from: Int?, to: Int?, list: String, index: Int) -> Bool {
        let ranges = listed(from: from, to: to, list: list)
        return ranges.isEmpty || ranges.contains { $0.contains(index) }
    }

    /// The field's text for a stored range and list.
    public static func shown(from: Int?, to: Int?, list: String) -> String {
        let ranges = listed(from: from, to: to, list: list)
        return ranges.isEmpty ? text(from: from, to: to) : listText(ranges)
    }

    /// The range and list as file names and logs show them: `4`, `4-7`, `4,7`, `4-end`.
    public static func named(from: Int?, to: Int?, list: String) -> String {
        let ranges = listed(from: from, to: to, list: list)
        return ranges.isEmpty ? label(from: from, to: to) : listText(ranges)
    }
}
