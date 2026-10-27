import Foundation
import NpiaCore
import NpiaNet
import NpiaPipeline
import NpiaSources
import NpiaUpload
import NpiaWebEngine
import Observation
import UIKit

/// App-wide state: settings, the download queue, and upload history.
@MainActor
@Observable
final class AppModel {
    var settings: AppSettings {
        didSet { if settings != oldValue { saveSettings() } }
    }
    private(set) var jobs: [JobItem] = []
    private(set) var uploadHistory: [UploadResult] = []
    /// Shown as an alert by the root view.
    var message: String?
    /// An upload waiting for the one-time sharing agreement; the root view shows it as a sheet.
    var pendingUpload: PendingUpload?
    /// URL handed to the Add screen (from the Browser tab's "Download this book").
    var pendingAddText: String?
    /// Page handed to the Browser tab (from the Catalog tab's "Open novel").
    var pendingBrowserURL: URL?
    /// Books collected with the Catalog tab's link-cart button.
    let cart = LinkCart()
    /// Downloads that run at once; the rest wait in the queue.
    var maxConcurrentJobs = 1
    /// The npia tag search, scrape-all, and Top 100 runs of the Tag search screen.
    let tagSearch = TagSearchController()

    let secrets: any SecretStore = KeychainSecretStore()
    let background = BackgroundActivity()

    private let store = SettingsStore.appDefault()
    private let jobStore = JSONFileStore<[JobItem]>(name: "jobs.json")
    private let historyStore = JSONFileStore<[UploadResult]>(name: "upload-history.json")
    /// The last imported desktop config without credentials, so exports keep unknown keys.
    private let desktopBaseStore = JSONFileStore<[String: JSONValue]>(name: "desktop-config-base.json")
    private var tasks: [UUID: Task<Void, Never>] = [:]
    private var uploadsInFlight: Set<String> = []
    /// Uploads started from the queue, with their progress; finished and failed ones stay until dismissed.
    private(set) var uploadActivities: [UploadActivity] = []

    init() {
        settings = store.load()
        jobs = jobStore.load() ?? []
        uploadHistory = historyStore.load() ?? []
        // Jobs that were running when the app was killed cannot resume mid-way.
        for index in jobs.indices where jobs[index].state.isActive {
            jobs[index].state = .failed
            jobs[index].status = "Interrupted; tap Retry"
        }
    }

    /// UserDefaults key of the sharing agreement; the agreement sheet writes it with @AppStorage.
    static let shareAgreementKey = "shareAgreementAccepted"

    var hasAcceptedShareAgreement: Bool {
        UserDefaults.standard.bool(forKey: Self.shareAgreementKey)
    }

    var outputDirectory: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }

    func saveSettings() {
        do {
            try store.save(settings)
        } catch {
            message = "Could not save settings: \(error.localizedDescription)"
        }
    }

    // MARK: - Queue

    /// Adds one job per URL; `settingsFor` builds the job's settings for its source.
    func enqueue(_ urls: [URL], settingsFor: (SourceID?) -> AppSettings) {
        for url in urls.reversed() {
            let source = SourceCatalog.adapterType(for: url).map { $0.id }
            jobs.insert(JobItem(url: url, source: source, settings: settingsFor(source)), at: 0)
        }
        persistJobs()
        startNext()
    }

    func cancel(_ id: UUID) {
        guard let index = jobs.firstIndex(where: { $0.id == id }) else { return }
        if let task = tasks[id] {
            task.cancel()
        } else if jobs[index].state == .queued {
            jobs[index].state = .cancelled
            jobs[index].status = "Cancelled"
            persistJobs()
        }
    }

    /// Jobs a "Stop all" would stop: everything queued or running.
    var activeJobCount: Int {
        jobs.filter { $0.state.isActive }.count
    }

    /// Cancels every queued and running job through the per-job path. Queued jobs are marked
    /// first, so a finishing job cannot start one of them in between.
    func cancelAll() {
        for id in jobs.filter({ $0.state.isActive }).map(\.id) {
            cancel(id)
        }
    }

    func retry(_ id: UUID) {
        guard let index = jobs.firstIndex(where: { $0.id == id }), !jobs[index].state.isActive else { return }
        var job = jobs[index]
        job.state = .queued
        job.status = "Queued"
        job.fraction = 0
        job.error = nil
        job.skipped = []
        job.log = []
        job.finishedAt = nil
        jobs[index] = job
        persistJobs()
        startNext()
    }

    func remove(_ id: UUID, deleteFiles: Bool) {
        guard let index = jobs.firstIndex(where: { $0.id == id }) else { return }
        tasks[id]?.cancel()
        if deleteFiles {
            for file in jobs[index].files(in: outputDirectory) {
                try? FileManager.default.removeItem(at: file)
            }
        }
        jobs.remove(at: index)
        persistJobs()
    }

    func clearFinished() {
        jobs.removeAll { !$0.state.isActive }
        persistJobs()
    }

    func job(_ id: UUID) -> JobItem? {
        jobs.first { $0.id == id }
    }

    private func startNext() {
        let running = jobs.filter { $0.state == .running }.count
        guard running < maxConcurrentJobs,
              let next = jobs.lastIndex(where: { $0.state == .queued }) else { return }
        start(jobs[next].id)
        startNext()
    }

    private func start(_ id: UUID) {
        guard let job = job(id) else { return }
        update(id) {
            $0.state = .running
            $0.status = "Starting"
        }
        let logger = JobLogBridge { [weak self] level, line in
            Task { @MainActor in
                self?.update(id) { $0.appendLog("[\(level.rawValue)] \(line)") }
            }
        }
        let pipeline = DownloadPipeline(secrets: secrets, outputDirectory: outputDirectory, logger: logger)
        background.jobStarted(id, title: job.displayTitle) { [weak self] in self?.cancel(id) }

        // Uploading publishes a link, so automatic uploads wait for the sharing agreement too.
        var adjusted = job.settings
        if adjusted.upload.autoUpload, !hasAcceptedShareAgreement {
            adjusted.upload.autoUpload = false
            update(id) { $0.appendLog("[info] Auto-upload skipped: tap Upload on a finished file once and accept the sharing notice first.") }
        }
        let runSettings = adjusted

        tasks[id] = Task { [weak self] in
            var success = false
            do {
                let outcome = try await pipeline.run(url: job.url, settings: runSettings) { phase in
                    Task { @MainActor in
                        guard let self else { return }
                        self.update(id) { $0.apply(phase) }
                        if let item = self.job(id) {
                            self.background.jobProgress(id, fraction: item.fraction, status: item.status)
                        }
                    }
                }
                success = true
                self?.finish(id, outcome: outcome)
            } catch is CancellationError {
                self?.update(id) {
                    $0.state = .cancelled
                    $0.status = "Cancelled"
                }
            } catch {
                self?.update(id) {
                    $0.state = .failed
                    $0.status = error.localizedDescription
                    $0.error = error.localizedDescription
                }
            }
            guard let self else { return }
            self.tasks[id] = nil
            self.update(id) { $0.finishedAt = Date() }
            self.background.jobEnded(id, success: success)
            self.persistJobs()
            self.startNext()
        }
    }

    private func finish(_ id: UUID, outcome: JobOutcome) {
        let base = outputDirectory.standardizedFileURL.path
        update(id) { job in
            job.title = outcome.book.title
            job.bookID = outcome.book.id
            job.fileNames = outcome.files.map { file in
                let path = file.standardizedFileURL.path
                return path.hasPrefix(base + "/") ? String(path.dropFirst(base.count + 1)) : file.lastPathComponent
            }
            job.uploads = outcome.uploads
            job.skipped = outcome.skipped.map { "\($0.0.index). \($0.0.title): \($0.1)" }
            job.state = .finished
            job.apply(.finished)
            if !outcome.skipped.isEmpty {
                job.status = "Done, \(outcome.skipped.count) chapters skipped"
            }
        }
        for upload in outcome.uploads { recordUpload(upload) }
    }

    private func update(_ id: UUID, _ change: (inout JobItem) -> Void) {
        guard let index = jobs.firstIndex(where: { $0.id == id }) else { return }
        change(&jobs[index])
    }

    private func persistJobs() {
        jobStore.save(jobs)
    }

    // MARK: - Uploads

    func isUploading(_ file: URL) -> Bool {
        uploadsInFlight.contains(file.path)
    }

    func uploadActivity(for file: URL) -> UploadActivity? {
        uploadActivities.first { $0.file.path == file.path }
    }

    func dismissUploadActivity(_ file: URL) {
        guard !isUploading(file) else { return }
        uploadActivities.removeAll { $0.file.path == file.path }
    }

    /// Starts an upload, or asks for the sharing agreement first when it has not been accepted.
    func upload(_ file: URL, jobID: UUID? = nil) {
        guard hasAcceptedShareAgreement else {
            pendingUpload = PendingUpload(file: file, jobID: jobID)
            return
        }
        startUpload(file, jobID: jobID)
    }

    private func startUpload(_ file: URL, jobID: UUID?) {
        guard !uploadsInFlight.contains(file.path) else { return }
        uploadsInFlight.insert(file.path)
        uploadActivities.removeAll { $0.file.path == file.path }
        uploadActivities.insert(UploadActivity(file: file, jobID: jobID), at: 0)

        let uploadSettings = settings.upload
        let nameID = jobID.flatMap { job($0) }.map { UploadNaming.nameID(bookID: $0.bookID, url: $0.url) }
        let (stream, continuation) = AsyncStream<UploadEvent>.makeStream()
        let relay = Task { @MainActor [weak self] in
            for await event in stream { self?.updateActivity(file) { $0.apply(event) } }
        }
        Task {
            defer { uploadsInFlight.remove(file.path) }
            do {
                let result = try await UploaderCatalog.upload(
                    file, settings: uploadSettings, nameID: nameID, events: { continuation.yield($0) }
                )
                continuation.finish()
                await relay.value
                recordUpload(result)
                if let jobID { update(jobID) { $0.uploads.append(result) } }
                persistJobs()
                updateActivity(file) { $0.phase = .finished(result) }
            } catch is CancellationError {
                continuation.finish()
                await relay.value
                updateActivity(file) { $0.phase = .failed("cancelled") }
            } catch {
                continuation.finish()
                await relay.value
                updateActivity(file) { $0.phase = .failed(error.localizedDescription) }
            }
        }
    }

    private func updateActivity(_ file: URL, _ change: (inout UploadActivity) -> Void) {
        guard let index = uploadActivities.firstIndex(where: { $0.file.path == file.path }) else { return }
        change(&uploadActivities[index])
    }

    private func recordUpload(_ result: UploadResult) {
        uploadHistory.insert(result, at: 0)
        historyStore.save(uploadHistory)
        if settings.upload.copyLinkToClipboard {
            UIPasteboard.general.url = result.link
        }
    }

    func removeUploads(at offsets: IndexSet) {
        uploadHistory.remove(atOffsets: offsets)
        historyStore.save(uploadHistory)
    }

    func clearUploadHistory() {
        uploadHistory = []
        historyStore.save(uploadHistory)
    }

    // MARK: - npia account

    /// Signs in with the saved email and password; on success the session key is stored in the
    /// Keychain for later downloads.
    func signInToNpia() async -> Bool {
        guard let email = secrets.string(for: .npiaEmail), !email.isEmpty,
              let password = secrets.string(for: .npiaPassword), !password.isEmpty else { return false }
        let context = SourceContext(http: URLSessionHTTPClient(maxRetries: 1), web: WKWebEngine(), secrets: secrets, settings: settings)
        do {
            return try await NpiaAuth.login(email: email, password: password, context: context, cookies: SharedCookieStorageJar()) != nil
        } catch {
            return false
        }
    }

    // MARK: - Desktop config

    /// Imports a desktop `config.json`: settings are merged, credentials go to the Keychain.
    func importDesktopConfig(_ data: Data) throws -> Int {
        let (imported, secrets) = try DesktopConfig.importConfig(data, into: settings)
        settings = imported
        var stored = 0
        if let email = secrets.email { self.secrets.set(email, for: .npiaEmail); stored += 1 }
        if let password = secrets.password { self.secrets.set(password, for: .npiaPassword); stored += 1 }
        if let key = secrets.loginKey { self.secrets.set(key, for: .npiaLoginKey); stored += 1 }
        if var raw = try JSONSerialization.jsonObject(with: data) as? [String: Any] {
            for key in DesktopConfig.credentialKeys { raw.removeValue(forKey: key) }
            desktopBaseStore.save(raw.compactMapValues { JSONValue($0) })
        }
        return stored
    }

    /// Settings as a desktop `config.json`, layered on the last imported file. No credentials.
    func exportDesktopConfig() throws -> Data {
        let base = (desktopBaseStore.load() ?? [:]).mapValues(\.any)
        let exported = DesktopConfig.exportConfig(settings, onto: base)
        return try JSONSerialization.data(withJSONObject: exported, options: [.prettyPrinted, .sortedKeys])
    }
}

extension DesktopConfig {
    /// Keys that hold credentials in the desktop file; never stored or exported by the app.
    static let credentialKeys = ["email", "wd", "loginkey"]
}

/// JSON value that survives a Codable round trip, for keeping unknown desktop keys.
enum JSONValue: Codable, Equatable {
    case string(String), number(Double), integer(Int), bool(Bool), array([JSONValue]), object([String: JSONValue]), null

    init?(_ any: Any) {
        switch any {
        case let value as String: self = .string(value)
        case let value as NSNumber:
            if CFGetTypeID(value) == CFBooleanGetTypeID() {
                self = .bool(value.boolValue)
            } else if CFNumberIsFloatType(value) {
                self = .number(value.doubleValue)
            } else {
                self = .integer(value.intValue)
            }
        case let value as [Any]: self = .array(value.compactMap { JSONValue($0) })
        case let value as [String: Any]: self = .object(value.compactMapValues { JSONValue($0) })
        case is NSNull: self = .null
        default: return nil
        }
    }

    var any: Any {
        switch self {
        case .string(let value): return value
        case .number(let value): return value
        case .integer(let value): return value
        case .bool(let value): return value
        case .array(let value): return value.map(\.any)
        case .object(let value): return value.mapValues(\.any)
        case .null: return NSNull()
        }
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let value = try? container.decode(Bool.self) { self = .bool(value) }
        else if let value = try? container.decode(Int.self) { self = .integer(value) }
        else if let value = try? container.decode(Double.self) { self = .number(value) }
        else if let value = try? container.decode(String.self) { self = .string(value) }
        else if let value = try? container.decode([JSONValue].self) { self = .array(value) }
        else { self = .object(try container.decode([String: JSONValue].self)) }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .integer(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }
}
