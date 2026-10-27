import Foundation
import NpiaUpload

/// What one upload is doing, for the progress rows in the job detail and the Uploads screen.
struct UploadActivity: Identifiable, Equatable {
    enum Phase: Equatable {
        case running
        case finished(UploadResult)
        case failed(String)
    }

    let file: URL
    var jobID: UUID?
    var phase: Phase = .running
    /// The host being tried now (its display name); nil until the first one starts.
    var host: String?
    var progress: UploadProgress?
    /// Hosts that refused the file, with the reason, in the order they were tried.
    var failures: [String] = []

    var id: String { file.path }
    var fileName: String { file.lastPathComponent }

    var isRunning: Bool { phase == .running }

    /// The bar fraction; 0 until the host reports bytes.
    var fraction: Double { progress?.fraction ?? 0 }

    mutating func apply(_ event: UploadEvent) {
        switch event {
        case .trying(let host, _, _):
            self.host = host
            progress = nil
        case .progress(let host, let value):
            self.host = host
            progress = value
        case .failed(let host, let message):
            failures.append("\(host) failed: \(message)")
            progress = nil
        }
    }

    /// "Uploading to Gofile… 42% (6.3 MB / 15 MB)", then "Uploaded — <link>" or "Upload failed: <reason>".
    var statusLine: String {
        switch phase {
        case .running:
            guard let host else { return "Preparing the upload\u{2026}" }
            var line = "Uploading to \(host)\u{2026}"
            if let progress, progress.totalBytes > 0 {
                line += " \(Int(progress.fraction * 100))% (\(Self.bytes(progress.sentBytes)) / \(Self.bytes(progress.totalBytes)))"
            }
            return line
        case .finished(let result):
            return "Uploaded \u{2014} \(result.link.absoluteString)"
        case .failed(let message):
            return "Upload failed: \(message)"
        }
    }

    static func bytes(_ count: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: count, countStyle: .file)
    }
}
