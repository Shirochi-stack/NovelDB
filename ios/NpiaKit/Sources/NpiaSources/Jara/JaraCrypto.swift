import CommonCrypto
import Foundation

/// Chapter text cipher: CryptoJS AES-CBC with a UTF-8 key and IV, PKCS#7 padding.
enum JaraCrypto {
    enum Failure: Error {
        case badInput
        case cipher(Int32)
        case notUTF8
    }

    static func decrypt(_ base64: String, key: String, iv: String) throws -> String {
        guard let data = Data(base64Encoded: base64, options: .ignoreUnknownCharacters), !data.isEmpty else { throw Failure.badInput }
        let plain = try crypt(CCOperation(kCCDecrypt), data, key: Data(key.utf8), iv: Data(iv.utf8))
        guard let text = String(data: plain, encoding: .utf8) else { throw Failure.notUTF8 }
        return text
    }

    /// Used by tests to build fixtures.
    static func encrypt(_ text: String, key: String, iv: String) throws -> String {
        try crypt(CCOperation(kCCEncrypt), Data(text.utf8), key: Data(key.utf8), iv: Data(iv.utf8)).base64EncodedString()
    }

    private static func crypt(_ operation: CCOperation, _ input: Data, key: Data, iv: Data) throws -> Data {
        guard [kCCKeySizeAES128, kCCKeySizeAES192, kCCKeySizeAES256].contains(key.count), iv.count == kCCBlockSizeAES128 else {
            throw Failure.badInput
        }
        var output = Data(count: input.count + kCCBlockSizeAES128)
        let capacity = output.count
        var moved = 0
        let status = output.withUnsafeMutableBytes { outBytes in
            input.withUnsafeBytes { inBytes in
                key.withUnsafeBytes { keyBytes in
                    iv.withUnsafeBytes { ivBytes in
                        CCCrypt(
                            operation, CCAlgorithm(kCCAlgorithmAES), CCOptions(kCCOptionPKCS7Padding),
                            keyBytes.baseAddress, key.count, ivBytes.baseAddress,
                            inBytes.baseAddress, input.count,
                            outBytes.baseAddress, capacity, &moved
                        )
                    }
                }
            }
        }
        guard Int(status) == kCCSuccess else { throw Failure.cipher(status) }
        output.count = moved
        return output
    }
}
