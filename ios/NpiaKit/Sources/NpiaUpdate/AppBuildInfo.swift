import Foundation

/// Which build of NovelDB is running.
///
/// CI stamps the build number, tag and label into the app: the build settings `NOVELDB_BUILD`,
/// `NOVELDB_TAG` and `NOVELDB_LABEL` become the Info.plist keys below. A build without a number
/// (local, debug and pull request builds) is a development build and never offers updates.
public struct AppBuildInfo: Equatable, Sendable {
    public static let buildKey = "NovelDBBuild"
    public static let tagKey = "NovelDBTag"
    public static let labelKey = "NovelDBLabel"

    /// Unix time in seconds at which the release workflow planned the build; larger is newer.
    /// 0 for a development build.
    public var build: Int
    /// The release tag, like `v98`; display only.
    public var tag: String
    /// `none` for a normal release, `hotfix3` and so on for a hotfix; display only.
    public var label: String

    public init(build: Int = 0, tag: String = "", label: String = "") {
        self.build = max(0, build)
        self.tag = tag.trimmingCharacters(in: .whitespacesAndNewlines)
        self.label = label.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public static let development = AppBuildInfo()

    /// Reads the stamped keys from an Info.plist dictionary. A missing, empty, zero, negative or
    /// non-numeric build (an unexpanded `$(NOVELDB_BUILD)` included) makes a development build.
    public init(info: [String: Any]?) {
        let info = info ?? [:]
        let build = Self.integer(info[Self.buildKey]) ?? 0
        let tag = (info[Self.tagKey] as? String) ?? ""
        let label = (info[Self.labelKey] as? String) ?? ""
        self.init(build: build, tag: tag, label: label)
    }

    public static func current(bundle: Bundle = .main) -> AppBuildInfo {
        AppBuildInfo(info: bundle.infoDictionary)
    }

    public var isDevelopment: Bool { build <= 0 }

    /// `v98` or `v98 hotfix3`; empty for a development build without a tag.
    public var versionName: String { Self.versionName(tag: tag, label: label) }

    /// The line Settings shows: `v98 hotfix3, build 1793193554`, or `Development build`.
    public var statusText: String {
        if isDevelopment { return "Development build" }
        return [versionName, "build \(build)"].filter { !$0.isEmpty }.joined(separator: ", ")
    }

    /// The tag, then the label unless it is empty or `none`.
    public static func versionName(tag: String, label: String) -> String {
        let shown = label.isEmpty || label.lowercased() == "none" ? "" : label
        return [tag, shown].filter { !$0.isEmpty }.joined(separator: " ")
    }

    private static func integer(_ value: Any?) -> Int? {
        if let text = value as? String {
            return Int(text.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        return UpdateFields.integer(value)
    }
}

/// Reading numbers out of parsed JSON and property lists.
enum UpdateFields {
    /// A whole number; booleans, fractions and strings give nil.
    static func integer(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID(),
              !CFNumberIsFloatType(number)
        else { return nil }
        return number.intValue
    }
}
