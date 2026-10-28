import Foundation
import NpiaUpload

/// The files one tap on Upload covers, with what the results need to say about each.
struct UploadRequest: Equatable {
    struct Entry: Equatable {
        var file: URL
        var jobID: UUID?
        /// The book's title, listed with the ZIP the file ends up in.
        var title: String?
        /// `<source>_<book id>`: names the file when its title has nothing usable in ASCII.
        var nameID: String?
    }

    var entries: [Entry]

    var files: [URL] { entries.map(\.file) }

    /// The jobs the files belong to, in order, once each.
    var jobIDs: [UUID] {
        var seen: [UUID] = []
        for case let id? in entries.map(\.jobID) where !seen.contains(id) { seen.append(id) }
        return seen
    }

    var items: [UploadItem] {
        entries.map { UploadItem(file: $0.file, title: $0.title, nameID: $0.nameID) }
    }
}

/// What one upload is doing, for the progress rows in the job detail and the Uploads screen. It
/// covers a file, or several files that may go up as one or more ZIPs.
struct UploadActivity: Identifiable, Equatable {
    enum Phase: Equatable {
        case running
        /// At least one link came back (`results`); `failedUploads` lists the ones that did not.
        case finished
        case failed(String)
    }

    let id = UUID()
    let request: UploadRequest
    var phase: Phase = .running
    /// "Preparing ZIP 1 of 3" while a ZIP is written; nil once a host has the file.
    var stepText: String?
    /// The ZIP or file being sent.
    var currentName: String?
    /// Upload `index` of `count`; 0 until the first one starts.
    var index = 0
    var count = 0
    /// The host being tried now (its display name); nil until the first one starts.
    var host: String?
    var progress: UploadProgress?
    /// Hosts that refused the file, with the reason, in the order they were tried.
    var failures: [String] = []
    /// One link per ZIP or file, added as each upload finishes.
    var results: [UploadResult] = []
    /// ZIPs or files that no host took, with the reason.
    var failedUploads: [String] = []

    var files: [URL] { request.files }
    var jobIDs: [UUID] { request.jobIDs }

    /// What the lists call it: the file name, or "3 files".
    var title: String {
        files.count == 1 ? files[0].lastPathComponent : "\(files.count) files"
    }

    var isRunning: Bool { phase == .running }

    func covers(_ file: URL) -> Bool {
        files.contains { $0.path == file.path }
    }

    /// The bar fraction over the whole upload: finished uploads plus the one in progress.
    var fraction: Double {
        let part = progress?.fraction ?? 0
        guard count > 1, index > 0 else { return part }
        return min(1, (Double(index - 1) + part) / Double(count))
    }

    mutating func apply(_ event: UploadBatchEvent) {
        switch event {
        case .starting(let index, let total, let name):
            self.index = index
            count = total
            currentName = name
            stepText = nil
            host = nil
            progress = nil
        case .preparing(let zip, let total, _):
            stepText = "Preparing ZIP \(zip) of \(total)"
            host = nil
            progress = nil
        case .host(let hostEvent):
            switch hostEvent {
            case .trying(let host, _, _):
                stepText = nil
                self.host = host
                progress = nil
            case .progress(let host, let value):
                self.host = host
                progress = value
            case .failed(let host, let message):
                failures.append("\(host) failed: \(message)")
                progress = nil
            }
        case .uploaded(let result):
            results.append(result)
            host = nil
            progress = nil
        case .failed(let name, let message):
            failedUploads.append("\(name): \(message)")
        }
    }

    /// "Uploading to Gofile… 42% (6.3 MB / 15 MB)", "Preparing ZIP 1 of 3", then the links or the reason.
    var statusLine: String {
        switch phase {
        case .running:
            var line = ""
            if count > 1, index > 0 { line += "(\(index) of \(count)) " }
            if let stepText {
                line += stepText
            } else if let host {
                line += "Uploading \(currentName.map { $0 + " " } ?? "")to \(host)\u{2026}"
                if let progress, progress.totalBytes > 0 {
                    line += " \(Int(progress.fraction * 100))% (\(Self.bytes(progress.sentBytes)) / \(Self.bytes(progress.totalBytes)))"
                }
            } else {
                line += "Preparing the upload\u{2026}"
            }
            return line
        case .finished:
            return results.count == 1 ? "Uploaded" : "Uploaded \(results.count) links"
        case .failed(let message):
            return "Upload failed: \(message)"
        }
    }

    /// Every link with what it holds, one per line, for Copy and Share.
    var linkText: String {
        results.map(\.linkLine).joined(separator: "\n")
    }

    static func bytes(_ count: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: count, countStyle: .file)
    }
}
