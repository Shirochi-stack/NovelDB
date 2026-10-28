import Foundation

/// One line of the combined queue log: when it was logged, which job it belongs to, and how
/// serious it is.
public struct LogEntry: Codable, Sendable, Equatable, Identifiable {
    /// Counts up for the life of the log (also across launches), so a list can keep its place.
    public var id: Int
    public var date: Date
    /// nil for lines that belong to no job.
    public var jobID: UUID?
    /// The job's title (or its address while the title is unknown) when the line was logged.
    public var jobTitle: String
    public var level: LogLevel
    public var message: String

    public init(id: Int = 0, date: Date = Date(), jobID: UUID? = nil, jobTitle: String = "", level: LogLevel = .info, message: String) {
        self.id = id
        self.date = date
        self.jobID = jobID
        self.jobTitle = jobTitle
        self.level = level
        self.message = message
    }

    /// Lines the "Details" switch hides.
    public var isDetail: Bool { level == .debug }

    /// The job title as the log rows show it.
    public var shortTitle: String { Self.shortTitle(jobTitle) }

    /// A short label for a job: web addresses lose the scheme, long titles are cut at `limit`
    /// characters with an ellipsis.
    public static func shortTitle(_ title: String, limit: Int = 22) -> String {
        var text = title.trimmingCharacters(in: .whitespacesAndNewlines)
        for scheme in ["https://", "http://"] where text.lowercased().hasPrefix(scheme) {
            text = String(text.dropFirst(scheme.count))
        }
        if text.hasPrefix("www.") { text = String(text.dropFirst(4)) }
        guard text.count > limit else { return text }
        return String(text.prefix(max(1, limit - 1))) + "\u{2026}"
    }
}

/// The combined log of every job in the queue, newest last, kept in memory as a ring of the
/// most recent `capacity` lines and mirrored to a rolling JSON-lines file so it survives a
/// relaunch.
///
/// Lines are kept in time order: one that was logged earlier but arrives later is placed before
/// the newer ones. The file is appended to a moment after each line (`flush()` writes at once)
/// and rewritten when it holds more than twice `capacity` lines.
public final class LogBuffer: @unchecked Sendable {
    public static let defaultCapacity = 5_000

    public let capacity: Int
    /// nil keeps the log in memory only.
    public let fileURL: URL?

    private let lock = NSLock()
    private let ioQueue = DispatchQueue(label: "npia.log-buffer", qos: .utility)
    private var ring: [LogEntry] = []
    private var nextID = 1
    private var pending: [LogEntry] = []
    private var linesOnDisk = 0
    private var flushScheduled = false

    public init(fileURL: URL? = nil, capacity: Int = LogBuffer.defaultCapacity) {
        self.capacity = max(1, capacity)
        self.fileURL = fileURL
        if let fileURL {
            let loaded = Self.load(fileURL, capacity: self.capacity)
            ring = loaded.entries
            linesOnDisk = loaded.lines
            nextID = (loaded.entries.map(\.id).max() ?? 0) + 1
        }
    }

    // MARK: - Writing

    /// Adds a line. `date` is when it was logged, so a line that took a detour still lands in order.
    @discardableResult
    public func append(_ level: LogLevel, _ message: String, jobID: UUID? = nil, jobTitle: String = "", date: Date = Date()) -> LogEntry {
        lock.lock()
        let entry = LogEntry(id: nextID, date: date, jobID: jobID, jobTitle: jobTitle, level: level, message: message)
        nextID += 1
        var position = ring.count
        while position > 0, ring[position - 1].date > entry.date { position -= 1 }
        ring.insert(entry, at: position)
        if ring.count > capacity { ring.removeFirst(ring.count - capacity) }
        pending.append(entry)
        let schedule = fileURL != nil && !flushScheduled
        if schedule { flushScheduled = true }
        lock.unlock()
        if schedule {
            ioQueue.asyncAfter(deadline: .now() + 0.5) { [self] in writePending() }
        }
        return entry
    }

    /// Removes every line, in memory and on disk.
    public func clear() {
        lock.lock()
        ring = []
        pending = []
        linesOnDisk = 0
        lock.unlock()
        guard let fileURL else { return }
        ioQueue.sync { try? FileManager.default.removeItem(at: fileURL) }
    }

    /// Writes the lines that are not on disk yet and waits for it. Call when the app leaves the foreground.
    public func flush() {
        guard fileURL != nil else { return }
        ioQueue.sync { writePending() }
    }

    // MARK: - Reading

    public var all: [LogEntry] {
        lock.lock(); defer { lock.unlock() }
        return ring
    }

    public var count: Int {
        lock.lock(); defer { lock.unlock() }
        return ring.count
    }

    /// The lines for one job (or every job when `jobID` is nil), without the debug lines unless `includeDetails`.
    public func entries(jobID: UUID? = nil, includeDetails: Bool = true) -> [LogEntry] {
        Self.filter(all, jobID: jobID, includeDetails: includeDetails)
    }

    public static func filter(_ entries: [LogEntry], jobID: UUID? = nil, includeDetails: Bool = true) -> [LogEntry] {
        entries.filter { entry in
            if let jobID, entry.jobID != jobID { return false }
            return includeDetails || !entry.isDetail
        }
    }

    /// The jobs that have lines in the log, most recently active first, with the title each last used.
    public func jobs() -> [(id: UUID, title: String)] {
        var seen = Set<UUID>()
        var found: [(id: UUID, title: String)] = []
        for entry in all.reversed() {
            guard let id = entry.jobID, seen.insert(id).inserted else { continue }
            found.append((id, entry.jobTitle))
        }
        return found
    }

    /// The lines as plain text, one per line, for Copy and Share.
    public static func text(for entries: [LogEntry], timeZone: TimeZone = .current) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return entries.map { entry in
            let job = entry.jobTitle.isEmpty ? "" : " [\(entry.jobTitle)]"
            return "\(formatter.string(from: entry.date)) \(entry.level.rawValue.uppercased())\(job) \(entry.message)"
        }.joined(separator: "\n")
    }

    // MARK: - File

    private func writePending() {
        lock.lock()
        flushScheduled = false
        let batch = pending
        pending = []
        let total = linesOnDisk + batch.count
        let rewrite = total > capacity * 2
        let snapshot = rewrite ? ring : []
        linesOnDisk = rewrite ? snapshot.count : total
        lock.unlock()
        guard let fileURL else { return }
        if rewrite {
            Self.write(snapshot, to: fileURL)
        } else if !batch.isEmpty {
            Self.append(batch, to: fileURL)
        }
    }

    private static func encode(_ entries: [LogEntry]) -> Data {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .millisecondsSince1970
        var data = Data()
        for entry in entries {
            guard let line = try? encoder.encode(entry) else { continue }
            data.append(line)
            data.append(0x0A)
        }
        return data
    }

    private static func write(_ entries: [LogEntry], to url: URL) {
        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try encode(entries).write(to: url, options: .atomic)
        } catch {
            // The log is a convenience; a failed write must not disturb a download.
        }
    }

    private static func append(_ entries: [LogEntry], to url: URL) {
        let data = encode(entries)
        guard !data.isEmpty else { return }
        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            if !FileManager.default.fileExists(atPath: url.path) {
                try data.write(to: url, options: .atomic)
                return
            }
            let handle = try FileHandle(forWritingTo: url)
            defer { try? handle.close() }
            try handle.seekToEnd()
            try handle.write(contentsOf: data)
        } catch {
            // Same as `write`.
        }
    }

    /// The last `capacity` lines of the file in time order, and how many lines the file holds
    /// (a torn last line from a crash is skipped).
    private static func load(_ url: URL, capacity: Int) -> (entries: [LogEntry], lines: Int) {
        guard let data = try? Data(contentsOf: url), !data.isEmpty else { return ([], 0) }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .millisecondsSince1970
        var loaded: [LogEntry] = []
        var lines = 0
        for piece in data.split(separator: 0x0A, omittingEmptySubsequences: true) {
            lines += 1
            if let entry = try? decoder.decode(LogEntry.self, from: Data(piece)) { loaded.append(entry) }
        }
        loaded.sort { $0.date != $1.date ? $0.date < $1.date : $0.id < $1.id }
        if loaded.count > capacity { loaded.removeFirst(loaded.count - capacity) }
        return (loaded, lines)
    }
}
