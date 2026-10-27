import Foundation
import NpiaCore
import NpiaNet
import NpiaSources
import NpiaWebEngine
import Observation

/// One line of the live log.
struct TagSearchLogLine: Identifiable, Equatable {
    let id: Int
    let text: String
}

/// What a finished search produced: a list to save as text or to send to the link cart.
struct TagSearchOutcome: Equatable {
    var fileName: String
    var fileText: String
    /// Novel ids, each once.
    var ids: [String]

    /// One link-cart entry per id, as an npia novel link.
    func cartItems() -> [CartItem] {
        ids.map { CartItem(url: NpiaCatalogSearch.novelURL(id: $0).absoluteString, title: "npia \($0)", source: "npia") }
    }
}

/// Runs the npia tag search, "scrape all", and Top 100 jobs for the Tag search screen. It lives
/// in `AppModel`, so a run keeps going while the screen is closed and the log is still there
/// when it is opened again.
@MainActor
@Observable
final class TagSearchController {
    private(set) var isRunning = false
    private(set) var lines: [TagSearchLogLine] = []
    /// The result label: progress text while running, then "Found N novel(s)." and so on.
    private(set) var summary = ""
    private(set) var outcome: TagSearchOutcome?

    static let maxLines = 1000

    private var nextLineID = 0
    private var task: Task<Void, Never>?
    private let jobID = UUID()

    var logText: String { lines.map(\.text).joined(separator: "\n") }

    private enum Work {
        case scrapeAll(maxQueries: Int)
        case tags(groups: [[String]])
        case top100
    }

    private enum Finished {
        case ids(NpiaSearchResult, groups: [[String]]?)
        case top100(NpiaTop100Result)
        case failed(String)
        case stopped
    }

    // MARK: - Starting and stopping

    /// Starts a tag search, or "scrape all" when that is switched on. Returns a message when
    /// there is nothing to run.
    @discardableResult
    func startSearch(settings: AppSettings, secrets: any SecretStore, background: BackgroundActivity) -> String? {
        guard !isRunning else { return nil }
        let npia = settings.npia
        if npia.tagScrapeAll {
            let queries = NpiaSettings.clampQueries(npia.scrapeMaxQueries)
            begin(.scrapeAll(maxQueries: queries), title: "Scraping all novels", status: "Scraping all novels (\(queries) queries)...",
                  settings: settings, secrets: secrets, background: background)
            return nil
        }
        let groups = NpiaSearchPlan.groupsToSearch(selected: npia.tagSelectedTags, custom: npia.tagCustomTags, groups: npia.tagQueryGroups)
        guard !groups.isEmpty else {
            return "Add at least one group to the query builder, or select tags directly."
        }
        begin(.tags(groups: groups), title: "Tag search", status: "Searching...", settings: settings, secrets: secrets, background: background)
        return nil
    }

    func startTop100(settings: AppSettings, secrets: any SecretStore, background: BackgroundActivity) {
        guard !isRunning else { return }
        begin(.top100, title: "Top 100 rankings", status: "Fetching Top 100 rankings...", settings: settings, secrets: secrets, background: background)
    }

    func stop() {
        guard isRunning else { return }
        append("Stopping...")
        task?.cancel()
    }

    func clearLog() {
        guard !isRunning else { return }
        lines = []
    }

    // MARK: - Running

    private func begin(_ work: Work, title: String, status: String, settings: AppSettings, secrets: any SecretStore, background: BackgroundActivity) {
        isRunning = true
        outcome = nil
        summary = status
        switch work {
        case .top100: append("--- Fetching Top 100 Rankings ---")
        case .scrapeAll: append("--- Scraping all novels ---")
        case .tags: append("--- Searching novel IDs ---")
        }

        let (stream, continuation) = AsyncStream<String>.makeStream()
        let consumer = Task { @MainActor [weak self] in
            for await line in stream { self?.append(line) }
        }
        let id = jobID
        let progress: @Sendable (Double, String) -> Void = { fraction, text in
            Task { @MainActor in background.jobProgress(id, fraction: fraction, status: text) }
        }
        background.jobStarted(id, title: title) { [weak self] in self?.stop() }

        let npia = settings.npia
        let shared = settings.effective(for: .npia)
        let delay = max(0, shared.intervalMin)
        let threads = max(1, shared.threads)
        let age = npia.tagAgeFilter

        task = Task { [weak self] in
            let client = URLSessionHTTPClient(maxRetries: 0)
            let logger = JobLogBridge { _, line in continuation.yield(line) }
            let sourceContext = SourceContext(http: client, web: WKWebEngine(), secrets: secrets, settings: settings, logger: logger)
            let context = NpiaSearchContext(http: client, log: { continuation.yield($0) }, progress: progress)

            let finished: Finished
            do {
                try await NpiaAuth.prepare(context: sourceContext, cookies: SharedCookieStorageJar())
                switch work {
                case .scrapeAll(let maxQueries):
                    let result = await NpiaCatalogSearch.fetchAllNovels(
                        delay: delay, ageFilter: age, maxQueries: maxQueries, threads: threads, context: context
                    )
                    finished = .ids(result, groups: nil)
                case .tags(let groups):
                    let result = try await NpiaCatalogSearch.fetchNovels(
                        query: .groups(groups), delay: delay, ageFilter: age, threads: threads, context: context
                    )
                    finished = .ids(result, groups: groups)
                case .top100:
                    finished = .top100(await NpiaCatalogSearch.fetchTop100(ageFilter: age, context: context))
                }
            } catch is CancellationError {
                finished = .stopped
            } catch {
                if Task.isCancelled {
                    finished = .stopped
                } else {
                    continuation.yield("Tag retrieval failed: \(error.localizedDescription)")
                    finished = .failed(error.localizedDescription)
                }
            }
            continuation.finish()
            await consumer.value
            self?.finish(finished, background: background)
        }
    }

    private func finish(_ finished: Finished, background: BackgroundActivity) {
        var success = true
        switch finished {
        case .ids(let result, let groups):
            summary = NpiaSearchOutput.idSummary(count: result.ids.count, stopped: result.stopped)
            success = !result.stopped
            if !result.ids.isEmpty {
                outcome = TagSearchOutcome(
                    fileName: NpiaSearchOutput.tagFileName(groups: groups),
                    fileText: NpiaSearchOutput.idListText(result.ids),
                    ids: result.ids
                )
            }
        case .top100(let result):
            summary = NpiaSearchOutput.top100Summary(result.rankings, stopped: result.stopped)
            success = !result.stopped
            let unique = NpiaSearchOutput.uniqueIDs(result.rankings)
            if !unique.isEmpty {
                outcome = TagSearchOutcome(
                    fileName: NpiaSearchOutput.top100FileName,
                    fileText: NpiaSearchOutput.top100Text(result.rankings),
                    ids: unique
                )
            }
        case .failed(let message):
            summary = "Error: \(message)"
            success = false
        case .stopped:
            summary = NpiaSearchOutput.idSummary(count: 0, stopped: true)
            success = false
        }
        isRunning = false
        task = nil
        background.jobEnded(jobID, success: success)
    }

    private func append(_ text: String) {
        lines.append(TagSearchLogLine(id: nextLineID, text: text))
        nextLineID += 1
        if lines.count > Self.maxLines {
            lines.removeFirst(lines.count - Self.maxLines)
        }
    }
}
