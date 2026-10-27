import Foundation
import NpiaCore

// Shared pieces of the npia catalog search: the run context, the lean row type, and the small
// helpers the desktop gets from Python (exception names, `str()`, `int()`, ordered sets).

// MARK: - Errors

/// Stands in for the Python exceptions the desktop logs by class name.
struct NpiaSearchError: Error, CustomStringConvertible, LocalizedError {
    var kind: String
    var message: String

    init(_ message: String, kind: String = "ValueError") {
        self.kind = kind
        self.message = message
    }

    var description: String { message }
    var errorDescription: String? { message }

    /// `f"{type(exc).__name__}: {exc}"`.
    static func summary(_ error: Error) -> String {
        if let error = error as? NpiaSearchError { return "\(error.kind): \(error.message)" }
        return "\(type(of: error)): \(error.localizedDescription)"
    }

    static func message(_ error: Error) -> String {
        (error as? NpiaSearchError)?.message ?? error.localizedDescription
    }
}

// MARK: - Context

/// Everything a search run uses. Stopping is task cancellation: every loop checks
/// `Task.isCancelled` where the desktop checks `stop_signal`, and a cancelled run returns what
/// it has found so far.
public struct NpiaSearchContext: Sendable {
    public var http: any HTTPFetching
    /// One line of the live log.
    public var log: @Sendable (String) -> Void
    /// Overall progress (0...1) with a short status, for the system progress indicator.
    public var progress: @Sendable (Double, String) -> Void
    /// Waits for the given seconds and throws when cancelled; tests pass a no-op.
    public var sleep: @Sendable (Double) async throws -> Void

    public static let realSleep: @Sendable (Double) async throws -> Void = { seconds in
        if seconds > 0 { try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000)) }
    }

    public init(
        http: any HTTPFetching,
        log: @escaping @Sendable (String) -> Void = { _ in },
        progress: @escaping @Sendable (Double, String) -> Void = { _, _ in },
        sleep: @escaping @Sendable (Double) async throws -> Void = NpiaSearchContext.realSleep
    ) {
        self.http = http
        self.log = log
        self.progress = progress
        self.sleep = sleep
    }

    /// Waits `seconds`; false when the run was stopped meanwhile.
    @discardableResult
    func pause(_ seconds: Double) async -> Bool {
        if seconds > 0 {
            do { try await sleep(seconds) } catch { return false }
        }
        return !Task.isCancelled
    }

    /// Runs `body` and logs "Ns elapsed..." every 3 seconds while it is pending.
    func withElapsedTicks<T>(_ body: () async throws -> T) async rethrows -> T {
        let log = self.log
        let start = Date()
        let ticker = Task {
            while !Task.isCancelled {
                do { try await Task.sleep(nanoseconds: 3_000_000_000) } catch { return }
                if Task.isCancelled { return }
                log("    \(Int(Date().timeIntervalSince(start)))s elapsed...")
            }
        }
        defer { ticker.cancel() }
        return try await body()
    }
}

/// Reports `done / total` between two fractions of the overall progress.
final class NpiaProgress: @unchecked Sendable {
    private let lock = NSLock()
    private var done = 0
    private let total: Int
    private let lower: Double
    private let upper: Double
    private let report: @Sendable (Double, String) -> Void

    init(total: Int, from lower: Double = 0, to upper: Double = 1, report: @escaping @Sendable (Double, String) -> Void) {
        self.total = max(1, total)
        self.lower = lower
        self.upper = upper
        self.report = report
    }

    func advance(_ status: String) {
        lock.lock()
        done += 1
        let fraction = lower + (upper - lower) * min(1, Double(done) / Double(total))
        lock.unlock()
        report(fraction, status)
    }
}

// MARK: - Results

public struct NpiaSearchResult: Sendable, Equatable {
    public var ids: [String]
    /// The run was stopped before it finished.
    public var stopped: Bool

    public init(ids: [String], stopped: Bool) {
        self.ids = ids
        self.stopped = stopped
    }
}

/// One Top 100 list, labelled like the desktop ("daily all", "weekly adult", ...).
public struct NpiaRanking: Sendable, Equatable {
    public var label: String
    public var ids: [String]

    public init(label: String, ids: [String]) {
        self.label = label
        self.ids = ids
    }
}

public struct NpiaTop100Result: Sendable, Equatable {
    public var rankings: [NpiaRanking]
    public var stopped: Bool

    public init(rankings: [NpiaRanking], stopped: Bool) {
        self.rankings = rankings
        self.stopped = stopped
    }
}

/// A set of novel ids that remembers the order they were first added in.
struct NpiaIDSet: Sendable, Equatable {
    private(set) var ids: [String] = []
    private var members = Set<String>()

    var count: Int { ids.count }

    @discardableResult
    mutating func insert(_ id: String) -> Bool {
        guard members.insert(id).inserted else { return false }
        ids.append(id)
        return true
    }

    mutating func formUnion(_ other: NpiaIDSet) {
        for id in other.ids { insert(id) }
    }

    mutating func formIntersection(_ other: NpiaIDSet) {
        ids = ids.filter { other.members.contains($0) }
        members = Set(ids)
    }
}

// MARK: - Loose JSON values

/// A JSON value with the loose typing the desktop code relies on (`str()`, `int()`).
enum NpiaScalar: Equatable, Sendable, Decodable {
    case null
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)
    case other

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Int.self) {
            self = .int(value)
        } else if let value = try? container.decode(Double.self) {
            self = .double(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else {
            self = .other
        }
    }

    /// From a `JSONSerialization` value.
    init(any value: Any?) {
        guard let value, !(value is NSNull) else {
            self = .null
            return
        }
        switch value {
        case let text as String:
            self = .string(text)
        case let number as NSNumber:
            if CFGetTypeID(number) == CFBooleanGetTypeID() {
                self = .bool(number.boolValue)
            } else if CFNumberIsFloatType(number) {
                self = .double(number.doubleValue)
            } else {
                self = .int(number.intValue)
            }
        default:
            self = .other
        }
    }

    /// Python `str(value)`; empty for null and containers.
    var text: String {
        switch self {
        case .string(let value): return value
        case .int(let value): return String(value)
        case .double(let value): return String(value)
        case .bool(let value): return value ? "True" : "False"
        case .null, .other: return ""
        }
    }

    /// Python `int(value)`; nil where Python raises.
    var integer: Int? {
        switch self {
        case .int(let value): return value
        case .double(let value): return value.isFinite ? Int(exactly: value.rounded(.towardZero)) : nil
        case .bool(let value): return value ? 1 : 0
        case .string(let value): return Int(value.trimmingCharacters(in: .whitespacesAndNewlines))
        case .null, .other: return nil
        }
    }
}

// MARK: - Novel rows

/// The fields of a search, intro, or author-catalog record the sweep needs.
struct NpiaNovelRow: Sendable, Equatable {
    var novelNo: String
    /// `int(record["novel_age"])`; 0 when the key is missing, nil when it cannot be read.
    var novelAge: Int?
    /// `author_id(record)`.
    var authorID: String?

    init(novelNo: String, novelAge: Int? = 0, authorID: String? = nil) {
        self.novelNo = novelNo
        self.novelAge = novelAge
        self.authorID = authorID
    }

    /// `fields` looks a key up in the raw record; nil means the key is absent.
    init(fields: (String) -> NpiaScalar?) {
        novelNo = fields("novel_no")?.text ?? ""
        if let age = fields("novel_age") {
            novelAge = age.integer
        } else {
            novelAge = 0
        }
        var owner: String?
        for key in ["writer_mem_no", "mem_no", "author_id"] {
            let value = fields(key)?.text ?? ""
            if Self.isPositiveInteger(value) {
                owner = value
                break
            }
        }
        authorID = owner
    }

    init(json: [String: Any]) {
        self.init(fields: { key in json[key].map { NpiaScalar(any: $0) } })
    }

    /// `matches_age`: "19" keeps adult novels, "15" keeps non-adult ones, anything else keeps all.
    func matches(ageFilter: String) -> Bool {
        guard let age = novelAge else { return ageFilter.isEmpty }
        if ageFilter == "19" { return age >= 19 }
        if ageFilter == "15" { return age < 19 }
        return true
    }

    /// Python `str.isdigit` for the ASCII digits the site uses.
    static func isDigits(_ value: String) -> Bool {
        !value.isEmpty && value.allSatisfy { ("0"..."9").contains($0) }
    }

    static func isPositiveInteger(_ value: String) -> Bool {
        isDigits(value) && value.contains { $0 != "0" }
    }
}

/// A search response, decoded without building the whole JSON tree: a tag search can return
/// tens of thousands of records and only a few fields of each matter.
struct NpiaSearchPayload: Decodable {
    var status: NpiaScalar?
    var errmsg: NpiaScalar?
    /// nil when the response has no "list" key.
    var rows: [NpiaSearchRecord]?
    /// False when "list" exists but is not an array.
    var listIsValid = true

    private enum CodingKeys: String, CodingKey {
        case status, errmsg, list
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = container.contains(.status) ? try container.decode(NpiaScalar.self, forKey: .status) : nil
        errmsg = container.contains(.errmsg) ? try container.decode(NpiaScalar.self, forKey: .errmsg) : nil
        if container.contains(.list) {
            if let decoded = try? container.decode([NpiaSearchRecord].self, forKey: .list) {
                rows = decoded
            } else {
                listIsValid = false
            }
        }
    }

    /// `data.get("status") == 200`.
    var isOK: Bool {
        switch status {
        case .int(200)?, .double(200)?: return true
        default: return false
        }
    }

    /// `data.get("errmsg", "Unknown")`.
    var errorText: String { errmsg?.text ?? "Unknown" }
}

/// One element of a search "list"; elements that are not objects decode as nil.
struct NpiaSearchRecord: Decodable {
    var row: NpiaNovelRow?

    private struct Key: CodingKey {
        var stringValue: String
        var intValue: Int? { nil }
        init?(stringValue: String) { self.stringValue = stringValue }
        init?(intValue: Int) { return nil }
    }

    init(from decoder: Decoder) throws {
        guard let container = try? decoder.container(keyedBy: Key.self) else {
            row = nil
            return
        }
        row = NpiaNovelRow(fields: { name in
            guard let key = Key(stringValue: name), container.contains(key) else { return nil }
            return try? container.decode(NpiaScalar.self, forKey: key)
        })
    }
}

// MARK: - Text helpers

enum NpiaSearchText {
    /// Python `repr(str)`.
    static func repr(_ value: String) -> String {
        let quote: Character = value.contains("'") && !value.contains("\"") ? "\"" : "'"
        var out = String(quote)
        for ch in value {
            switch ch {
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            case quote: out += "\\" + String(ch)
            default: out.append(ch)
            }
        }
        out.append(quote)
        return out
    }

    /// `response_details`: bounded body text, without credentials, for failure logs.
    static func responseDetails(_ response: HTTPResponse) -> String {
        let text = response.text
        let redacted = NpiaRx.replace(
            #"(["']?(?:loginkey|access_token|refresh_token|api_key|authorization|password|cookie)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s<>&,;]+)"#,
            in: text, with: "$1[redacted]", .caseInsensitive
        )
        let preview = String(redacted.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ").prefix(240))
        let contentType = response.headers["content-type"] ?? response.headers["Content-Type"] ?? "unknown"
        return "HTTP \(response.status); content-type=\(repr(contentType)); body-chars=\(text.count); preview=\(repr(preview))"
    }
}
