import CryptoKit
import Foundation
import NpiaCore
import Security

/// The WEBTOON app API's email + password sign-in, a port of `login_webtoons_app` in the desktop
/// `external_scraper.py`: `getRsaKey.json` hands back an RSA public key; the password (with the session key
/// and email, length-prefixed) is RSA-encrypted and POSTed to `loginById.json`, which returns the session.
/// Every request is HMAC-SHA1 signed (`msgpad`/`md`). webtoons is not a `SourceID` adapter on the phone —
/// its episodes download through the generic reader engine — so the session cookie is written into the store
/// every request shares (see `GenericCookieSession`).
///
/// The app API's host, signing key, user agents and session cookie name are kept out of the source as
/// plaintext (a base64 blob decoded at runtime, as on the desktop) per the repository's naming policy; it is
/// not a security measure.
public enum WebtoonsAppAPI {
    public static let domain = "\u{77}\u{65}\u{62}\u{74}\u{6f}\u{6f}\u{6e}\u{73}.com"
    public static let cookieKey = SecretKey(rawValue: "webtoons.appCookie")

    /// The cookie is valid for the whole domain; downloads use both the desktop and the phone host.
    public static let session = GenericCookieSession(
        key: cookieKey,
        cookieURLs: [URL(string: "https://www.\(domain)/")!, URL(string: "https://m.\(domain)/")!],
        domain: domain
    )

    private static let blob =
        "eyJhcGkiOiJodHRwczovL2dsb2JhbC5hcGlzLm5hdmVyLmNvbS9saW5lV2VidG9vbi93ZWJ0b29uLyIsInRpbWUiOiJodHRwczovL2dsb2JhbC5hcGlzLm5hdmVyLmNvbS9jdXJyZW50VGltZSIsImtleSI6ImdVdFB6SkZaY2g0WnlBR3ZpaXlIOTRQOTlsUTNwRmRSVHdwSldEbFNHRmZ3Z3ByNnNlczVBTE94V0hPSVQ3UjEiLCJ1YSI6Im5BcHBzIChBbmRyb2lkIDEzOyBsaW5ld2VidG9vbjsgMi4xMi4yKSIsImxvZ2ludWEiOiJBbmRyb2lkLzkgTW9kZWwvU00tTjk3NUYgY29tLm5hdmVyLmxpbmV3ZWJ0b29uLzIuMTIuNSgyMTIwNTAwLHVpZDoxMDA1OSkgTmVvSWRTaWduSW5Nb2QvMC4xLjEyIiwic2VzIjoiTkVPX1NFUyJ9"

    private static let config: [String: String] = {
        guard let data = Data(base64Encoded: blob),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: String] else { return [:] }
        return json
    }()

    private static func cfg(_ key: String) -> String { config[key] ?? "" }

    /// The app API's own clock (raw bytes); both `msgpad` and the signature use it verbatim.
    static func serverTime(_ http: any HTTPFetching) async throws -> Data {
        try await http.send(HTTPRequest(url: URL(string: cfg("time"))!, headers: ["User-Agent": cfg("ua")])).body
    }

    /// Appends the app API's `msgpad`/`md` signature (HMAC-SHA1 of the URL's first 255 bytes + time).
    static func sign(_ url: String, time: Data) -> String {
        let key = SymmetricKey(data: Data(cfg("key").utf8))
        let urlBytes = Array(url.utf8).prefix(255)
        let mac = HMAC<Insecure.SHA1>.authenticationCode(for: Data(urlBytes) + time, using: key)
        let md = Data(mac).base64EncodedString()
        let separator = url.contains("?") ? "&" : "?"
        return "\(url)\(separator)msgpad=\(quote(String(decoding: time, as: UTF8.self)))&md=\(quote(md))"
    }

    /// RSAES-PKCS1-v1_5 encrypt with the public key (modulus, exponent); returns lowercase hex, or nil.
    static func rsaEncryptHex(_ message: Data, modulus: [UInt8], exponent: [UInt8]) -> String? {
        let der = Data(derSequence(derInteger(modulus) + derInteger(exponent)))
        let attributes: [String: Any] = [
            kSecAttrKeyType as String: kSecAttrKeyTypeRSA,
            kSecAttrKeyClass as String: kSecAttrKeyClassPublic,
        ]
        guard let key = SecKeyCreateWithData(der as CFData, attributes as CFDictionary, nil),
              let cipher = SecKeyCreateEncryptedData(key, .rsaEncryptionPKCS1, message as CFData, nil) else { return nil }
        return (cipher as Data).map { String(format: "%02x", $0) }.joined()
    }

    /// Signs in with an email and password and keeps the `NEO_SES` session cookie. Returns false (and logs
    /// the reason) when the reply carried no session.
    @discardableResult
    public static func login(
        email: String,
        password: String,
        http: any HTTPFetching,
        secrets: any SecretStore,
        cookies: any SessionCookieAccess,
        logger: (any JobLogging)? = nil
    ) async -> Bool {
        let user = email.trimmingCharacters(in: .whitespaces)
        guard !user.isEmpty, !password.isEmpty else { return false }
        let keyURL = cfg("api") + "getRsaKey.json?v=1&platform=APP_ANDROID&language=en&serviceZone=GLOBAL&locale=en"
        guard let time1 = try? await serverTime(http),
              let signedKeyURL = URL(string: sign(keyURL, time: time1)),
              let keyResponse = try? await http.send(HTTPRequest(url: signedKeyURL, headers: ["User-Agent": cfg("ua")])),
              let keyResult = result(keyResponse.body) else {
            logger?.log(.warning, "[Webtoons] Could not reach the login key endpoint.")
            return false
        }
        let sessionKey = string(keyResult, "sessionKey")
        let keyName = string(keyResult, "keyName")
        let nValue = string(keyResult, "nvalue")
        let eValue = string(keyResult, "evalue")
        guard !sessionKey.isEmpty, !keyName.isEmpty, !nValue.isEmpty, !eValue.isEmpty else {
            logger?.log(.warning, "[Webtoons] Could not get a login key (signing or endpoint changed).")
            return false
        }
        var plain = Data()
        for part in [sessionKey, user, password] {
            plain.append(UInt8(part.count & 0xFF))
            plain.append(contentsOf: Array(part.utf8))
        }
        // This endpoint labels the exponent "nvalue" and the modulus "evalue" (the reverse of the usual nid
        // naming), so pick them by size: the modulus is always the larger integer.
        let (modulus, exponent) = byMagnitude(hexBytes(nValue), hexBytes(eValue))
        guard let encpw = rsaEncryptHex(plain, modulus: modulus, exponent: exponent) else {
            logger?.log(.warning, "[Webtoons] Could not encrypt the password.")
            return false
        }
        let fields = [("v", "2"), ("encnm", keyName), ("encpw", encpw), ("language", "en"), ("loginType", "EMAIL"), ("serviceZone", "GLOBAL")]
        let form = fields.map { "\($0.0)=\(formEncode($0.1))" }.joined(separator: "&")
        guard let time2 = try? await serverTime(http),
              let signedLoginURL = URL(string: sign(cfg("api") + "loginById.json", time: time2)),
              let loginResponse = try? await http.send(HTTPRequest(
                  url: signedLoginURL,
                  method: "POST",
                  headers: ["User-Agent": cfg("loginua"), "Content-Type": "application/x-www-form-urlencoded"],
                  body: Data(form.utf8))),
              let loginResult = result(loginResponse.body) else { return false }
        let ses = string(loginResult, "ses")
        guard !ses.isEmpty else {
            let status = HTTPSourceSupport.int(loginResult["login_status"])
            let reason: String
            switch status {
            case 110, 131: reason = "wrong email or password"
            case 151: reason = "a captcha or extra verification is required — sign in with Enter Browser instead"
            default: reason = "login_status=\(status); try the Enter Browser login instead"
            }
            logger?.log(.warning, "[Webtoons] Login failed: \(reason)")
            return false
        }
        return await session.save("\(cfg("ses"))=\(ses)", secrets: secrets, cookies: cookies)
    }

    // MARK: - Helpers

    private static func result(_ body: Data) -> [String: Any]? {
        guard let payload = try? HTTPSourceSupport.jsonObject(body),
              let message = payload["message"] as? [String: Any] else { return nil }
        return message["result"] as? [String: Any]
    }

    private static func string(_ result: [String: Any]?, _ key: String) -> String {
        HTTPSourceSupport.string(result?[key])
    }

    /// Percent-encodes like Python's `urllib.parse.quote` (default safe `/`).
    private static func quote(_ value: String) -> String {
        var allowed = CharacterSet.alphanumerics
        allowed.insert(charactersIn: "_.-~/")
        return value.addingPercentEncoding(withAllowedCharacters: allowed) ?? value
    }

    private static func formEncode(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? value
    }

    /// Bytes of a big-endian hex integer (a leading `0` is added when the hex has an odd length).
    private static func hexBytes(_ hex: String) -> [UInt8] {
        let padded = hex.count % 2 == 0 ? hex : "0" + hex
        var bytes: [UInt8] = []
        var index = padded.startIndex
        while index < padded.endIndex {
            let next = padded.index(index, offsetBy: 2)
            bytes.append(UInt8(padded[index..<next], radix: 16) ?? 0)
            index = next
        }
        return bytes
    }

    /// The larger magnitude first (modulus), then the smaller (exponent).
    private static func byMagnitude(_ a: [UInt8], _ b: [UInt8]) -> ([UInt8], [UInt8]) {
        func stripped(_ bytes: [UInt8]) -> [UInt8] { Array(bytes.drop { $0 == 0 }) }
        let sa = stripped(a)
        let sb = stripped(b)
        if sa.count != sb.count { return sa.count > sb.count ? (a, b) : (b, a) }
        for (x, y) in zip(sa, sb) where x != y { return x > y ? (a, b) : (b, a) }
        return (a, b)
    }

    // MARK: - Minimal ASN.1 DER (RSAPublicKey PKCS#1: SEQUENCE { INTEGER modulus, INTEGER exponent })

    private static func derLength(_ count: Int) -> [UInt8] {
        if count < 0x80 { return [UInt8(count)] }
        var value = count
        var bytes: [UInt8] = []
        while value > 0 { bytes.insert(UInt8(value & 0xFF), at: 0); value >>= 8 }
        return [UInt8(0x80 | bytes.count)] + bytes
    }

    private static func derInteger(_ bytes: [UInt8]) -> [UInt8] {
        var trimmed = Array(bytes.drop { $0 == 0 })
        if trimmed.isEmpty { trimmed = [0] }
        if trimmed[0] & 0x80 != 0 { trimmed.insert(0, at: 0) } // keep it positive
        return [0x02] + derLength(trimmed.count) + trimmed
    }

    private static func derSequence(_ content: [UInt8]) -> [UInt8] {
        [0x30] + derLength(content.count) + content
    }
}
