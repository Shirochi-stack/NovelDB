import XCTest
import NpiaCore
import Security
@testable import NpiaSources

final class WebtoonsAppAPITests: XCTestCase {
    private func keyPair() -> (SecKey, SecKey) {
        let attributes: [String: Any] = [
            kSecAttrKeyType as String: kSecAttrKeyTypeRSA,
            kSecAttrKeySizeInBits as String: 2048,
        ]
        let priv = SecKeyCreateRandomKey(attributes as CFDictionary, nil)!
        return (SecKeyCopyPublicKey(priv)!, priv)
    }

    /// Modulus and exponent bytes from an RSA public key's PKCS#1 DER: SEQUENCE { INTEGER n, INTEGER e }.
    private func modulusExponent(_ pub: SecKey) -> ([UInt8], [UInt8]) {
        let der = Array(SecKeyCopyExternalRepresentation(pub, nil)! as Data)
        var i = 0
        func readLength() -> Int {
            var length = Int(der[i]); i += 1
            if length & 0x80 != 0 {
                let count = length & 0x7f
                length = 0
                for _ in 0..<count { length = (length << 8) | Int(der[i]); i += 1 }
            }
            return length
        }
        i += 1 // SEQUENCE tag
        _ = readLength()
        i += 1 // INTEGER tag (modulus)
        let modulusLength = readLength()
        let modulus = Array(der[i..<i + modulusLength]); i += modulusLength
        i += 1 // INTEGER tag (exponent)
        let exponentLength = readLength()
        let exponent = Array(der[i..<i + exponentLength])
        return (modulus, exponent)
    }

    private func hex(_ bytes: [UInt8]) -> String { bytes.map { String(format: "%02x", $0) }.joined() }

    func testRsaEncryptHexRoundTripsWithThePrivateKey() throws {
        let (pub, priv) = keyPair()
        let (modulus, exponent) = modulusExponent(pub)
        let message = Data("hello, webtoons".utf8)
        let hexCipher = try XCTUnwrap(WebtoonsAppAPI.rsaEncryptHex(message, modulus: modulus, exponent: exponent))
        var cipher = Data()
        var i = hexCipher.startIndex
        while i < hexCipher.endIndex {
            let j = hexCipher.index(i, offsetBy: 2)
            cipher.append(UInt8(hexCipher[i..<j], radix: 16)!)
            i = j
        }
        let plain = SecKeyCreateDecryptedData(priv, .rsaEncryptionPKCS1, cipher as CFData, nil)! as Data
        XCTAssertEqual(plain, message)
    }

    func testSignAppendsMsgpadAndMdAndIsDeterministic() {
        let time = Data("1700000000000".utf8)
        let noQuery = WebtoonsAppAPI.sign("https://host/path", time: time)
        XCTAssertTrue(noQuery.contains("?msgpad="))
        XCTAssertTrue(noQuery.contains("&md="))
        let withQuery = WebtoonsAppAPI.sign("https://host/path?v=1", time: time)
        XCTAssertTrue(withQuery.contains("&msgpad="))
        XCTAssertEqual(noQuery, WebtoonsAppAPI.sign("https://host/path", time: time))
    }

    func testLoginStoresTheSessionCookieOnSuccess() async {
        let (pub, _) = keyPair()
        let (modulus, exponent) = modulusExponent(pub)
        let http = scripted(modulusHex: hex(modulus), exponentHex: hex(exponent), loginResult: ["ses": "SESVALUE"])
        let cookies = FakeCookies()
        let secrets = InMemorySecretStore()
        let ok = await WebtoonsAppAPI.login(email: "a@b.com", password: "pw", http: http, secrets: secrets, cookies: cookies)
        XCTAssertTrue(ok)
        XCTAssertEqual(secrets.string(for: WebtoonsAppAPI.cookieKey), "NEO_SES=SESVALUE")
        XCTAssertEqual(cookies.values["NEO_SES"], "SESVALUE")
    }

    func testLoginFailsAndSavesNothingWhenNoSessionComesBack() async {
        let (pub, _) = keyPair()
        let (modulus, exponent) = modulusExponent(pub)
        let http = scripted(modulusHex: hex(modulus), exponentHex: hex(exponent), loginResult: ["login_status": 110])
        let secrets = InMemorySecretStore()
        let ok = await WebtoonsAppAPI.login(email: "a@b.com", password: "pw", http: http, secrets: secrets, cookies: FakeCookies())
        XCTAssertFalse(ok)
        XCTAssertNil(secrets.string(for: WebtoonsAppAPI.cookieKey))
    }

    func testLoginRejectsEmptyCredentials() async {
        let ok = await WebtoonsAppAPI.login(email: "", password: "", http: RoutedHTTP(), secrets: InMemorySecretStore(), cookies: FakeCookies())
        XCTAssertFalse(ok)
    }

    /// Serves the server clock, an RSA key (exponent as `nvalue`, modulus as `evalue`), and a login result.
    private func scripted(modulusHex: String, exponentHex: String, loginResult: [String: Any]) -> RoutedHTTP {
        let http = RoutedHTTP()
        http.on("currentTime", "1700000000000")
        http.on("getRsaKey.json", json: ["message": ["result": ["sessionKey": "SK", "keyName": "KN", "nvalue": exponentHex, "evalue": modulusHex]]])
        http.on("loginById.json", json: ["message": ["result": loginResult]])
        return http
    }
}
