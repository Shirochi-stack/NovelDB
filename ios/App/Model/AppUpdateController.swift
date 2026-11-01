import CryptoKit
import Foundation
import NpiaUpdate
import Observation

/// Looks for a newer build of the app itself and, after the user taps Update, downloads and
/// verifies it. iOS apps cannot install themselves, so the end of the road is a checked IPA that
/// the update sheet offers to the share sheet and to Files.
///
/// It lives in `AppModel` (like `TagSearchController`), so a download keeps going while the
/// sheet is closed. The checks themselves are `AppUpdateChecker` in NpiaKit.
@MainActor
@Observable
final class AppUpdateController {
    enum Phase: Equatable {
        case idle
        case downloading
        /// The verified file.
        case ready(URL)
        case failed(String)
    }

    /// The build in use, from the Info.plist keys CI stamps in.
    let build = AppBuildInfo.current()
    /// What the last check or download said (Settings > App updates).
    private(set) var statusMessage = ""
    private(set) var checking = false
    private(set) var lastOutcome: AppUpdateOutcome?
    /// The update the last check found. The root view shows it as a sheet.
    private(set) var offer: AppUpdate?
    var showingOffer = false
    private(set) var phase: Phase = .idle
    /// 0...1 while downloading.
    private(set) var fraction = 0.0

    private let checker: AppUpdateChecker
    private var downloadTask: Task<Void, Never>?

    init() {
        var endpoints = AppUpdateEndpoints()
        #if DEBUG
        if let text = Self.launchArgument("-NovelDBUpdateBase"), let base = URL(string: text) {
            endpoints = AppUpdateEndpoints(base: base)
        }
        #endif
        checker = AppUpdateChecker(build: AppBuildInfo.current(), endpoints: endpoints)
        if build.isDevelopment { statusMessage = AppUpdateOutcome.development.message }
    }

    /// The automatic check (when the setting is on, at most once a day) or, with `automatic`
    /// false, the one behind "Check for app updates now".
    func check(automatic: Bool, enabled: Bool) async {
        guard !checking, phase != .downloading else { return }
        if build.isDevelopment {
            statusMessage = AppUpdateOutcome.development.message
            return
        }
        if automatic && !enabled { return }
        checking = true
        defer { checking = false }

        let outcome: AppUpdateOutcome
        if automatic {
            guard let due = await checker.checkIfDue() else { return }
            outcome = due
        } else {
            outcome = await checker.check()
        }
        lastOutcome = outcome
        statusMessage = outcome.message
        if case .available(let update) = outcome {
            present(update)
        } else {
            offer = nil
            phase = .idle
        }
    }

    private func present(_ update: AppUpdate) {
        // The same update again keeps its finished download.
        if offer != update {
            offer = update
            phase = .idle
            fraction = 0
        }
        showingOffer = true
        #if DEBUG
        if Self.reportRequested { startDownload() }
        #endif
    }

    // MARK: - Downloading

    /// "Update": downloads the file and checks its size and SHA-256.
    func startDownload() {
        guard let update = offer, downloadTask == nil else { return }
        phase = .downloading
        fraction = 0
        statusMessage = "Downloading \(update.versionName)..."
        let report: @Sendable (Int64, Int64) -> Void = { [weak self] received, total in
            Task { @MainActor in
                self?.fraction = total > 0 ? min(1, Double(received) / Double(total)) : 0
            }
        }
        downloadTask = Task { [weak self, checker] in
            do {
                let file = try await checker.download(update, progress: report)
                self?.downloadEnded(.ready(file))
            } catch is CancellationError {
                self?.downloadEnded(.idle)
            } catch {
                self?.downloadEnded(.failed(error.localizedDescription))
            }
        }
    }

    func cancelDownload() {
        downloadTask?.cancel()
    }

    private func downloadEnded(_ result: Phase) {
        downloadTask = nil
        phase = result
        switch result {
        case .ready:
            statusMessage = "Downloaded and checked \(offer?.versionName ?? "the update")."
            showingOffer = true
        case .failed(let text):
            statusMessage = text
            showingOffer = true
        case .idle:
            statusMessage = offer?.title ?? ""
        case .downloading:
            break
        }
    }

    /// "Later": closes the sheet. The update stays in Settings > App updates until the next check.
    func dismissOffer() {
        showingOffer = false
    }

    // MARK: - Debug hooks (the iOS workflow's update check)

    #if DEBUG
    /// `-NovelDBUpdateBase <releases address>` sends the checks and downloads to another server,
    /// for example a local one. `-NovelDBUpdateReport` also starts the download as soon as an
    /// update is found and writes `update-check-report.txt` to Documents when everything has
    /// settled. Release builds contain neither.
    static let reportRequested = ProcessInfo.processInfo.arguments.contains("-NovelDBUpdateReport")

    private static func launchArgument(_ name: String) -> String? {
        let arguments = ProcessInfo.processInfo.arguments
        guard let flag = arguments.firstIndex(of: name), arguments.indices.contains(flag + 1) else { return nil }
        return arguments[flag + 1]
    }

    func writeReportIfAsked(to directory: URL) async {
        guard Self.reportRequested else { return }
        // Wait for the launch check, and for the download it starts, to finish.
        for _ in 0..<240 {
            if lastOutcome != nil, !checking, phase != .downloading { break }
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
        var lines = ["build: \(build.statusText)"]
        switch lastOutcome {
        case .development?: lines.append("outcome: development")
        case .upToDate?: lines.append("outcome: upToDate")
        case .available?: lines.append("outcome: available")
        case .noInformation?: lines.append("outcome: noInformation")
        case .failed?: lines.append("outcome: failed")
        case nil: lines.append("outcome: none")
        }
        lines.append("check: \(lastOutcome?.message ?? "no check ran")")
        lines.append("offer: \(offer?.title ?? "none")")
        switch phase {
        case .idle: lines.append("phase: idle")
        case .downloading: lines.append("phase: downloading")
        case .failed(let text):
            lines.append("phase: failed")
            lines.append("error: \(text)")
        case .ready(let file):
            lines.append("phase: ready")
            lines.append("file: \(file.lastPathComponent)")
            if let data = try? Data(contentsOf: file) {
                lines.append("file-size: \(data.count)")
                lines.append("file-sha256: \(SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined())")
            }
        }
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("NovelDBUpdate", isDirectory: true)
        let names = ((try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? []).sorted()
        lines.append("leftover: \(names.isEmpty ? "none" : names.joined(separator: ", "))")
        try? lines.joined(separator: "\n").write(
            to: directory.appendingPathComponent("update-check-report.txt"), atomically: true, encoding: .utf8)
    }
    #endif
}
