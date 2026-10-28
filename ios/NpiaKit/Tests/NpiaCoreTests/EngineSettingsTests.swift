import XCTest
@testable import NpiaCore

final class EngineSettingsTests: XCTestCase {
    func testOrderKeepsKnownEnginesAndAppendsMissingOnes() {
        XCTAssertEqual(
            SiteEngine.ordered(["webtoepub", "bogus", "native"]),
            [.webtoepub, .native, .novelDownloader]
        )
        XCTAssertEqual(SiteEngine.ordered(nil), SiteEngine.allCases)
        XCTAssertEqual(SiteEngine.ordered(["native", "native", "webtoepub"]), [.native, .webtoepub, .novelDownloader])
        XCTAssertEqual(SiteEngine.ordered(SiteEngine.allCases.map { $0.rawValue }, disabled: [.novelDownloader]), [.native, .webtoepub])
    }

    func testDefaultsAreAllEnginesOnAndUpdatesOn() {
        let engines = AppSettings().engines
        XCTAssertEqual(engines.enabled(), [.native, .novelDownloader, .webtoepub])
        XCTAssertEqual(engines.fullOrder(), SiteEngine.allCases)
        XCTAssertTrue(engines.novelDownloaderAutoUpdate)
        XCTAssertTrue(engines.webtoepubAutoUpdate)
        XCTAssertTrue(engines.autoUpdate(.novelDownloader))
        XCTAssertTrue(engines.autoUpdate(.webtoepub))
        XCTAssertFalse(engines.autoUpdate(.native))
    }

    func testRawValuesAreTheDesktopNames() {
        XCTAssertEqual(SiteEngine.allCases.map { $0.rawValue }, ["native", "novel_downloader", "webtoepub"])
    }

    func testSwitchedOffEnginesKeepTheirPlaceInTheFullOrder() {
        var engines = EngineSettings(order: [.webtoepub, .native, .novelDownloader])
        XCTAssertTrue(engines.setEnabled(.native, false))
        XCTAssertEqual(engines.enabled(), [.webtoepub, .novelDownloader])
        XCTAssertEqual(engines.fullOrder(), [.webtoepub, .native, .novelDownloader])
        XCTAssertFalse(engines.isEnabled(.native))
        XCTAssertTrue(engines.setEnabled(.native, true))
        XCTAssertEqual(engines.enabled(), [.webtoepub, .native, .novelDownloader])
    }

    func testOneEngineAlwaysStaysOn() {
        var engines = EngineSettings()
        XCTAssertTrue(engines.setEnabled(.native, false))
        XCTAssertTrue(engines.setEnabled(.novelDownloader, false))
        XCTAssertFalse(engines.setEnabled(.webtoepub, false), "the last engine cannot be switched off")
        XCTAssertEqual(engines.enabled(), [.webtoepub])
        // The initializer applies the same rule.
        XCTAssertEqual(EngineSettings(disabled: SiteEngine.allCases).enabled(), SiteEngine.allCases)
    }

    func testResetOrderKeepsUpdateChoices() {
        var engines = EngineSettings(order: [.webtoepub, .native, .novelDownloader], disabled: [.native], webtoepubAutoUpdate: true)
        engines.resetOrder()
        XCTAssertEqual(engines.order, SiteEngine.allCases)
        XCTAssertEqual(engines.disabled, [])
        XCTAssertTrue(engines.webtoepubAutoUpdate)
    }

    // MARK: - Desktop config

    func testDesktopConfigRoundTrip() {
        let (settings, _) = DesktopConfig.importConfig([
            "source_engine_order": ["webtoepub", "native", "novel_downloader"],
            "source_engines_off": ["novel_downloader"],
            "novel_downloader_auto_update": false,
        ])
        XCTAssertEqual(settings.engines, EngineSettings(
            order: [.webtoepub, .native, .novelDownloader], disabled: [.novelDownloader], novelDownloaderAutoUpdate: false
        ))
        let exported = DesktopConfig.exportConfig(settings)
        XCTAssertEqual(exported["source_engine_order"] as? [String], ["webtoepub", "native", "novel_downloader"])
        XCTAssertEqual(exported["source_engines_off"] as? [String], ["novel_downloader"])
        XCTAssertEqual(exported["webtoepub_auto_update"] as? Bool, true)
        XCTAssertEqual(exported["novel_downloader_auto_update"] as? Bool, false)
        XCTAssertEqual(DesktopConfig.importConfig(exported).0.engines, settings.engines)
    }

    func testDesktopImportDropsUnknownNamesAndAppendsMissingOnes() {
        let (settings, _) = DesktopConfig.importConfig([
            "source_engine_order": ["webtoepub", "bogus"],
            "source_engines_off": ["bogus", "native"],
            "novel_downloader_auto_update": true,
        ])
        XCTAssertEqual(settings.engines.fullOrder(), [.webtoepub, .native, .novelDownloader])
        XCTAssertEqual(settings.engines.disabled, [.native])
        XCTAssertTrue(settings.engines.novelDownloaderAutoUpdate)
        XCTAssertTrue(settings.engines.webtoepubAutoUpdate, "a missing key keeps the current choice")
    }

    func testDesktopImportKeepsExistingValuesForMissingKeys() {
        var base = AppSettings()
        base.engines = EngineSettings(order: [.novelDownloader, .webtoepub, .native], disabled: [.webtoepub], novelDownloaderAutoUpdate: true)
        let (settings, _) = DesktopConfig.importConfig(["ext_threads": 3], into: base)
        XCTAssertEqual(settings.engines, base.engines)
        XCTAssertEqual(settings.shared.threads, 3)
        // Values of the wrong type are ignored too.
        let (odd, _) = DesktopConfig.importConfig(["source_engine_order": "native", "webtoepub_auto_update": "yes"], into: base)
        XCTAssertEqual(odd.engines, base.engines)
    }

    func testDesktopImportNeverLeavesEveryEngineOff() {
        let (settings, _) = DesktopConfig.importConfig(["source_engines_off": ["native", "novel_downloader", "webtoepub"]])
        XCTAssertEqual(settings.engines.enabled(), SiteEngine.allCases)
        XCTAssertEqual(settings.engines.disabled, [])
    }

    func testExportKeepsUnknownDesktopKeys() {
        let exported = DesktopConfig.exportConfig(AppSettings(), onto: ["gui_scale_factor": 1.5])
        XCTAssertEqual(exported["gui_scale_factor"] as? Double, 1.5)
        XCTAssertEqual(exported["source_engine_order"] as? [String], ["native", "novel_downloader", "webtoepub"])
        XCTAssertEqual(exported["source_engines_off"] as? [String], [])
    }

    // MARK: - App settings file

    func testSettingsRoundTripThroughTheAppFile() throws {
        var settings = AppSettings()
        settings.engines = EngineSettings(order: [.novelDownloader, .native, .webtoepub], disabled: [.native], webtoepubAutoUpdate: true)
        XCTAssertEqual(try SettingsCoding.decode(SettingsCoding.encode(settings)), settings)
    }

    func testOldSettingsFilesGetTheDefaultEngines() throws {
        let stored = #"{"shared": {"threads": 2}}"#
        let settings = try SettingsCoding.decode(Data(stored.utf8))
        XCTAssertEqual(settings.shared.threads, 2)
        XCTAssertEqual(settings.engines, EngineSettings())
    }

    func testSettingsStoredBeforeEnginesExistedDecodeWithAPlainDecoder() throws {
        // The queue keeps each job's settings and reads them with a plain JSONDecoder.
        var settings = AppSettings()
        settings.shared.threads = 7
        let data = try JSONEncoder().encode(settings)
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertNotNil(object.removeValue(forKey: "engines"))
        let decoded = try JSONDecoder().decode(AppSettings.self, from: JSONSerialization.data(withJSONObject: object))
        XCTAssertEqual(decoded.shared.threads, 7)
        XCTAssertEqual(decoded.engines, EngineSettings())
    }

    func testHandEditedEngineSettingsStillLoad() throws {
        let stored = #"{"engines": {"order": ["webtoepub", "bogus"], "disabled": ["native", "bogus"]}}"#
        let settings = try SettingsCoding.decode(Data(stored.utf8))
        XCTAssertEqual(settings.engines.fullOrder(), [.webtoepub, .native, .novelDownloader])
        XCTAssertEqual(settings.engines.disabled, [.native])
        XCTAssertTrue(settings.engines.novelDownloaderAutoUpdate, "a missing key means the default, on")
    }
}
