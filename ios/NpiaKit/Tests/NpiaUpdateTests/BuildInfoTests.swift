import XCTest
@testable import NpiaUpdate

final class BuildInfoTests: XCTestCase {
    func testReadsTheStampedInfoPlistKeys() {
        let info = AppBuildInfo(info: ["NovelDBBuild": "1793193554", "NovelDBTag": "v98", "NovelDBLabel": "hotfix3"])
        XCTAssertEqual(info.build, 1_793_193_554)
        XCTAssertEqual(info.tag, "v98")
        XCTAssertEqual(info.label, "hotfix3")
        XCTAssertFalse(info.isDevelopment)
        XCTAssertEqual(info.versionName, "v98 hotfix3")
        XCTAssertEqual(info.statusText, "v98 hotfix3, build 1793193554")
    }

    func testTheLabelNoneIsNotShown() {
        let info = AppBuildInfo(info: ["NovelDBBuild": "1793193554", "NovelDBTag": "v98", "NovelDBLabel": "none"])
        XCTAssertEqual(info.versionName, "v98")
        XCTAssertEqual(info.statusText, "v98, build 1793193554")
        XCTAssertEqual(AppBuildInfo(build: 5, tag: "v98", label: "").versionName, "v98")
        XCTAssertEqual(AppBuildInfo(build: 5, tag: "v98", label: "NONE").versionName, "v98")
    }

    func testABuildNumberWithoutATagStillShowsTheBuild() {
        let info = AppBuildInfo(info: ["NovelDBBuild": "1793193554"])
        XCTAssertFalse(info.isDevelopment)
        XCTAssertEqual(info.versionName, "")
        XCTAssertEqual(info.statusText, "build 1793193554")
    }

    func testNumbersInThePlistWorkToo() {
        XCTAssertEqual(AppBuildInfo(info: ["NovelDBBuild": 1_793_193_554]).build, 1_793_193_554)
        XCTAssertEqual(AppBuildInfo(info: ["NovelDBBuild": NSNumber(value: 42)]).build, 42)
        XCTAssertEqual(AppBuildInfo(info: ["NovelDBBuild": " 77 "]).build, 77)
    }

    func testMissingKeysMakeADevelopmentBuild() {
        for info in [nil, [:], ["CFBundleVersion": "1"]] as [[String: Any]?] {
            let build = AppBuildInfo(info: info)
            XCTAssertTrue(build.isDevelopment)
            XCTAssertEqual(build.build, 0)
            XCTAssertEqual(build.statusText, "Development build")
        }
        XCTAssertEqual(AppBuildInfo.development, AppBuildInfo(info: nil))
    }

    func testEmptyOrUnusableBuildNumbersMakeADevelopmentBuild() {
        // An empty build setting expands to an empty string; an undefined one can stay unexpanded.
        let unusable: [Any] = ["", "   ", "$(NOVELDB_BUILD)", "abc", "12.5", "-3", "0", 0, -1, 1.5, true]
        for value in unusable {
            let build = AppBuildInfo(info: ["NovelDBBuild": value, "NovelDBTag": "v98", "NovelDBLabel": "none"])
            XCTAssertTrue(build.isDevelopment, "\(value) should not count as a build number")
            XCTAssertEqual(build.statusText, "Development build")
        }
    }

    func testTheRunningTestBundleHasNoStamp() {
        // The test runner carries none of the keys, like a build made without CI.
        let build = AppBuildInfo.current(bundle: .main)
        XCTAssertTrue(build.isDevelopment)
        XCTAssertEqual(build.statusText, "Development build")
    }
}
