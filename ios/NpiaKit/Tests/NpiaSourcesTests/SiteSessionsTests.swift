import XCTest
import NpiaCore
@testable import NpiaSources

final class SiteSessionsTests: XCTestCase {
    private let qdn = HardSessions.qdn
    private let rbooks = HardSessions.rbooks
    private let copyKey = SecretKey.source(.qdn, "sessionCookies")
    private let rbooksCopyKey = SecretKey.source(.rbooks, "sessionCookies")

    override func setUp() {
        SiteSessions.resetProcessState()
    }

    private func sessions(_ secrets: InMemorySecretStore, _ cookies: FakeCookies) -> SiteSessions {
        SiteSessions(secrets: secrets, cookies: cookies)
    }

    // MARK: What counts as a login

    func testQdnNeedsBothLoginCookies() async {
        let none = sessions(InMemorySecretStore(), FakeCookies())
        let noneStatus = await none.status(qdn)
        XCTAssertFalse(noneStatus.usable)
        XCTAssertTrue(noneStatus.present.isEmpty)

        let half = sessions(InMemorySecretStore(), FakeCookies(["ywkey": "k"]))
        let halfStatus = await half.status(qdn)
        XCTAssertFalse(halfStatus.usable)
        XCTAssertEqual(halfStatus.present, ["ywkey"])

        let both = sessions(InMemorySecretStore(), FakeCookies(["ywkey": "k", "ywguid": "g", "unrelated": "x"]))
        let bothStatus = await both.status(qdn)
        XCTAssertTrue(bothStatus.usable)
        XCTAssertEqual(bothStatus.present, ["ywkey", "ywguid"])
    }

    func testEmptyCookieValuesAreNotALogin() async {
        let empty = sessions(InMemorySecretStore(), FakeCookies(["ywkey": "", "ywguid": ""]))
        let status = await empty.status(qdn)
        XCTAssertFalse(status.usable)
    }

    func testRbooksNeedsAnAccessOrRefreshToken() async {
        for name in [RbooksSite.accessCookie, RbooksSite.refreshCookie, RbooksSite.globalCookie] {
            let signedIn = sessions(InMemorySecretStore(), FakeCookies([name: "t"]))
            let status = await signedIn.status(rbooks)
            XCTAssertTrue(status.usable, name)
        }
        let other = sessions(InMemorySecretStore(), FakeCookies(["ywkey": "k", "ywguid": "g"]))
        let status = await other.status(rbooks)
        XCTAssertFalse(status.usable)
    }

    // MARK: The saved copy

    func testTheLoginIsCopiedToTheSecretStoreAndOnlyThoseCookiesAreKept() async {
        let secrets = InMemorySecretStore()
        let cookies = FakeCookies(["ywkey": "k1", "ywguid": "g1", "tracking": "secret-looking"])
        let store = sessions(secrets, cookies)
        let before = await store.status(qdn)
        XCTAssertFalse(before.hasSavedCopy)
        let saved = await store.snapshot(qdn)
        XCTAssertEqual(saved, 2)
        let copy = secrets.string(for: copyKey) ?? ""
        XCTAssertTrue(copy.contains("k1") && copy.contains("g1"))
        XCTAssertFalse(copy.contains("tracking") || copy.contains("secret-looking"))
        let after = await store.status(qdn)
        XCTAssertTrue(after.hasSavedCopy)
    }

    func testABrowserThatNeverOpenedTheSiteKeepsTheEarlierCopy() async {
        let secrets = InMemorySecretStore()
        await sessions(secrets, FakeCookies(["ywkey": "k1", "ywguid": "g1"])).snapshot(qdn)
        let before = secrets.string(for: copyKey)
        XCTAssertNotNil(before)
        let saved = await sessions(secrets, FakeCookies()).snapshot(qdn)
        XCTAssertEqual(saved, 0)
        XCTAssertEqual(secrets.string(for: copyKey), before)
    }

    func testALaterLoginReplacesTheCopy() async {
        let secrets = InMemorySecretStore()
        await sessions(secrets, FakeCookies(["ywkey": "old", "ywguid": "old"])).snapshot(qdn)
        await sessions(secrets, FakeCookies(["ywkey": "new", "ywguid": "new"])).snapshot(qdn)
        let copy = secrets.string(for: copyKey) ?? ""
        XCTAssertTrue(copy.contains("new"))
        XCTAssertFalse(copy.contains("old"))
    }

    func testTheCopiesOfTheTwoSitesAreSeparate() async {
        let secrets = InMemorySecretStore()
        let store = sessions(secrets, FakeCookies(["ywkey": "k", "ywguid": "g", RbooksSite.accessCookie: "at"]))
        await store.snapshot(qdn)
        await store.snapshot(rbooks)
        XCTAssertNotNil(secrets.string(for: copyKey))
        XCTAssertNotNil(secrets.string(for: rbooksCopyKey))
        XCTAssertFalse((secrets.string(for: copyKey) ?? "").contains("at\""))
        XCTAssertFalse((secrets.string(for: copyKey) ?? "").contains(RbooksSite.accessCookie))
    }

    // MARK: Restore

    func testLostSessionCookiesComeBackFromTheCopy() async {
        let secrets = InMemorySecretStore()
        await sessions(secrets, FakeCookies(["ywkey": "k1", "ywguid": "g1"])).snapshot(qdn)
        let restarted = FakeCookies()
        let store = sessions(secrets, restarted)
        let restored = await store.restore(qdn)
        XCTAssertEqual(restored, 2)
        XCTAssertEqual(restarted.values, ["ywkey": "k1", "ywguid": "g1"])
        let status = await store.status(qdn)
        XCTAssertTrue(status.usable)
    }

    func testRestoreNeverOverwritesACookieTheBrowserHas() async {
        let secrets = InMemorySecretStore()
        await sessions(secrets, FakeCookies(["ywkey": "saved", "ywguid": "saved"])).snapshot(qdn)
        let cookies = FakeCookies(["ywkey": "newer"])
        let restored = await sessions(secrets, cookies).restore(qdn)
        XCTAssertEqual(restored, 1)
        XCTAssertEqual(cookies.values["ywkey"], "newer")
        XCTAssertEqual(cookies.values["ywguid"], "saved")
    }

    func testRestoreOnlyHappensOncePerProcess() async {
        let secrets = InMemorySecretStore()
        await sessions(secrets, FakeCookies(["ywkey": "k", "ywguid": "g"])).snapshot(qdn)
        let cookies = FakeCookies()
        let store = sessions(secrets, cookies)
        let first = await store.restoreOnce(qdn)
        XCTAssertEqual(first, 2)
        cookies.clear() // the user signed out in the browser afterwards
        let second = await store.restoreOnce(qdn)
        XCTAssertEqual(second, 0)
        XCTAssertTrue(cookies.values.isEmpty)
    }

    func testADamagedCopyIsIgnored() async {
        let broken = InMemorySecretStore([copyKey: "{not json"])
        let cookies = FakeCookies()
        let restored = await sessions(broken, cookies).restore(qdn)
        XCTAssertEqual(restored, 0)
        XCTAssertTrue(cookies.values.isEmpty)

        let odd = InMemorySecretStore([copyKey: "{\"cookies\":{\"sneaky\":\"x\",\"ywkey\":\"k\",\"ywguid\":42,\"ywopenid\":\"\"}}"])
        let kept = FakeCookies()
        await sessions(odd, kept).restore(qdn)
        // Only known login cookie names with real values come back.
        XCTAssertEqual(kept.values, ["ywkey": "k", "ywguid": "42"])
    }

    // MARK: Sign out

    func testSignOutRemovesTheCookiesOnEveryHostAndTheCopy() async {
        let secrets = InMemorySecretStore()
        let cookies = FakeCookies(["ywkey": "k", "ywguid": "g", "other": "x"])
        let store = sessions(secrets, cookies)
        await store.snapshot(qdn)
        await store.signOut(qdn)
        XCTAssertEqual(cookies.values, ["other": "x"])
        XCTAssertNil(secrets.string(for: copyKey))
        let status = await store.status(qdn)
        XCTAssertFalse(status.usable)
        // The saved copy is gone, so the next start cannot bring the login back.
        let restored = await store.restore(qdn)
        XCTAssertEqual(restored, 0)
    }

    func testSigningOutOfOneSiteLeavesTheOther() async {
        let secrets = InMemorySecretStore()
        let cookies = FakeCookies(["ywkey": "k", "ywguid": "g", RbooksSite.accessCookie: "at"])
        let store = sessions(secrets, cookies)
        await store.snapshot(qdn)
        await store.snapshot(rbooks)
        await store.signOut(qdn)
        let status = await store.status(rbooks)
        XCTAssertTrue(status.usable)
        XCTAssertNotNil(secrets.string(for: rbooksCopyKey))
    }

    func testSignOutAsksEveryHostOfTheSiteToDeleteEveryLoginCookie() async {
        let cookies = FakeCookies(["ywkey": "k"])
        await sessions(InMemorySecretStore(), cookies).signOut(qdn)
        // Three login cookie names; the fake counts one removal per call.
        XCTAssertEqual(cookies.removed.sorted(), ["ywguid", "ywguid", "ywkey", "ywkey", "ywopenid", "ywopenid"])
    }

    func testTheBrowserTabsSignOutOnlyDropsTheCopy() async {
        let secrets = InMemorySecretStore()
        let cookies = FakeCookies(["ywkey": "k", "ywguid": "g"])
        let store = sessions(secrets, cookies)
        await store.snapshot(qdn)
        store.forgetSavedCopy(qdn)
        XCTAssertNil(secrets.string(for: copyKey))
        XCTAssertEqual(cookies.values.count, 2)
    }

    // MARK: The sites

    func testTheSitesAreFoundByTheirHosts() {
        XCTAssertEqual(HardSessions.forHost("www." + QdnSite.domain).map(\.source), [.qdn])
        XCTAssertEqual(HardSessions.forHost("m." + QdnSite.domain).map(\.source), [.qdn])
        XCTAssertEqual(HardSessions.forHost(QdnSite.domain).map(\.source), [.qdn])
        XCTAssertEqual(HardSessions.forHost("account." + RbooksSite.domain).map(\.source), [.rbooks])
        XCTAssertTrue(HardSessions.forHost("example.com").isEmpty)
        XCTAssertEqual(HardSessions.of(.qdn)?.source, .qdn)
        XCTAssertEqual(HardSessions.of(.rbooks)?.source, .rbooks)
        XCTAssertNil(HardSessions.of(.sfc))
    }

    func testCookieNamesAndSignInPagesAreTheDocumentedOnes() {
        XCTAssertEqual(qdn.cookieNames, ["ywkey", "ywguid", "ywopenid"])
        XCTAssertEqual(qdn.signInURL.absoluteString, "https://m." + QdnSite.domain + "/user/")
        XCTAssertEqual(qdn.cookieURLs.map { $0.host ?? "" }, ["www." + QdnSite.domain, "m." + QdnSite.domain])
        XCTAssertTrue(RbooksSite.loginCookies.allSatisfy { $0.hasSuffix("-at") || $0.hasSuffix("-rt") })
        XCTAssertTrue(rbooks.signInURL.absoluteString.hasSuffix("/account/login"))
        XCTAssertEqual(rbooks.cookieURLs.compactMap(\.host), [RbooksSite.domain, "account." + RbooksSite.domain, "library." + RbooksSite.domain])
    }

    // MARK: What the screen says

    func testTheStatusLineSaysWhatIsMissing() {
        let signedIn = SessionStatus(usable: true, present: ["ywkey", "ywguid"], hasSavedCopy: true)
        XCTAssertEqual(signedIn.summary(for: qdn), "Signed in (found ywguid, ywkey).")
        let partly = SessionStatus(usable: false, present: ["ywkey"], hasSavedCopy: false)
        XCTAssertEqual(partly.summary(for: qdn), "Partly signed in: found ywkey, but ywkey and ywguid are needed.")
        let saved = SessionStatus(usable: false, present: [], hasSavedCopy: true)
        XCTAssertEqual(saved.summary(for: qdn), "Not signed in. A saved copy exists and is restored when a download starts.")
        let none = SessionStatus(usable: false, present: [], hasSavedCopy: false)
        XCTAssertEqual(none.summary(for: qdn), "Not signed in. Free chapters still download.")
    }

    // MARK: Cookie domains and the HTTP cookie jar

    func testADomainCookieGoesToSubdomainsAndAHostCookieOnlyToItsHost() {
        XCTAssertTrue(CookieDomain.covers(".example.com", host: "www.example.com"))
        XCTAssertTrue(CookieDomain.covers(".example.com", host: "example.com"))
        XCTAssertFalse(CookieDomain.covers(".example.com", host: "badexample.com"))
        XCTAssertTrue(CookieDomain.covers("www.example.com", host: "www.example.com"))
        XCTAssertFalse(CookieDomain.covers("www.example.com", host: "a.www.example.com"))
        XCTAssertFalse(CookieDomain.covers("example.com", host: "www.example.com"))
        XCTAssertFalse(CookieDomain.covers("", host: "example.com"))
    }

    func testTheCookieJarStoresReadsAndDeletesALogin() async {
        let jar = HTTPCookieStorageAccess()
        let url = URL(string: "https://www." + QdnSite.domain + "/")!
        let name = "test_login_\(UUID().uuidString.prefix(8))"
        await jar.setCookie(name: name, value: "v1", url: url, domain: "." + QdnSite.domain)
        let seenOnDesktop = await jar.cookies(for: url)
        XCTAssertEqual(seenOnDesktop[name], "v1")
        let seenOnMobile = await jar.cookies(for: URL(string: "https://m." + QdnSite.domain + "/")!)
        XCTAssertEqual(seenOnMobile[name], "v1")
        let seenElsewhere = await jar.cookies(for: URL(string: "https://example.com/")!)
        XCTAssertNil(seenElsewhere[name])
        await jar.removeCookie(name: name, url: url)
        let gone = await jar.cookies(for: url)
        XCTAssertNil(gone[name])
    }
}
