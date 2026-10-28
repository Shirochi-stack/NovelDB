import XCTest
@testable import NpiaWebEngine

/// Page translation in the Browser tab: the language setting, the translated copy's address and
/// the way back to the original site.
final class BrowserTranslateTests: XCTestCase {
    private func back(_ address: String) -> String? {
        BrowserTranslate.originalAddress(address)
    }

    func testTheLanguageSettingFallsBackToEnglish() {
        XCTAssertEqual(BrowserTranslate.defaultLanguage, "en")
        XCTAssertEqual(BrowserTranslate.language(nil).name, "English")
        XCTAssertEqual(BrowserTranslate.language("").name, "English")
        XCTAssertEqual(BrowserTranslate.language("xx").name, "English")
        XCTAssertEqual(BrowserTranslate.language("ko").code, "ko")
        XCTAssertEqual(BrowserTranslate.language("zh-cn").code, "zh-CN")
        let codes = BrowserTranslate.languages.map(\.code)
        XCTAssertTrue(Set(codes).isSuperset(of: ["en", "ko", "ja", "zh-CN", "es"]))
        XCTAssertEqual(codes.count, Set(codes).count)
    }

    func testTheTranslatedCopyAddressNamesTheLanguageAndThePage() {
        XCTAssertEqual(
            BrowserTranslate.copyAddress(of: "https://example.com/novel/1?a=b", language: "zh-CN"),
            "https://translate.google.com/translate?sl=auto&tl=zh-CN&u=https%3A%2F%2Fexample.com%2Fnovel%2F1%3Fa%3Db"
        )
        XCTAssertEqual(
            BrowserTranslate.copyURL(of: URL(string: "https://example.com/novel/1?a=b")!, language: "ko")?.host,
            "translate.google.com"
        )
        // A space is a plus, as Java's URLEncoder writes it.
        XCTAssertTrue(BrowserTranslate.copyAddress(of: "https://example.com/a b", language: "en").hasSuffix("u=https%3A%2F%2Fexample.com%2Fa+b"))
    }

    func testTheProxyAddressMapsBackToTheOriginalSite() {
        XCTAssertEqual(back("https://global-example-com.translate.goog/novel/123"), "https://global.example.com/novel/123")
        XCTAssertEqual(
            back("https://global-example-com.translate.goog/novel/123?_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en&page=2&_x_tr_pto=wapp"),
            "https://global.example.com/novel/123?page=2"
        )
        XCTAssertEqual(back("https://global-example-com.translate.goog/novel/123?_x_tr_sl=ko&_x_tr_tl=en"), "https://global.example.com/novel/123")
        XCTAssertEqual(back("https://global-example-com.translate.goog"), "https://global.example.com/")
        // A dash in the original host is doubled.
        XCTAssertEqual(back("https://my--site-example-com.translate.goog/a?_x_tr_sl=auto"), "https://my-site.example.com/a")
        XCTAssertEqual(back("https://example-com.translate.goog/a?_x_tr_sl=auto#part"), "https://example.com/a#part")
        // Google's own address carries the page in u.
        XCTAssertEqual(
            back("https://translate.google.com/translate?sl=auto&tl=en&u=https%3A%2F%2Fexample.com%2Fnovel%2F1%3Fa%3Db"),
            "https://example.com/novel/1?a=b"
        )
    }

    func testOtherAddressesAreNotTranslatedCopies() {
        XCTAssertNil(back("https://example.com/novel/1"))
        XCTAssertNil(back("https://translate.google.com/"))
        XCTAssertNil(back("https://translate.google.com/translate?u=javascript:alert(1)"))
        XCTAssertNil(back("https://evil.translate.goog.example.com/a"))
        XCTAssertNil(back("https://a.b.translate.goog/a"))
        XCTAssertNil(back("not a url"))
        XCTAssertFalse(BrowserTranslate.isTranslatedCopy(URL(string: "https://example.com/")!))
        XCTAssertTrue(BrowserTranslate.isTranslatedCopy(URL(string: "https://example-com.translate.goog/")!))
    }

    func testCopyAndOriginalRoundTrip() {
        // Google redirects the translate address to the proxy; the original comes back from either form.
        let page = "https://global.example.com/novel/77"
        XCTAssertEqual(back(BrowserTranslate.copyAddress(of: page, language: "ko")), page)
        let url = URL(string: "https://global-example-com.translate.goog/novel/77?_x_tr_sl=auto&_x_tr_tl=en")!
        XCTAssertEqual(BrowserTranslate.originalURL(of: url)?.absoluteString, page)
    }

    func testTheInjectedScriptStartsTheTranslatorInTheChosenLanguage() throws {
        let script = BrowserTranslate.injection(language: "zh-CN")
        // The script itself ships as a resource and drives Google's website translator.
        XCTAssertTrue(script.contains("translate_a/element.js"), "browser-translate.js is missing from the bundle")
        XCTAssertTrue(script.contains("goog-te-combo"))
        XCTAssertTrue(script.hasSuffix("window.NpiaTranslate && window.NpiaTranslate.start('zh-CN');"))
        // Nothing but a language code can reach the page's script.
        let hostile = BrowserTranslate.injection(language: "en');alert('x")
        XCTAssertTrue(hostile.hasSuffix("window.NpiaTranslate.start('enalertx');"), hostile)
    }
}
