import Foundation
import NpiaCore
import NpiaPipeline
import NpiaUpload

enum JobState: String, Codable {
    case queued, running, finished, failed, cancelled

    var isActive: Bool { self == .queued || self == .running }
}

/// One download in the queue. Persisted between launches; file paths are stored relative to
/// the Documents folder because the app container path changes on reinstall.
struct JobItem: Identifiable, Codable {
    var id = UUID()
    var url: URL
    var source: SourceID?
    var title: String?
    /// The adapter's book id (the novel number for npia); names uploaded files whose title is not ASCII.
    var bookID: String?
    var settings: AppSettings
    var state: JobState = .queued
    var status = "Queued"
    var fraction: Double = 0
    var error: String?
    var fileNames: [String] = []
    var uploads: [UploadResult] = []
    var skipped: [String] = []
    var log: [String] = []
    var createdAt = Date()
    var finishedAt: Date?

    var displayTitle: String { title ?? url.absoluteString }

    func files(in directory: URL) -> [URL] {
        fileNames.map { directory.appendingPathComponent($0) }
    }

    mutating func apply(_ phase: JobPhase) {
        switch phase {
        case .queued:
            status = "Queued"; fraction = 0
        case .fetchingBook:
            status = "Reading book info"; fraction = 0.02
        case .fetchingChapters(let done, let total):
            status = "Chapters \(done)/\(total)"
            fraction = 0.05 + 0.6 * Self.ratio(done, total)
        case .downloadingImages(let done, let total):
            status = total == 0 ? "No images" : "Images \(done)/\(total)"
            fraction = 0.65 + 0.25 * Self.ratio(done, total)
        case .writing(let format):
            status = "Writing \(format.rawValue.uppercased())"; fraction = max(fraction, 0.92)
        case .uploading(let name):
            status = "Uploading \(name)"; fraction = max(fraction, 0.97)
        case .signingIn(let host):
            status = "Sign in to \(host) to continue"
        case .finished:
            status = "Done"; fraction = 1
        case .failed(let message):
            status = message; error = message
        }
    }

    /// The most lines a job keeps. Every line also goes to the combined queue log.
    static let logLimit = 1_500

    mutating func appendLog(_ line: String) {
        log.append(line)
        if log.count > Self.logLimit { log.removeFirst(log.count - Self.logLimit) }
    }

    private static func ratio(_ done: Int, _ total: Int) -> Double {
        total > 0 ? min(1, Double(done) / Double(total)) : 1
    }
}

/// Bridges pipeline log lines onto the main actor.
struct JobLogBridge: JobLogging {
    let sink: @Sendable (LogLevel, String) -> Void

    func log(_ level: LogLevel, _ message: String) {
        sink(level, message)
    }
}
