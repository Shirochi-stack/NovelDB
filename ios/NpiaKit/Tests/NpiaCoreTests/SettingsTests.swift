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

    func testInMemorySecretStore() {
        let store = InMemorySecretStore()
        store.set("abc", for: .npiaLoginKey)
        XCTAssertEqual(store.string(for: .npiaLoginKey), "abc")
        store.set(nil, for: .npiaLoginKey)
        XCTAssertNil(store.string(for: .npiaLoginKey))
    }
}
