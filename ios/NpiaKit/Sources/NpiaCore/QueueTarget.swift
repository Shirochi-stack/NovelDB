import Foundation

/// Where tapping a queue notification takes the user: the combined log of the Queue tab, or the
/// detail page of one job.
public enum QueueTarget: Equatable, Sendable {
    /// Queue > Log, filtered to every job.
    case log
    case job(UUID)

    /// `userInfo` key holding "log" or "job".
    public static let targetKey = "npia.queue.target"
    /// `userInfo` key holding the job's UUID string for "job".
    public static let jobKey = "npia.queue.job"

    /// The screen for a run that held these jobs: the job's own page when the run was a single
    /// book (the same job retried counts once), otherwise the combined log.
    public static func forRun(_ jobIDs: [UUID]) -> QueueTarget {
        var unique: [UUID] = []
        for id in jobIDs where !unique.contains(id) { unique.append(id) }
        if unique.count == 1, let only = unique.first { return .job(only) }
        return .log
    }

    /// The screen to show now: a job that is no longer in the queue falls back to the log.
    public func resolved(existing jobIDs: Set<UUID>) -> QueueTarget {
        if case .job(let id) = self, !jobIDs.contains(id) { return .log }
        return self
    }

    /// What a notification carries so the app can route the tap.
    public var userInfo: [String: String] {
        switch self {
        case .log:
            return [Self.targetKey: "log"]
        case .job(let id):
            return [Self.targetKey: "job", Self.jobKey: id.uuidString]
        }
    }

    /// Reads `userInfo` back; nil for a notification that is not a queue one.
    public init?(userInfo: [AnyHashable: Any]) {
        switch userInfo[Self.targetKey] as? String {
        case "log":
            self = .log
        case "job":
            guard let text = userInfo[Self.jobKey] as? String, let id = UUID(uuidString: text) else { return nil }
            self = .job(id)
        default:
            return nil
        }
    }
}

/// The text of the notification that ends a run of downloads.
public struct QueueRunSummary: Equatable, Sendable {
    public var finished: Int
    public var failed: Int
    public var cancelled: Int

    public init(finished: Int, failed: Int, cancelled: Int = 0) {
        self.finished = finished
        self.failed = failed
        self.cancelled = cancelled
    }

    public var total: Int { finished + failed + cancelled }

    public var title: String {
        failed == 0 && cancelled == 0 ? "Downloads finished" : "Downloads ended"
    }

    /// "3 books done, 1 failed".
    public var body: String {
        var parts: [String] = []
        if finished > 0 { parts.append(finished == 1 ? "1 book done" : "\(finished) books done") }
        if failed > 0 { parts.append("\(failed) failed") }
        if cancelled > 0 { parts.append("\(cancelled) cancelled") }
        return parts.isEmpty ? "Nothing was downloaded" : parts.joined(separator: ", ")
    }
}
