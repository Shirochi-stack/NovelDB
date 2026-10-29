import XCTest
@testable import NpiaCore

final class SettingsTests: XCTestCase {
    func testDecodeFillsMissingKeysWithDefaults() throws {
        let stored = #"{"shared": {"threads": 8}, "upload": {"autoUpload": true}}"#
        let settings = try SettingsCoding.decode(Data(stored.utf8))
        XCTAssertEqual(settings.shared.threads, 8)
        XCTAssertEqual(settings.shared.formats, [.epub])
        XCTAssertTrue(settings.upload.autoUpload)
        XCTAssertEqual(settings.upload.hosts, UploadSettings().hosts)
    }

    func testTimeoutsRoundTripThroughDesktopConfig() throws {
        let fresh = DesktopConfig.exportConfig(AppSettings())
        XCTAssertEqual(fresh["request_timeout"] as? Int, 60)
        XCTAssertEqual(fresh["page_load_timeout"] as? Int, 60)
        let (imported, _) = DesktopConfig.importConfig(["request_timeout": 90, "page_load_timeout": 120])
        XCTAssertEqual(imported.shared.requestTimeoutSeconds, 90)
        XCTAssertEqual(imported.shared.pageLoadTimeoutSeconds, 120)
        let (again, _) = DesktopConfig.importConfig(DesktopConfig.exportConfig(imported))
        XCTAssertEqual(again.shared, imported.shared)
        // Out-of-range values are clamped to 10...600.
        let (clamped, _) = DesktopConfig.importConfig(["request_timeout": 5, "page_load_timeout": 9999])
        XCTAssertEqual(clamped.shared.requestTimeoutSeconds, 10)
        XCTAssertEqual(clamped.shared.pageLoadTimeoutSeconds, 600)
        // Settings saved before the keys existed still decode, with the defaults.
        let old = try JSONDecoder().decode(AppSettings.self, from: Data(#"{"shared": {"threads": 2}}"#.utf8))
        XCTAssertEqual(old.shared.threads, 2)
        XCTAssertEqual(old.shared.requestTimeoutSeconds, 60)
        XCTAssertEqual(old.shared.formats, SharedSettings().formats)
    }

    func testEncodeDecodeRoundTrip() throws {
        var settings = AppSettings()
        settings.shared.rangeFrom = 5
        settings.overrides["npia"] = { var o = SharedOverrides(); o.threads = 2; return o }()
        XCTAssertEqual(try SettingsCoding.decode(SettingsCoding.encode(settings)), settings)
    }

    func testOverridesApplyPerSource() {
        var settings = AppSettings()
        settings.shared.threads = 4
        var npia = SharedOverrides()
        npia.threads = 1
        npia.formats = [.txt]
        settings.overrides["npia"] = npia
        XCTAssertEqual(settings.effective(for: .npia).threads, 1)
        XCTAssertEqual(settings.effective(for: .npia).formats, [.txt])
        XCTAssertEqual(settings.effective(for: .kpage).threads, 4)
    }

    func testDesktopImportSplitsSharedAndNpiaAndSecrets() {
        let legacyKpage = "\u{6b}\u{61}\u{6b}\u{61}\u{6f}"
        let config: [String: Any] = [
            "email": "reader@example.com", "wd": "pw", "loginkey": "",
            "thread_num": 4, "interval_num": 2.5, "interval_max_num": 4.0,
            "save_formats": ["epub", "txt"], "from_enabled": false, "to_enabled": true, "to_num": 10,
            "ext_threads": 4, "ext_interval": 1.0, "ext_interval_max": 2.0,
            "ext_formats": ["epub", "txt"], "ext_from_enabled": false, "ext_to_enabled": true, "ext_from": 89, "ext_to": 92,
            "image_format": "WEBP", "jpeg_quality": 90, "recompress_target_enable": false, "recompress_target_mb": 20.0,
            "ext_\(legacyKpage)_dedupe_leading_images": 3, "ext_kpage_keep_filler": true,
        ]
        let (settings, secrets) = DesktopConfig.importConfig(config)
        XCTAssertEqual(settings.shared.threads, 4)
        XCTAssertEqual(settings.shared.intervalMin, 1.0)
        XCTAssertEqual(settings.shared.intervalMax, 2.0)
        XCTAssertNil(settings.shared.rangeFrom)
        XCTAssertEqual(settings.shared.rangeTo, 92)

        let npia = settings.effective(for: .npia)
        XCTAssertEqual(npia.threads, 4)
        XCTAssertEqual(npia.intervalMin, 2.5)
        XCTAssertEqual(npia.intervalMax, 4.0)
        XCTAssertEqual(npia.rangeTo, 10)
        XCTAssertNil(settings.overrides["npia"]?.threads, "equal values are not stored as overrides")

        XCTAssertEqual(settings.images.format, .webp)
        XCTAssertNil(settings.images.recompressTargetMB)
        XCTAssertEqual(settings.sources.kpage.dedupeLeadingImages, 3)
        XCTAssertTrue(settings.sources.kpage.keepFiller)

        XCTAssertEqual(secrets.email, "reader@example.com")
        XCTAssertEqual(secrets.password, "pw")
        XCTAssertNil(secrets.loginKey)
    }

    func testDesktopExportKeepsUnknownKeysAndOmitsSecrets() {
        var settings = AppSettings()
        settings.shared.threads = 6
        var npia = SharedOverrides()
        npia.threads = 3
        settings.overrides["npia"] = npia
        let exported = DesktopConfig.exportConfig(settings, onto: ["gui_scale_factor": 1.7, "email": "keep@example.com"])
        XCTAssertEqual(exported["ext_threads"] as? Int, 6)
        XCTAssertEqual(exported["thread_num"] as? Int, 3)
        XCTAssertEqual(exported["gui_scale_factor"] as? Double, 1.7)
        XCTAssertEqual(exported["email"] as? String, "keep@example.com")
        XCTAssertNil(exported["wd"])

        let (reimported, _) = DesktopConfig.importConfig(exported)
        XCTAssertEqual(reimported.effective(for: .npia).threads, 3)
        XCTAssertEqual(reimported.shared.threads, 6)
    }

    func testRateLimitingDefaultsToTwoThroughFiveSeconds() throws {
        let defaults = SharedSettings()
        XCTAssertEqual(defaults.intervalMin, 2.0)
        XCTAssertEqual(defaults.intervalMax, 5.0)
        XCTAssertEqual(defaults.normalizedInterval, 2.0...5.0)

        // A desktop config without the keys imports to the new defaults.
        let (imported, _) = DesktopConfig.importConfig([:])
        XCTAssertEqual(imported.shared.intervalMin, 2.0)
        XCTAssertEqual(imported.shared.intervalMax, 5.0)

        // Values that were saved stay as they are.
        let saved = try SettingsCoding.decode(Data(#"{"shared": {"intervalMin": 1.0, "intervalMax": 1.0}}"#.utf8))
        XCTAssertEqual(saved.shared.intervalMin, 1.0)
        XCTAssertEqual(saved.shared.intervalMax, 1.0)
        let untouched = try SettingsCoding.decode(Data(#"{"shared": {"threads": 3}}"#.utf8))
        XCTAssertEqual(untouched.shared.intervalMin, 2.0)
        XCTAssertEqual(untouched.shared.intervalMax, 5.0)
    }

    func testTagQueryGroupsRoundTripThroughDesktopConfig() {
        let config: [String: Any] = [
            "tag_query_groups": [["TS"], ["a", "b"]], "tag_selected_tags": ["TS"], "tag_custom_tags": "x, y",
            "tag_age_filter": "19", "tag_scrape_all": true, "scrape_max_queries": 120,
        ]
        let (settings, _) = DesktopConfig.importConfig(config)
        XCTAssertEqual(settings.npia.tagQueryGroups, [["TS"], ["a", "b"]])
        XCTAssertEqual(settings.npia.scrapeMaxQueries, 120)

        let exported = DesktopConfig.exportConfig(settings)
        XCTAssertEqual(exported["tag_query_groups"] as? [[String]], [["TS"], ["a", "b"]])
        XCTAssertEqual(exported["scrape_max_queries"] as? Int, 120)

        let (again, _) = DesktopConfig.importConfig(exported)
        XCTAssertEqual(again.npia, settings.npia)

        // Missing or malformed groups leave the existing ones alone; empty groups are dropped.
        XCTAssertEqual(DesktopConfig.importConfig([:], into: settings).0.npia.tagQueryGroups, [["TS"], ["a", "b"]])
        let cleaned = DesktopConfig.importConfig(["tag_query_groups": [["a"], [], ["b", 5]] as [Any]]).0
        XCTAssertEqual(cleaned.npia.tagQueryGroups, [["a"], ["b"]])
    }

    func testSearchQueryCountIsClampedAndLegacyDefaultMoves() throws {
        XCTAssertEqual(NpiaSettings().scrapeMaxQueries, 165)
        func imported(_ value: Any) -> Int {
            DesktopConfig.importConfig(["scrape_max_queries": value]).0.npia.scrapeMaxQueries
        }
        XCTAssertEqual(imported(500), 165)
        XCTAssertEqual(imported(0), 1)
        XCTAssertEqual(imported(-4), 1)
        XCTAssertEqual(imported("80"), 80)
        XCTAssertEqual(imported(100), 165, "the desktop treats the old default of 100 as the full sweep")

        var settings = NpiaSettings()
        settings.scrapeMaxQueries = 9_999
        XCTAssertEqual(settings.scrapeMaxQueries, 165)
        settings.scrapeMaxQueries = 0
        XCTAssertEqual(settings.scrapeMaxQueries, 1)

        let stored = try SettingsCoding.decode(Data(#"{"npia": {"scrapeMaxQueries": 4000}}"#.utf8))
        XCTAssertEqual(stored.npia.scrapeMaxQueries, 165)
    }

    func testNpiaSettingsStoredBeforeQueryGroupsStillDecode() throws {
        // A job saved by an older build has no tagQueryGroups key and decodes without the merge step.
        let old = #"{"autoLogin": true, "includeNotices": false, "useBrowserFallback": true, "tagAgeFilter": "19", "tagSelectedTags": ["TS"], "tagCustomTags": "x", "tagScrapeAll": false, "scrapeMaxQueries": 50}"#
        let decoded = try JSONDecoder().decode(NpiaSettings.self, from: Data(old.utf8))
        XCTAssertTrue(decoded.autoLogin)
        XCTAssertFalse(decoded.includeNotices)
        XCTAssertEqual(decoded.tagSelectedTags, ["TS"])
        XCTAssertEqual(decoded.tagQueryGroups, [])
        XCTAssertEqual(decoded.scrapeMaxQueries, 50)
        XCTAssertEqual(try JSONDecoder().decode(NpiaSettings.self, from: Data("{}".utf8)), NpiaSettings())

        var settings = AppSettings()
        settings.npia.tagQueryGroups = [["a", "b"], ["c"]]
        XCTAssertEqual(try SettingsCoding.decode(SettingsCoding.encode(settings)).npia.tagQueryGroups, [["a", "b"], ["c"]])
    }

    func testUploadBundlingDefaults() {
        let upload = UploadSettings()
        XCTAssertTrue(upload.zipBundle, "bundling is on by default")
        XCTAssertEqual(upload.zipMaxMB, 1024, "largest ZIP is 1 GB by default")
        XCTAssertEqual(UploadSettings.zipMaxMBRange, 50...4096)
        XCTAssertEqual(AppSettings().upload, upload)
    }

    func testUploadSettingsStoredBeforeBundlingStillDecode() throws {
        // A job saved by an older build keeps its settings without the two new keys and decodes
        // with the plain JSONDecoder (no merge step).
        let old = #"{"autoUpload": true, "hosts": ["gofile"], "copyLinkToClipboard": false}"#
        let decoded = try JSONDecoder().decode(UploadSettings.self, from: Data(old.utf8))
        XCTAssertTrue(decoded.autoUpload)
        XCTAssertEqual(decoded.hosts, ["gofile"])
        XCTAssertFalse(decoded.copyLinkToClipboard)
        XCTAssertTrue(decoded.zipBundle)
        XCTAssertEqual(decoded.zipMaxMB, 1024)
        XCTAssertEqual(try JSONDecoder().decode(UploadSettings.self, from: Data("{}".utf8)), UploadSettings())

        let wholeApp = #"{"upload": {"autoUpload": true}}"#
        let settings = try JSONDecoder().decode(AppSettings.self, from: Data(wholeApp.utf8))
        XCTAssertEqual(settings.upload.zipMaxMB, 1024)
    }

    func testZipSizeIsClampedToTheRange() throws {
        var upload = UploadSettings()
        upload.zipMaxMB = 5
        XCTAssertEqual(upload.zipMaxMB, 50)
        upload.zipMaxMB = 99_999
        XCTAssertEqual(upload.zipMaxMB, 4096)
        upload.zipMaxMB = 700
        XCTAssertEqual(upload.zipMaxMB, 700)

        let stored = try JSONDecoder().decode(UploadSettings.self, from: Data(#"{"zipMaxMB": 10}"#.utf8))
        XCTAssertEqual(stored.zipMaxMB, 50)
        let huge = try SettingsCoding.decode(Data(#"{"upload": {"zipMaxMB": 100000}}"#.utf8))
        XCTAssertEqual(huge.upload.zipMaxMB, 4096)
    }

    func testUploadBundlingRoundTripsThroughSettingsAndDesktopConfig() throws {
        var settings = AppSettings()
        settings.upload.zipBundle = false
        settings.upload.zipMaxMB = 300
        XCTAssertEqual(try SettingsCoding.decode(SettingsCoding.encode(settings)), settings)

        let exported = DesktopConfig.exportConfig(settings, onto: ["gui_scale_factor": 1.7])
        XCTAssertEqual(exported["upload_zip_bundle"] as? Bool, false)
        XCTAssertEqual(exported["upload_zip_max_mb"] as? Int, 300)
        XCTAssertEqual(exported["gui_scale_factor"] as? Double, 1.7)

        let (reimported, _) = DesktopConfig.importConfig(exported)
        XCTAssertFalse(reimported.upload.zipBundle)
        XCTAssertEqual(reimported.upload.zipMaxMB, 300)

        // A desktop file without the keys leaves the defaults (or the current values) alone.
        let (defaults, _) = DesktopConfig.importConfig([:])
        XCTAssertTrue(defaults.upload.zipBundle)
        XCTAssertEqual(defaults.upload.zipMaxMB, 1024)
        XCTAssertEqual(DesktopConfig.importConfig([:], into: settings).0.upload.zipMaxMB, 300)

        // Numbers as the desktop file may spell them, and values out of range.
        XCTAssertEqual(DesktopConfig.importConfig(["upload_zip_max_mb": "2048"]).0.upload.zipMaxMB, 2048)
        XCTAssertEqual(DesktopConfig.importConfig(["upload_zip_max_mb": 512.0]).0.upload.zipMaxMB, 512)
        XCTAssertEqual(DesktopConfig.importConfig(["upload_zip_max_mb": 1]).0.upload.zipMaxMB, 50)
        XCTAssertEqual(DesktopConfig.importConfig(["upload_zip_max_mb": 99_999]).0.upload.zipMaxMB, 4096)
        XCTAssertFalse(DesktopConfig.importConfig(["upload_zip_bundle": false]).0.upload.zipBundle)
    }

    func testInMemorySecretStore() {
        let store = InMemorySecretStore()
        store.set("abc", for: .npiaLoginKey)
        XCTAssertEqual(store.string(for: .npiaLoginKey), "abc")
        store.set(nil, for: .npiaLoginKey)
        XCTAssertNil(store.string(for: .npiaLoginKey))
    }
}
