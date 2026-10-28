import Foundation

/// Converts between the desktop app's flat `config.json` and `AppSettings`.
///
/// The desktop external downloader's `ext_*` keys become `shared`; the npia keys that differ
/// from them become `overrides["npia"]`. Credentials go to the secret store, never settings.
public enum DesktopConfig {
    // Legacy desktop keys that spell provider names are kept as escapes.
    static let legacyKpage = "\u{6b}\u{61}\u{6b}\u{61}\u{6f}"
    static let legacyNpia = "\u{6e}\u{6f}\u{76}\u{65}\u{6c}\u{70}\u{69}\u{61}"

    public struct Secrets: Equatable, Sendable {
        public var email: String?
        public var password: String?
        public var loginKey: String?
    }

    public static func importConfig(_ data: Data, into base: AppSettings = AppSettings()) throws -> (AppSettings, Secrets) {
        guard let config = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw SourceError.parse("config.json is not an object")
        }
        return importConfig(config, into: base)
    }

    public static func importConfig(_ c: [String: Any], into base: AppSettings = AppSettings()) -> (AppSettings, Secrets) {
        var s = base

        // Shared (external downloader) values.
        s.shared.threads = int(c["ext_threads"]) ?? s.shared.threads
        s.shared.intervalMin = double(c["ext_interval"]) ?? s.shared.intervalMin
        s.shared.intervalMax = double(c["ext_interval_max"]) ?? max(s.shared.intervalMin, s.shared.intervalMax)
        s.shared.formats = formats(c["ext_formats"], fallback: c["ext_format"]) ?? s.shared.formats
        s.shared.rangeFrom = bool(c["ext_from_enabled"]) == true ? int(c["ext_from"]) : nil
        s.shared.rangeTo = bool(c["ext_to_enabled"]) == true ? int(c["ext_to"]) : nil
        s.shared.useCache = bool(c["ext_use_cache"]) ?? s.shared.useCache
        s.shared.cacheImages = bool(c["ext_cache_images"]) ?? s.shared.cacheImages
        s.shared.numberChapters = bool(c["ext_number_chapters"]) ?? s.shared.numberChapters
        s.shared.separateTXT = bool(c["ext_separate_txt"]) ?? s.shared.separateTXT
        s.shared.skipPaid = bool(c["ext_skip_paid"]) ?? s.shared.skipPaid
        s.shared.imageWorkers = int(c["ext_image_workers"]) ?? s.shared.imageWorkers
        s.shared.generateOnStop = bool(c["ext_generate_on_stop"]) ?? s.shared.generateOnStop
        s.shared.maxRetries = int(c["max_retries"]) ?? s.shared.maxRetries
        s.shared.namingMode = (c["naming_mode"] as? String).flatMap(NamingMode.init(rawValue:)) ?? s.shared.namingMode
        s.shared.appendRange = bool(c["append_range"]) ?? s.shared.appendRange

        // npia values, stored only where they differ from shared.
        var npia = SharedOverrides()
        let shared = s.shared
        npia.threads = differing(int(c["thread_num"]), shared.threads)
        npia.intervalMin = differing(double(c["interval_num"]), shared.intervalMin)
        npia.intervalMax = differing(double(c["interval_max_num"]), shared.intervalMax)
        npia.formats = differing(formats(c["save_formats"], fallback: c["save_format"]), shared.formats)
        npia.rangeFrom = bool(c["from_enabled"]) == true ? int(c["from_num"]) : nil
        npia.rangeTo = bool(c["to_enabled"]) == true ? int(c["to_num"]) : nil
        npia.useCache = differing(bool(c["use_cache"]), shared.useCache)
        npia.cacheImages = differing(bool(c["cache_images"]), shared.cacheImages)
        npia.numberChapters = differing(bool(c["number_chapters"]), shared.numberChapters)
        npia.separateTXT = differing(bool(c["separate_txt"]), shared.separateTXT)
        s.overrides[SourceID.npia.rawValue] = npia.isEmpty ? nil : npia

        // Images.
        s.images.compress = bool(c["compress_images"]) ?? s.images.compress
        s.images.quality = int(c["jpeg_quality"]) ?? s.images.quality
        s.images.format = imageFormat(c["image_format"]) ?? s.images.format
        s.images.convertGIFs = bool(c["convert_gifs"]) ?? s.images.convertGIFs
        s.images.staticOnly = bool(c["static_only"]) ?? s.images.staticOnly
        s.images.recompressTargetMB = bool(c["recompress_target_enable"]) == true ? double(c["recompress_target_mb"]) : nil
        s.images.compressCover = bool(c["compress_cover"]) ?? s.images.compressCover
        s.images.coverQuality = int(c["cover_quality"]) ?? s.images.coverQuality
        s.images.coverFormat = imageFormat(c["cover_format"]) ?? s.images.coverFormat
        s.images.zipCompressImages = bool(c["zip_compress_images"]) ?? s.images.zipCompressImages
        s.images.compressionWorkers = int(c["image_compression_workers"]) ?? s.images.compressionWorkers
        s.images.longImageLayout = bool(c["ext_long_image_layout"]) ?? s.images.longImageLayout
        s.images.saveImageURLsOnly = bool(c["save_image_urls_only"]) ?? s.images.saveImageURLsOnly

        // Text and PDF.
        s.text.stripLeadingSpaces = bool(c["strip_leading_spaces"]) ?? s.text.stripLeadingSpaces
        s.text.removeNewlines = bool(c["remove_newlines"]) ?? s.text.removeNewlines
        s.text.blankParagraphLines = bool(c["ext_blank_paragraph_lines"]) ?? s.text.blankParagraphLines
        s.text.saveHTML = bool(c["save_html"]) ?? s.text.saveHTML
        s.text.pdfTOC = bool(c["pdf_toc"]) ?? s.text.pdfTOC
        s.text.pdfPageNumbers = bool(c["pdf_page_numbers"]) ?? s.text.pdfPageNumbers
        s.text.pdfCounterLayout = bool(c["pdf_counter_layout"]) ?? s.text.pdfCounterLayout

        // npia-only.
        s.npia.autoLogin = bool(c["auto_login"]) ?? s.npia.autoLogin
        s.npia.includeNotices = bool(c["include_notices"]) ?? s.npia.includeNotices
        s.npia.useBrowserFallback = bool(c["use_browser_fallback"]) ?? s.npia.useBrowserFallback
        s.npia.tagAgeFilter = (c["tag_age_filter"] as? String) ?? s.npia.tagAgeFilter
        s.npia.tagSelectedTags = (c["tag_selected_tags"] as? [String]) ?? s.npia.tagSelectedTags
        s.npia.tagCustomTags = (c["tag_custom_tags"] as? String) ?? s.npia.tagCustomTags
        s.npia.tagQueryGroups = stringGroups(c["tag_query_groups"]) ?? s.npia.tagQueryGroups
        s.npia.tagScrapeAll = bool(c["tag_scrape_all"]) ?? s.npia.tagScrapeAll
        var queries = int(c["scrape_max_queries"]) ?? s.npia.scrapeMaxQueries
        // The desktop treats the old default of 100 as "not customised" and moves it to the full sweep.
        if queries == 100 { queries = NpiaSettings.maxSearchQueries }
        s.npia.scrapeMaxQueries = NpiaSettings.clampQueries(queries)

        // Source options; the kpage-named keys win over legacy ones.
        func kpageKey(_ suffix: String) -> Any? {
            c["ext_kpage_\(suffix)"] ?? c["ext_\(legacyKpage)_\(suffix)"]
        }
        s.sources.kpage.skipLastPage = bool(kpageKey("skip_last_page")) ?? s.sources.kpage.skipLastPage
        s.sources.kpage.keepFiller = bool(kpageKey("keep_filler")) ?? s.sources.kpage.keepFiller
        s.sources.kpage.dedupeImages = bool(kpageKey("dedupe_images")) ?? s.sources.kpage.dedupeImages
        s.sources.kpage.dedupeLeadingImages = int(kpageKey("dedupe_leading_images")) ?? s.sources.kpage.dedupeLeadingImages
        s.sources.ntkNpiaCover = bool(c["ext_ntk_npia_cover"] ?? c["ext_ntk_\(legacyNpia)_cover"]) ?? s.sources.ntkNpiaCover
        s.sources.nwebStoreCover = bool(c["ext_syosetu_amazon_cover"]) ?? s.sources.nwebStoreCover
        s.sources.koboHorizontalLayout = bool(c["ext_kobo_horizontal_layout"]) ?? s.sources.koboHorizontalLayout

        // Site engines: order (unknown names dropped, missing ones appended), switched-off ones, opt-in updates.
        var engines = s.engines
        if let names = strings(c["source_engine_order"]) {
            engines.order = SiteEngine.ordered(names)
        }
        if let off = strings(c["source_engines_off"]) {
            engines.disabled = off.compactMap { SiteEngine(rawValue: $0) }
        }
        engines.novelDownloaderAutoUpdate = bool(c["novel_downloader_auto_update"]) ?? engines.novelDownloaderAutoUpdate
        engines.webtoepubAutoUpdate = bool(c["webtoepub_auto_update"]) ?? engines.webtoepubAutoUpdate
        if engines.enabled().isEmpty { engines.disabled = [] }  // never leave every engine off
        s.engines = engines

        let secrets = Secrets(
            email: nonEmpty(c["email"]),
            password: nonEmpty(c["wd"]),
            loginKey: nonEmpty(c["loginkey"])
        )
        return (s, secrets)
    }

    /// Writes settings back onto an existing desktop config, keeping keys the app does not know.
    /// Credentials are never exported.
    public static func exportConfig(_ s: AppSettings, onto existing: [String: Any] = [:]) -> [String: Any] {
        var c = existing
        let shared = s.shared
        let npia = s.effective(for: .npia)

        c["ext_threads"] = shared.threads
        c["ext_interval"] = shared.intervalMin
        c["ext_interval_max"] = shared.intervalMax
        c["ext_formats"] = shared.formats.map(\.rawValue)
        c["ext_format"] = shared.formats.first?.rawValue ?? "epub"
        c["ext_from_enabled"] = shared.rangeFrom != nil
        if let from = shared.rangeFrom { c["ext_from"] = from }
        c["ext_to_enabled"] = shared.rangeTo != nil
        if let to = shared.rangeTo { c["ext_to"] = to }
        c["ext_use_cache"] = shared.useCache
        c["ext_cache_images"] = shared.cacheImages
        c["ext_number_chapters"] = shared.numberChapters
        c["ext_separate_txt"] = shared.separateTXT
        c["ext_skip_paid"] = shared.skipPaid
        c["ext_image_workers"] = shared.imageWorkers
        c["ext_generate_on_stop"] = shared.generateOnStop
        c["max_retries"] = shared.maxRetries
        c["naming_mode"] = shared.namingMode.rawValue
        c["append_range"] = shared.appendRange

        c["thread_num"] = npia.threads
        c["interval_num"] = npia.intervalMin
        c["interval_max_num"] = npia.intervalMax
        c["save_formats"] = npia.formats.map(\.rawValue)
        c["save_format"] = npia.formats.first?.rawValue ?? "epub"
        c["from_enabled"] = npia.rangeFrom != nil
        if let from = npia.rangeFrom { c["from_num"] = from }
        c["to_enabled"] = npia.rangeTo != nil
        if let to = npia.rangeTo { c["to_num"] = to }
        c["use_cache"] = npia.useCache
        c["cache_images"] = npia.cacheImages
        c["number_chapters"] = npia.numberChapters
        c["separate_txt"] = npia.separateTXT

        c["compress_images"] = s.images.compress
        c["jpeg_quality"] = s.images.quality
        c["image_format"] = s.images.format.rawValue
        c["convert_gifs"] = s.images.convertGIFs
        c["static_only"] = s.images.staticOnly
        c["recompress_target_enable"] = s.images.recompressTargetMB != nil
        if let target = s.images.recompressTargetMB { c["recompress_target_mb"] = target }
        c["compress_cover"] = s.images.compressCover
        c["cover_quality"] = s.images.coverQuality
        c["cover_format"] = s.images.coverFormat.rawValue
        c["zip_compress_images"] = s.images.zipCompressImages
        c["image_compression_workers"] = s.images.compressionWorkers
        c["ext_long_image_layout"] = s.images.longImageLayout
        c["save_image_urls_only"] = s.images.saveImageURLsOnly

        c["strip_leading_spaces"] = s.text.stripLeadingSpaces
        c["remove_newlines"] = s.text.removeNewlines
        c["ext_blank_paragraph_lines"] = s.text.blankParagraphLines
        c["save_html"] = s.text.saveHTML
        c["pdf_toc"] = s.text.pdfTOC
        c["pdf_page_numbers"] = s.text.pdfPageNumbers
        c["pdf_counter_layout"] = s.text.pdfCounterLayout

        c["auto_login"] = s.npia.autoLogin
        c["include_notices"] = s.npia.includeNotices
        c["use_browser_fallback"] = s.npia.useBrowserFallback
        c["tag_age_filter"] = s.npia.tagAgeFilter
        c["tag_selected_tags"] = s.npia.tagSelectedTags
        c["tag_custom_tags"] = s.npia.tagCustomTags
        c["tag_query_groups"] = s.npia.tagQueryGroups
        c["tag_scrape_all"] = s.npia.tagScrapeAll
        c["scrape_max_queries"] = s.npia.scrapeMaxQueries

        c["ext_kpage_skip_last_page"] = s.sources.kpage.skipLastPage
        c["ext_kpage_keep_filler"] = s.sources.kpage.keepFiller
        c["ext_kpage_dedupe_images"] = s.sources.kpage.dedupeImages
        c["ext_kpage_dedupe_leading_images"] = s.sources.kpage.dedupeLeadingImages
        c["ext_ntk_npia_cover"] = s.sources.ntkNpiaCover
        c["ext_syosetu_amazon_cover"] = s.sources.nwebStoreCover
        c["ext_kobo_horizontal_layout"] = s.sources.koboHorizontalLayout

        c["source_engine_order"] = s.engines.fullOrder().map { $0.rawValue }
        c["source_engines_off"] = s.engines.disabled.map { $0.rawValue }
        c["novel_downloader_auto_update"] = s.engines.novelDownloaderAutoUpdate
        c["webtoepub_auto_update"] = s.engines.webtoepubAutoUpdate
        return c
    }

    // MARK: - Value coercion (the desktop file mixes ints, floats, and strings)

    static func int(_ value: Any?) -> Int? {
        switch value {
        case let v as Int: return v
        case let v as Double: return Int(v)
        case let v as String: return Int(v)
        default: return nil
        }
    }

    static func double(_ value: Any?) -> Double? {
        switch value {
        case let v as Double: return v
        case let v as Int: return Double(v)
        case let v as String: return Double(v)
        default: return nil
        }
    }

    static func bool(_ value: Any?) -> Bool? {
        switch value {
        case let v as Bool: return v
        case let v as NSNumber: return v.boolValue
        default: return nil
        }
    }

    static func formats(_ list: Any?, fallback: Any?) -> [OutputFormat]? {
        if let names = list as? [String] {
            let values = names.compactMap { OutputFormat(rawValue: $0.lowercased()) }
            if !values.isEmpty { return values }
        }
        if let name = fallback as? String, let value = OutputFormat(rawValue: name.lowercased()) {
            return [value]
        }
        return nil
    }

    static func imageFormat(_ value: Any?) -> ImageFormat? {
        (value as? String).flatMap { ImageFormat(rawValue: $0.uppercased()) }
    }

    static func differing<T: Equatable>(_ value: T?, _ shared: T) -> T? {
        guard let value, value != shared else { return nil }
        return value
    }

    /// A JSON array of tag arrays; empty groups and non-string tags are dropped.
    static func stringGroups(_ value: Any?) -> [[String]]? {
        guard let list = value as? [Any] else { return nil }
        return list.compactMap { ($0 as? [Any])?.compactMap { $0 as? String } }.filter { !$0.isEmpty }
    }

    /// A JSON array as strings (null entries dropped); nil when the value is not an array.
    static func strings(_ value: Any?) -> [String]? {
        guard let list = value as? [Any] else { return nil }
        return list.compactMap { $0 is NSNull ? nil : String(describing: $0) }
    }

    static func nonEmpty(_ value: Any?) -> String? {
        guard let text = value as? String, !text.isEmpty else { return nil }
        return text
    }
}
