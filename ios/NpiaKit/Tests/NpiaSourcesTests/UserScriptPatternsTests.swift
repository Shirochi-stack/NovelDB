import XCTest
@testable import NpiaSources

/// The page patterns of a userscript header (`@match`, `@include`, `@exclude`), as Greasemonkey reads them.
final class UserScriptPatternsTests: XCTestCase {
    private let backslash = "\u{5c}"

    private func patterns(_ lines: String...) -> UserScriptPatterns {
        UserScriptPatterns.parse("// ==UserScript==\n" + lines.map { "// " + $0 }.joined(separator: "\n") + "\n// ==/UserScript==\nbody();")
    }

    func testStarSchemeMeansHttpOrHttpsOnly() {
        let rules = patterns("@match *://site.test/book/*")
        XCTAssertTrue(rules.accepts("https://site.test/book/1"))
        XCTAssertTrue(rules.accepts("http://site.test/book/1"))
        XCTAssertFalse(rules.accepts("ftp://site.test/book/1"))
        XCTAssertFalse(rules.accepts("file://site.test/book/1"))
        let onlyHTTPS = patterns("@match https://site.test/*")
        XCTAssertTrue(onlyHTTPS.accepts("https://site.test/x"))
        XCTAssertFalse(onlyHTTPS.accepts("http://site.test/x"))
    }

    func testWildcardsInHostAndPath() {
        let rules = patterns("@match *://*.site.test/info/*", "@match *://other.test/a/*/c")
        // "*." is the site and every subdomain.
        XCTAssertTrue(rules.accepts("https://site.test/info/1"))
        XCTAssertTrue(rules.accepts("https://book.site.test/info/1"))
        XCTAssertTrue(rules.accepts("https://a.b.site.test/info/1"))
        XCTAssertFalse(rules.accepts("https://notsite.test/info/1"))
        XCTAssertFalse(rules.accepts("https://site.test.evil/info/1"))
        XCTAssertFalse(rules.accepts("https://site.test/rank/"))
        // "*" in the path crosses slashes; the pattern must still fit the whole path.
        XCTAssertTrue(rules.accepts("https://other.test/a/b/c"))
        XCTAssertTrue(rules.accepts("https://other.test/a/b/x/c"))
        XCTAssertFalse(rules.accepts("https://other.test/a/b/c/d"))
    }

    func testTheQueryCountsAndTheFragmentAndPortDoNot() {
        let rules = patterns("@match *://site.test/read.php?id=*", "@match *://home.test/")
        XCTAssertTrue(rules.accepts("https://site.test/read.php?id=5&x=1"))
        XCTAssertFalse(rules.accepts("https://site.test/read.php"))
        XCTAssertTrue(rules.accepts("https://site.test:8443/read.php?id=5#top"))
        XCTAssertTrue(rules.accepts("https://home.test"))
        XCTAssertTrue(rules.accepts("https://home.test/#section"))
        XCTAssertFalse(rules.accepts("https://home.test/rank/"))
    }

    func testExcludeBeatsMatch() {
        let rules = patterns("@match *://site.test/*", "@exclude *://site.test/chapter/*", "@exclude *://site.test/")
        XCTAssertTrue(rules.accepts("https://site.test/book/9"))
        XCTAssertFalse(rules.accepts("https://site.test/chapter/9"))
        XCTAssertFalse(rules.accepts("https://site.test/"))
        XCTAssertEqual(rules.allowCount, 1)
        XCTAssertEqual(rules.denyCount, 2)
    }

    func testNoMatchMeansNoPage() {
        XCTAssertFalse(patterns("@exclude *://site.test/*").accepts("https://site.test/a"))
        XCTAssertFalse(patterns("@match *://site.test/*").accepts("not a url"))
        XCTAssertFalse(patterns("@match *://site.test/*").accepts("mailto:a@site.test"))
    }

    func testEscapedHostsAreDecoded() {
        let rules = patterns("@match *://book.\(backslash)u0073\(backslash)u0069te.test/info/*", "@exclude *://book.site.test/info/0")
        XCTAssertTrue(rules.accepts("https://book.site.test/info/1"))
        XCTAssertFalse(rules.accepts("https://book.site.test/info/0"))
        XCTAssertEqual(UserScriptPatterns.decode("\(backslash)u0073\(backslash)u0069te"), "site")
        // An incomplete or invalid escape is left as written.
        XCTAssertEqual(UserScriptPatterns.decode("\(backslash)u12"), "\(backslash)u12")
        XCTAssertEqual(UserScriptPatterns.decode("a\(backslash)uzzzzb"), "a\(backslash)uzzzzb")
    }

    func testIncludeAcceptsRegularExpressionsAndGlobs() {
        let rules = patterns(#"@include /^https?:\/\/(?:www\.)?toon\d+\.com\/novel\//"#, "@include *://glob.test/n/*")
        XCTAssertTrue(rules.accepts("https://www.toon12.com/novel/3"))
        XCTAssertTrue(rules.accepts("http://toon7.com/novel/3"))
        XCTAssertFalse(rules.accepts("https://toon7.com/webtoon/3"))
        XCTAssertTrue(rules.accepts("https://glob.test/n/1"))
        XCTAssertEqual(rules.allowCount, 2)
    }

    func testUnusableLinesAreSkipped() {
        let rules = patterns("@match not-a-pattern", "@match *://site.test/ok", "@name something", "@include /unterminated(")
        XCTAssertEqual(rules.allowCount, 1)
        XCTAssertNil(UserScriptPatterns.compile("no scheme here"))
    }

    func testTheHeaderEndsAtItsClosingLine() {
        let rules = UserScriptPatterns.parse(
            "// ==UserScript==\n// @match *://a.test/*\n// ==/UserScript==\n// @match *://b.test/*\n"
        )
        XCTAssertTrue(rules.accepts("https://a.test/x"))
        XCTAssertFalse(rules.accepts("https://b.test/x"))
    }

    func testTheShippedRulesBundleHasHundredsOfPagePatterns() throws {
        let bundle = try XCTUnwrap(EngineStore.shared.current(.novelDownloader))
        let rules = UserScriptPatterns.parse(try bundle.read())
        XCTAssertGreaterThan(rules.allowCount, 200, "match lines: \(rules.allowCount)")
        XCTAssertGreaterThan(rules.denyCount, 50, "exclude lines: \(rules.denyCount)")
    }
}
