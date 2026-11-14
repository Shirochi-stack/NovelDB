import XCTest
import NpiaCore
@testable import NpiaSources

/// A userscript added as a custom engine: its name and sites come from its header.
final class UserscriptEngineTests: XCTestCase {
    private let script = """
    // ==UserScript==
    // @name        Test capture
    // @match       *://reader.example/*
    // @match       https://*.other.example/read/*
    // @version     1.0
    // ==/UserScript==
    (function () {})();
    """

    func testTheHeaderGivesTheNameAndTheSites() throws {
        let info = try CustomEngines.describeUserscript("test_capture", script)
        XCTAssertEqual(info.id, "test_capture")
        XCTAssertEqual(info.label, "Test capture")
        XCTAssertEqual(info.sites, ["reader.example", "other.example"])
        XCTAssertTrue(info.userscript)
        XCTAssertFalse(info.everywhere)
        XCTAssertEqual(info.fileName, "test_capture.user.js")
    }

    func testAScriptForEverySiteNamesNone() throws {
        let anywhere = "// ==UserScript==\n// @name Any site\n// @match *://*/*\n// ==/UserScript==\n"
        let info = try CustomEngines.describeUserscript("any", anywhere)
        XCTAssertTrue(info.everywhere)
        XCTAssertTrue(info.sites.isEmpty)
        // No @match at all is every site too, as in a userscript manager.
        let bare = try CustomEngines.describeUserscript("bare", "// ==UserScript==\n// @name Bare\n// ==/UserScript==\n")
        XCTAssertTrue(bare.everywhere)
    }

    func testAFileWithoutAHeaderOrANameIsRefused() {
        XCTAssertThrowsError(try CustomEngines.describeUserscript("plain", "console.log(1)"))
        XCTAssertThrowsError(try CustomEngines.describeUserscript(
            "nameless", "// ==UserScript==\n// @match *://a.example/*\n// ==/UserScript==\n"))
    }
}
