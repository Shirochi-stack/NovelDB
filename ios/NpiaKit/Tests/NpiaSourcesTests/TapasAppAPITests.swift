import XCTest
import NpiaCore
@testable import NpiaSources

final class TapasAppAPITests: XCTestCase {
    func testLoggedInDetectsTheRedirectAwayFromSignIn() {
        XCTAssertTrue(TapasAppAPI.loggedIn(finalURL: URL(string: "https://host/")!, body: "<html>home</html>"))
        XCTAssertFalse(TapasAppAPI.loggedIn(finalURL: URL(string: "https://host/account/signin")!, body: "x"))
        XCTAssertFalse(TapasAppAPI.loggedIn(finalURL: URL(string: "https://host/account/authenticate")!, body: "x"))
        // A failed login re-renders the sign-in form (which posts to /account/authenticate).
        XCTAssertFalse(TapasAppAPI.loggedIn(finalURL: URL(string: "https://host/")!, body: "<form action=\"/account/authenticate\">"))
    }

    func testLoginKeepsTheJarCookiesOnSuccess() async {
        let cookies = FakeCookies(["_ses": "abc"])
        let http = RoutedHTTP()
        http.on("signin", "ok")
        http.on("authenticate", "<html>home</html>", finalURL: URL(string: "https://host/")!)
        let secrets = InMemorySecretStore()
        let ok = await TapasAppAPI.login(username: "user@example.com", password: "pw", http: http, secrets: secrets, cookies: cookies)
        XCTAssertTrue(ok)
        XCTAssertEqual(secrets.string(for: TapasAppAPI.cookieKey), "_ses=abc")
    }

    func testLoginFailsWhenStillOnTheSignInPage() async {
        let http = RoutedHTTP()
        http.on("signin", "ok")
        http.on("authenticate", "<form action=\"/account/authenticate\">", finalURL: URL(string: "https://host/account/signin")!)
        let secrets = InMemorySecretStore()
        let ok = await TapasAppAPI.login(username: "u", password: "p", http: http, secrets: secrets, cookies: FakeCookies())
        XCTAssertFalse(ok)
        XCTAssertNil(secrets.string(for: TapasAppAPI.cookieKey))
    }

    func testLoginRejectsEmptyCredentials() async {
        let ok = await TapasAppAPI.login(username: "", password: "", http: RoutedHTTP(), secrets: InMemorySecretStore(), cookies: FakeCookies())
        XCTAssertFalse(ok)
    }
}
