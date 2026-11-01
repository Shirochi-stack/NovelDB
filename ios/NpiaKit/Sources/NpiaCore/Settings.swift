import Foundation

public enum OutputFormat: String, Codable, CaseIterable, Sendable {
    case epub, txt, pdf, html, cbz
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
    public var namingMode: NamingMode = .title
    public var appendRange = false

    public init() {}

    /// The request timeout and page load wait the app starts with, in seconds (Settings > Network).
    public static let defaultTimeoutSeconds = 60

    private enum CodingKeys: String, CodingKey {
        case threads, intervalMin, intervalMax, maxRetries, requestTimeoutSeconds, pageLoadTimeoutSeconds, formats
        case rangeFrom, rangeTo, useCache, cacheImages, numberChapters, separateTXT, skipPaid, imageWorkers
        case generateOnStop, namingMode, appendRange
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
        generateOnStop = try c.decodeIfPresent(Bool.self, forKey: .generateOnStop) ?? generateOnStop
        namingMode = try c.decodeIfPresent(NamingMode.self, forKey: .namingMode) ?? namingMode
        appendRange = try c.decodeIfPresent(Bool.self, forKey: .appendRange) ?? appendRange
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
    public var compress = true
    public var quality = 90
    public var format: ImageFormat = .webp
    public var convertGIFs = false
    public var staticOnly = false
    public var recompressTargetMB: Double?
    public var compressCover = false
    public var coverQuality = 90
    public var coverFormat: ImageFormat = .jpeg
    public var zipCompressImages = false
    public var compressionWorkers = 4
    public var longImageLayout = false
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
