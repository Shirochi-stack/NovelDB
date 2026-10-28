import XCTest
@testable import NpiaCore

final class AppUpdateSettingsTests: XCTestCase {
    func testCheckingForAppUpdatesIsOnByDefault() {
        XCTAssertTrue(AppSettings().appUpdateCheck)
        let (imported, _) = DesktopConfig.importConfig([:])
        XCTAssertTrue(imported.appUpdateCheck, "a desktop config without the key keeps the default")
    }

    func testSettingsStoredBeforeTheFieldExistedStillDecodeAsOn() throws {
        // Straight through the decoder (the queue keeps a copy of the settings with each job) ...
        let old = Data(#"{"schemaVersion": 1}"#.utf8)
        XCTAssertTrue(try JSONDecoder().decode(AppSettings.self, from: old).appUpdateCheck)
        XCTAssertTrue(try JSONDecoder().decode(AppSettings.self, from: Data("{}".utf8)).appUpdateCheck)
        // ... and through the reader of the settings file, which fills missing keys with the defaults first.
        let merged = try SettingsCoding.decode(Data(#"{"schemaVersion": 1, "shared": {"threads": 3}}"#.utf8))
        XCTAssertTrue(merged.appUpdateCheck)
        XCTAssertEqual(merged.shared.threads, 3)
    }

    func testTheChoiceSurvivesTheSettingsFile() throws {
        var settings = AppSettings()
        settings.appUpdateCheck = false
        let data = try SettingsCoding.encode(settings)
        XCTAssertFalse(try SettingsCoding.decode(data).appUpdateCheck)
        XCTAssertFalse(try JSONDecoder().decode(AppSettings.self, from: data).appUpdateCheck)
        let stored = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(stored["appUpdateCheck"] as? Bool, false)

        let explicit = Data(#"{"appUpdateCheck": false}"#.utf8)
        XCTAssertFalse(try SettingsCoding.decode(explicit).appUpdateCheck)
    }

    func testDesktopConfigRoundTripsTheChoiceAsAppUpdateCheck() {
        let (off, _) = DesktopConfig.importConfig(["app_update_check": false])
        XCTAssertFalse(off.appUpdateCheck)
        let exportedOff = DesktopConfig.exportConfig(off)
        XCTAssertEqual(exportedOff["app_update_check"] as? Bool, false)
        XCTAssertFalse(DesktopConfig.importConfig(exportedOff).0.appUpdateCheck)

        let (on, _) = DesktopConfig.importConfig(["app_update_check": true], into: off)
        XCTAssertTrue(on.appUpdateCheck)
        let exportedOn = DesktopConfig.exportConfig(on)
        XCTAssertEqual(exportedOn["app_update_check"] as? Bool, true)
        XCTAssertTrue(DesktopConfig.importConfig(exportedOn).0.appUpdateCheck)

        // The default is exported as true, like the other keys.
        XCTAssertEqual(DesktopConfig.exportConfig(AppSettings())["app_update_check"] as? Bool, true)
    }

    func testImportKeepsTheCurrentChoiceForMissingOrOddValues() {
        var base = AppSettings()
        base.appUpdateCheck = false
        XCTAssertFalse(DesktopConfig.importConfig(["ext_threads": 3], into: base).0.appUpdateCheck)
        XCTAssertFalse(DesktopConfig.importConfig(["app_update_check": "yes"], into: base).0.appUpdateCheck)
        XCTAssertFalse(DesktopConfig.importConfig(["app_update_check": NSNull()], into: base).0.appUpdateCheck)
        base.appUpdateCheck = true
        XCTAssertTrue(DesktopConfig.importConfig(["app_update_check": "no"], into: base).0.appUpdateCheck)
    }

    func testExportKeepsUnknownKeysNextToTheNewOne() {
        let exported = DesktopConfig.exportConfig(AppSettings(), onto: ["gui_scale_factor": 1.5, "app_update_check": false])
        XCTAssertEqual(exported["gui_scale_factor"] as? Double, 1.5)
        XCTAssertEqual(exported["app_update_check"] as? Bool, true, "the app's own value replaces the old one")
    }
}
