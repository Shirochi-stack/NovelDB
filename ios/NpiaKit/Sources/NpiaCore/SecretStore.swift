import Foundation
import Security

/// Credentials never live in `AppSettings`; they go through this store.
public protocol SecretStore: Sendable {
    func string(for key: SecretKey) -> String?
    func set(_ value: String?, for key: SecretKey)
}

public struct SecretKey: RawRepresentable, Hashable, Sendable {
    public var rawValue: String
    public init(rawValue: String) { self.rawValue = rawValue }

    public static let npiaEmail = SecretKey(rawValue: "npia.email")
    public static let npiaPassword = SecretKey(rawValue: "npia.password")
    public static let npiaLoginKey = SecretKey(rawValue: "npia.loginkey")
    public static let googleDriveRefreshToken = SecretKey(rawValue: "upload.gdrive.refresh")

    public static func source(_ id: SourceID, _ name: String) -> SecretKey {
        SecretKey(rawValue: "\(id.rawValue).\(name)")
    }
}

public final class KeychainSecretStore: SecretStore, @unchecked Sendable {
    private let service: String

    public init(service: String = "com.shirochi.npiamobile") {
        self.service = service
    }

    private func query(_ key: SecretKey) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key.rawValue,
        ]
    }

    public func string(for key: SecretKey) -> String? {
        var request = query(key)
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        guard SecItemCopyMatching(request as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    public func set(_ value: String?, for key: SecretKey) {
        SecItemDelete(query(key) as CFDictionary)
        guard let value, !value.isEmpty else { return }
        var item = query(key)
        item[kSecValueData as String] = Data(value.utf8)
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(item as CFDictionary, nil)
    }
}

public final class InMemorySecretStore: SecretStore, @unchecked Sendable {
    private var values: [SecretKey: String] = [:]
    private let lock = NSLock()

    public init(_ values: [SecretKey: String] = [:]) {
        self.values = values
    }

    public func string(for key: SecretKey) -> String? {
        lock.lock(); defer { lock.unlock() }
        return values[key]
    }

    public func set(_ value: String?, for key: SecretKey) {
        lock.lock(); defer { lock.unlock() }
        values[key] = value
    }
}
