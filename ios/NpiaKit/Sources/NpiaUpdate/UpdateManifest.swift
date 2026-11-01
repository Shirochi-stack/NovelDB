import Foundation

/// One file of a release, as `update-ios.json` lists it.
public struct AppUpdateAsset: Equatable, Sendable {
    public var name: String
    public var variant: String
    public var arch: String
    public var kind: String
    /// The build number the release workflow gave this file; the app's own build is compared with it.
    public var build: Int
    public var label: String
    public var commit: String
    public var size: Int64
    /// 64 lowercase hex digits.
    public var sha256: String

    public init(
        name: String, variant: String, arch: String, kind: String, build: Int,
        label: String = "", commit: String = "", size: Int64, sha256: String
    ) {
        self.name = name
        self.variant = variant
        self.arch = arch
        self.kind = kind
        self.build = build
        self.label = label
        self.commit = commit
        self.size = size
        self.sha256 = sha256
    }

    /// Nil unless the entry has a name, a kind, a positive build and size, and a SHA-256.
    init?(json: [String: Any]) {
        guard let name = json["name"] as? String, !name.isEmpty,
              let kind = json["kind"] as? String, !kind.isEmpty,
              let build = UpdateFields.integer(json["build"]), build > 0,
              let size = UpdateFields.integer(json["size"]), size > 0,
              let sha = (json["sha256"] as? String)?.lowercased(), Self.isSHA256(sha)
        else { return nil }
        self.init(
            name: name,
            variant: json["variant"] as? String ?? "",
            arch: json["arch"] as? String ?? "",
            kind: kind,
            build: build,
            label: json["label"] as? String ?? "",
            commit: json["commit"] as? String ?? "",
            size: Int64(size),
            sha256: sha
        )
    }

    static func isSHA256(_ text: String) -> Bool {
        text.utf8.count == 64 && text.utf8.allSatisfy { ($0 >= 48 && $0 <= 57) || ($0 >= 97 && $0 <= 102) }
    }
}

/// `update-<platform>.json`, the manifest each release carries for each platform.
public struct AppUpdateManifest: Equatable, Sendable {
    public static let supportedSchema = 1

    public var platform: String
    public var tag: String
    public var releaseURL: String
    public var assets: [AppUpdateAsset]

    public init(platform: String, tag: String, releaseURL: String = "", assets: [AppUpdateAsset]) {
        self.platform = platform
        self.tag = tag
        self.releaseURL = releaseURL
        self.assets = assets
    }

    /// Nil unless `data` is a JSON object whose `schema` is 1 (anything else is "no update
    /// information"). Unknown fields are ignored, and entries without the required fields are
    /// skipped so one bad entry cannot hide the others.
    public init?(data: Data) {
        guard let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              UpdateFields.integer(object["schema"]) == Self.supportedSchema
        else { return nil }
        self.init(
            platform: object["platform"] as? String ?? "",
            tag: object["tag"] as? String ?? "",
            releaseURL: object["release_url"] as? String ?? "",
            assets: (object["assets"] as? [Any] ?? []).compactMap { entry in
                (entry as? [String: Any]).flatMap { AppUpdateAsset(json: $0) }
            }
        )
    }

    /// The entry for one kind, variant and architecture (the newest, should a release list it twice).
    public func asset(kind: String, variant: String, arch: String) -> AppUpdateAsset? {
        assets
            .filter { $0.kind == kind && $0.variant == variant && $0.arch == arch }
            .max { $0.build < $1.build }
    }
}

/// Where releases and their manifests live. `base` is the repository's `releases` address; a
/// Debug build can point it at a local server.
public struct AppUpdateEndpoints: Equatable, Sendable {
    public static let defaultBase = URL(string: "https://github.com/Shirochi-stack/NovelDB/releases")!

    public var base: URL

    public init(base: URL = AppUpdateEndpoints.defaultBase) {
        self.base = base
    }

    /// GitHub redirects `latest/download/<file>` to the newest published release that has the file.
    public func manifestURL(platform: String) -> URL {
        base.appendingPathComponent("latest").appendingPathComponent("download")
            .appendingPathComponent("update-\(platform).json")
    }

    /// Nil unless the tag and the file name are plain names, so a manifest cannot point elsewhere.
    public func assetURL(tag: String, name: String) -> URL? {
        guard Self.isPlainName(tag), Self.isPlainName(name) else { return nil }
        return base.appendingPathComponent("download").appendingPathComponent(tag).appendingPathComponent(name)
    }

    public func releasePageURL(tag: String) -> URL? {
        guard Self.isPlainName(tag) else { return nil }
        return base.appendingPathComponent("tag").appendingPathComponent(tag)
    }

    /// A single path component: no separators, no `..`, no leading dot, no control characters.
    public static func isPlainName(_ name: String) -> Bool {
        guard !name.isEmpty, !name.hasPrefix("."), !name.contains(".."),
              !name.contains("/"), !name.contains("\\"), !name.contains("?"), !name.contains("#"), !name.contains("%")
        else { return false }
        return name.unicodeScalars.allSatisfy { $0.value >= 0x20 && $0.value != 0x7f }
    }
}
