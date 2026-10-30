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
        didSet {
            if settings != oldValue { saveSettings() }
            web.pageLoadWait = TimeInterval(settings.shared.pageLoadTimeoutSeconds)
            // Opting in to engine updates (the toggle, or an imported desktop config) checks right away.
            let optedIn = RuleEngine.allCases.filter {
                settings.engines.autoUpdate($0.siteEngine) && !oldValue.engines.autoUpdate($0.siteEngine)
            }
            if !optedIn.isEmpty { Task { await checkEngineUpdates(automatic: true, only: optedIn) } }
            // Turning the app update check back on (the toggle, or an imported desktop config) looks
            // once the day's check is due.
            if settings.appUpdateCheck && !oldValue.appUpdateCheck { Task { await checkAppUpdate(automatic: true) } }
        }
    }
    private(set) var jobs: [JobItem] = []
    private(set) var uploadHistory: [UploadResult] = []
    /// Shown as an alert by the root view.
    var message: String?
    /// An upload waiting for the one-time sharing agreement; the root view shows it as a sheet.
    var pendingUpload: PendingUpload?
    /// Which half of the Queue tab shows: the jobs, or the log of all of them.
    var queueSection: QueueSection = .jobs
    /// The job detail pages open in the Queue tab (the navigation stack's path).
    var queuePath: [UUID] = []
    /// The job the Queue log is filtered to; nil shows every job.
    var logFilterJobID: UUID?
    /// A tab to switch to (set when a notification is tapped); the root view clears it.
    var requestedPanel: Panel?
    /// Bumped with every log line, so views that show the log refresh.
    private(set) var logRevision = 0
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
    /// What the last update check said for each rule engine (Settings > Site engines).
    private(set) var engineUpdateMessages: [RuleEngine: String] = [:]
    /// The copy of each rule engine in use, for the same screen.
    private(set) var engineBundleLabels: [RuleEngine: String] = [:]
    private(set) var checkingEngineUpdates = false
    /// Looks for newer builds of the app itself (Settings > App updates) and downloads one on request.
    let appUpdate = AppUpdateController()

    let secrets: any SecretStore = KeychainSecretStore()
    /// The web engine downloads and the sign-in browser share.
    let web = WKWebEngine()
    let background = BackgroundActivity()
    /// Bumped when a sign-in or sign-out ends, so the Enter App screen reads the sessions again.
    private(set) var sessionVersion = 0
    /// An sfc app sign-in is running.
    private(set) var sfcWorking = false

    private let store = SettingsStore.appDefault()
    private let jobStore = JSONFileStore<[JobItem]>(name: "jobs.json")
    private let historyStore = JSONFileStore<[UploadResult]>(name: "upload-history.json")
    /// The last imported desktop config without credentials, so exports keep unknown keys.
    private let desktopBaseStore = JSONFileStore<[String: JSONValue]>(name: "desktop-config-base.json")
    private var tasks: [UUID: Task<Void, Never>] = [:]
    /// Uploads started from the queue, with their progress; finished and failed ones stay until dismissed.
    private(set) var uploadActivities: [UploadActivity] = []
    /// The combined log of every job: the last 5,000 lines, kept in `Application Support/queue-log.jsonl`.
    let logBuffer = LogBuffer(
        fileURL: FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("queue-log.jsonl")
    )
    /// The jobs started since the queue was last idle: the notification at the end of the run
    /// opens the job's page when this was a single book.
    private var runJobIDs: [UUID] = []

    init() {
        settings = store.load()
        web.pageLoadWait = TimeInterval(settings.shared.pageLoadTimeoutSeconds)
        jobs = jobStore.load() ?? []
        uploadHistory = historyStore.load() ?? []
        // Jobs that were running when the app was killed cannot resume mid-way.
        for index in jobs.indices where jobs[index].state.isActive {
            jobs[index].state = .failed
            jobs[index].status = "Interrupted; tap Retry"
            logBuffer.append(.warning, "Interrupted when the app was closed; tap Retry", jobID: jobs[index].id, jobTitle: jobs[index].displayTitle)
        }
        QueueNotifier.shared.setHandler { [weak self] target in self?.open(target) }
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
        for link in urls.reversed() {
            // A phone-site link and its desktop twin are one book.
            let url = SourceCatalog.canonicalURL(link)
            let source = SourceCatalog.adapterType(for: url).map { $0.id }
            let job = JobItem(url: url, source: source, settings: settingsFor(source))
            jobs.insert(job, at: 0)
            runJobIDs.append(job.id)
            log(job.id, .info, "Queued \(url.absoluteString)" + (source.map { " (\($0.rawValue))" } ?? ""))
        }
        if !urls.isEmpty { QueueNotifier.shared.requestAuthorizationIfNeeded() }
        persistJobs()
        startNext()
    }

    func cancel(_ id: UUID) {
        guard let index = jobs.firstIndex(where: { $0.id == id }) else { return }
        if let task = tasks[id] {
            log(id, .info, "Cancel requested")
            task.cancel()
        } else if jobs[index].state == .queued {
            jobs[index].state = .cancelled
            jobs[index].status = "Cancelled"
            log(id, .info, "Cancelled before it started")
            persistJobs()
            finishRunIfIdle()
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
        if !runJobIDs.contains(id) { runJobIDs.append(id) }
        log(id, .info, "Retry: queued again")
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
        // The time is taken here, where the line is written, so lines keep their order in the log.
        let logger = JobLogBridge { [weak self] level, line in
            let date = Date()
            Task { @MainActor in
                self?.log(id, level, line, date: date)
            }
        }
        log(id, .info, "Starting \(job.url.absoluteString)")
        let pipeline = DownloadPipeline(web: web, secrets: secrets, outputDirectory: outputDirectory, logger: logger)
        background.jobStarted(id, title: job.displayTitle) { [weak self] in self?.cancel(id) }

        // Uploading publishes a link, so automatic uploads wait for the sharing agreement too.
        var adjusted = job.settings
        if adjusted.upload.autoUpload, !hasAcceptedShareAgreement {
            adjusted.upload.autoUpload = false
            log(id, .info, "Auto-upload skipped: tap Upload on a finished file once and accept the sharing notice first.")
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
                self?.log(id, .info, "Cancelled")
            } catch {
                self?.update(id) {
                    $0.state = .failed
                    $0.status = error.localizedDescription
                    $0.error = error.localizedDescription
                }
                self?.log(id, .error, "Failed: \(error.localizedDescription)")
            }
            guard let self else { return }
            self.tasks[id] = nil
            self.update(id) { $0.finishedAt = Date() }
            self.background.jobEnded(id, success: success)
            self.persistJobs()
            self.startNext()
            self.finishRunIfIdle()
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
        log(id, .info, "Finished: \(outcome.files.count) file" + (outcome.files.count == 1 ? "" : "s") + (outcome.skipped.isEmpty ? "" : ", \(outcome.skipped.count) chapters skipped"))
        recordUploads(outcome.uploads)
    }

    private func update(_ id: UUID, _ change: (inout JobItem) -> Void) {
        guard let index = jobs.firstIndex(where: { $0.id == id }) else { return }
        change(&jobs[index])
    }

    private func persistJobs() {
        jobStore.save(jobs)
    }

    // MARK: - Log

    /// Adds a line to the job's own log and to the combined queue log. `title` names lines that
    /// belong to no single job.
    func log(_ jobID: UUID?, _ level: LogLevel, _ message: String, title: String? = nil, date: Date = Date()) {
        var jobTitle = title ?? ""
        if let jobID {
            update(jobID) { $0.appendLog("[\(level.rawValue)] \(message)") }
            jobTitle = job(jobID)?.displayTitle ?? jobTitle
        }
        logBuffer.append(level, message, jobID: jobID, jobTitle: jobTitle, date: date)
        logRevision &+= 1
    }

    func clearQueueLog() {
        logBuffer.clear()
        logRevision &+= 1
    }

    /// Opens the Log of the Queue tab, filtered to one job (nil for every job).
    func showLog(for jobID: UUID?) {
        queueSection = .log
        logFilterJobID = jobID
        queuePath = []
        requestedPanel = .queue
    }

    // MARK: - Notifications

    /// Where a tap on a queue notification goes: the Log filtered to every job, or the job's own page.
    func open(_ target: QueueTarget) {
        switch target.resolved(existing: Set(jobs.map(\.id))) {
        case .job(let id):
            queueSection = .jobs
            queuePath = [id]
            requestedPanel = .queue
        case .log:
            showLog(for: nil)
        }
    }

    /// When the queue has gone idle, tells the user how the run went, if the app is not in front.
    private func finishRunIfIdle() {
        guard activeJobCount == 0, !runJobIDs.isEmpty else { return }
        let run = runJobIDs
        runJobIDs = []
        let ended = run.compactMap { job($0) }
        guard !ended.isEmpty, UIApplication.shared.applicationState != .active else { return }
        let summary = QueueRunSummary(
            finished: ended.filter { $0.state == .finished }.count,
            failed: ended.filter { $0.state == .failed }.count,
            cancelled: ended.filter { $0.state == .cancelled }.count
        )
        QueueNotifier.shared.post(title: summary.title, body: summary.body, target: QueueTarget.forRun(run))
    }

    /// Called when the app leaves the foreground: writes the log out, and says downloads go on.
    func appDidEnterBackground() {
        logBuffer.flush()
        let running = activeJobCount
        guard running > 0 else { return }
        QueueNotifier.shared.post(
            title: "Downloads continue",
            body: running == 1 ? "1 book is still downloading" : "\(running) books are still downloading",
            target: QueueTarget.forRun(runJobIDs)
        )
    }

    func appBecameActive() {
        QueueNotifier.shared.clearDelivered()
    }

    // MARK: - Uploads

    func isUploading(_ file: URL) -> Bool {
        uploadActivities.contains { $0.isRunning && $0.covers(file) }
    }

    func uploadActivity(for file: URL) -> UploadActivity? {
        uploadActivities.first { $0.covers(file) }
    }

    func dismissUploadActivity(_ id: UUID) {
        guard uploadActivities.first(where: { $0.id == id })?.isRunning == false else { return }
        uploadActivities.removeAll { $0.id == id }
    }

    /// The files of a finished job that are still on disk.
    func existingFiles(of job: JobItem) -> [URL] {
        guard job.state == .finished else { return [] }
        return job.files(in: outputDirectory).filter { FileManager.default.fileExists(atPath: $0.path) }
    }

    /// A finished job with files to share or upload.
    func isUploadable(_ job: JobItem) -> Bool {
        !existingFiles(of: job).isEmpty
    }

    /// The files of these jobs, in queue order, for Share and Upload.
    func uploadRequest(forJobs ids: Set<UUID>) -> UploadRequest {
        var entries: [UploadRequest.Entry] = []
        for job in jobs where ids.contains(job.id) {
            let nameID = UploadNaming.nameID(source: job.source?.rawValue, bookID: job.bookID, url: job.url)
            for file in existingFiles(of: job) {
                entries.append(UploadRequest.Entry(file: file, jobID: job.id, title: job.title, nameID: nameID))
            }
        }
        return UploadRequest(entries: entries)
    }

    /// One file, with the name and title of the job it came from when there is one.
    func uploadRequest(file: URL, jobID: UUID?) -> UploadRequest {
        var entry = UploadRequest.Entry(file: file, jobID: jobID, title: nil, nameID: nil)
        if let jobID, let source = self.job(jobID) {
            entry.title = source.title
            entry.nameID = UploadNaming.nameID(source: source.source?.rawValue, bookID: source.bookID, url: source.url)
        }
        return UploadRequest(entries: [entry])
    }

    func upload(_ file: URL, jobID: UUID? = nil) {
        upload(uploadRequest(file: file, jobID: jobID))
    }

    /// Uploads the files of these finished jobs; with ZIP bundles on they go up as few ZIPs as the size limit allows.
    func upload(jobs ids: Set<UUID>) {
        upload(uploadRequest(forJobs: ids))
    }

    /// Starts an upload, or asks for the sharing agreement first when it has not been accepted.
    func upload(_ request: UploadRequest) {
        guard !request.entries.isEmpty else {
            message = "There are no files to upload."
            return
        }
        guard hasAcceptedShareAgreement else {
            pendingUpload = PendingUpload(request: request)
            return
        }
        startUpload(request)
    }

    private func startUpload(_ request: UploadRequest) {
        guard !request.files.allSatisfy({ isUploading($0) }) else { return }
        uploadActivities.removeAll { !$0.isRunning && $0.request == request }
        let activity = UploadActivity(request: request)
        let id = activity.id
        uploadActivities.insert(activity, at: 0)

        let jobIDs = request.jobIDs
        let uploadSettings = settings.upload
        let items = request.items
        let fileCount = items.count
        // One job: its lines go to its own log. Several: a line each at the start, the rest under "Upload".
        let single = jobIDs.count == 1 ? jobIDs[0] : nil
        for jobID in jobIDs {
            log(jobID, .info, "Upload started" + (fileCount > 1 ? " (\(fileCount) files)" : ""))
        }
        if single == nil { log(nil, .info, "Uploading \(fileCount) files", title: "Upload") }

        let (stream, continuation) = AsyncStream<UploadBatchEvent>.makeStream()
        let relay = Task { @MainActor [weak self] in
            for await event in stream {
                self?.updateActivity(id) { $0.apply(event) }
                self?.logUploadEvent(event, jobID: single)
            }
        }
        Task {
            do {
                let outcome = try await UploaderCatalog.uploadBatch(
                    items, settings: uploadSettings, history: nil, events: { continuation.yield($0) }
                )
                continuation.finish()
                await relay.value
                finishUpload(id, request: request, outcome: outcome)
            } catch is CancellationError {
                continuation.finish()
                await relay.value
                updateActivity(id) { $0.phase = .failed("cancelled") }
            } catch {
                continuation.finish()
                await relay.value
                log(single, .error, "Upload failed: \(error.localizedDescription)", title: "Upload")
                updateActivity(id) { $0.phase = .failed(error.localizedDescription) }
            }
        }
    }

    private func finishUpload(_ id: UUID, request: UploadRequest, outcome: UploadBatchOutcome) {
        for result in outcome.uploads {
            let names = Set(result.contents?.map(\.fileName) ?? [result.fileName])
            for jobID in request.jobIDs {
                let covered = request.entries.contains { entry in
                    entry.jobID == jobID && names.contains(entry.file.lastPathComponent.precomposedStringWithCanonicalMapping)
                }
                guard covered else { continue }
                update(jobID) { $0.uploads.append(result) }
                if request.jobIDs.count > 1 {
                    log(jobID, .info, "Uploaded in \(result.summary): \(result.link.absoluteString)")
                }
            }
        }
        recordUploads(outcome.uploads)
        persistJobs()
        if outcome.uploads.isEmpty {
            let reason = outcome.failures.first?.message ?? "no host took the files"
            updateActivity(id) { $0.phase = .failed(reason) }
        } else {
            updateActivity(id) { $0.phase = .finished }
        }
    }

    private func logUploadEvent(_ event: UploadBatchEvent, jobID: UUID?) {
        switch event {
        case .starting(let index, let total, let name):
            log(jobID, .info, total > 1 ? "Upload \(index) of \(total): \(name)" : "Uploading \(name)", title: "Upload")
        case .preparing(let zip, let total, let name):
            log(jobID, .info, "Preparing ZIP \(zip) of \(total): \(name)", title: "Upload")
        case .host(let hostEvent):
            switch hostEvent {
            case .trying(let host, let position, let total):
                log(jobID, .debug, "Trying \(host) (\(position) of \(total))", title: "Upload")
            case .progress:
                break
            case .failed(let host, let message):
                log(jobID, .warning, "\(host) failed: \(message)", title: "Upload")
            }
        case .uploaded(let result):
            log(jobID, .info, "Uploaded \(result.summary): \(result.link.absoluteString)", title: "Upload")
        case .failed(let name, let message):
            log(jobID, .warning, "Upload of \(name) failed: \(message)", title: "Upload")
        }
    }

    private func updateActivity(_ id: UUID, _ change: (inout UploadActivity) -> Void) {
        guard let index = uploadActivities.firstIndex(where: { $0.id == id }) else { return }
        change(&uploadActivities[index])
    }

    /// Adds finished uploads to the history, newest batch first, and copies the link(s) when asked to.
    private func recordUploads(_ results: [UploadResult]) {
        guard !results.isEmpty else { return }
        uploadHistory.insert(contentsOf: results, at: 0)
        historyStore.save(uploadHistory)
        guard settings.upload.copyLinkToClipboard else { return }
        if results.count == 1 {
            UIPasteboard.general.url = results[0].link
        } else {
            UIPasteboard.general.string = results.map(\.linkLine).joined(separator: "\n")
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
        let http = URLSessionHTTPClient(maxRetries: 1, minTimeout: TimeInterval(settings.shared.requestTimeoutSeconds))
        let context = SourceContext(http: http, web: web, secrets: secrets, settings: settings)
        do {
            return try await NpiaAuth.login(email: email, password: password, context: context, cookies: SharedCookieStorageJar()) != nil
        } catch {
            return false
        }
    }

    // MARK: - Enter App: sfc, qdn and rbooks

    /// Status, saved copies and sign-out of the browser-session logins (qdn, rbooks), over the cookies of the
    /// web engine downloads use.
    var sessions: SiteSessions {
        SiteSessions(secrets: secrets, cookies: web.sessionCookies)
    }

    /// Puts back a login the web view dropped when the app was stopped (qdn keeps its login in session
    /// cookies). Done once per run, at start; it never overwrites a cookie the browser has.
    func restoreSessions() async {
        for site in HardSessions.all { await sessions.restoreOnce(site) }
        sessionVersion += 1
    }

    /// Copies the current logins to the Keychain, so a stop of the app cannot lose session cookies.
    func snapshotSessions() async {
        for site in HardSessions.all { await sessions.snapshot(site) }
    }

    /// Opens the site's sign-in page in the app's own browser (the one the Browser tab uses, whose
    /// cookies downloads share) and reads the status again when it closes.
    func signInToSite(_ site: SessionSite) async {
        do {
            try await web.presentLogin(url: site.signInURL, profile: BrowserProfile.name, userAgentSuffix: site.appUserAgentSuffix)
        } catch is CancellationError {
            // Closed without finishing: keep whatever login is there.
        } catch {
            message = "The sign-in browser could not open: \(error.localizedDescription)"
        }
        await sessions.snapshot(site)
        // An sfc website sign-in may have been handed an app session; keep it for VIP text.
        if site.source == .sfc, !SfcAdapter.hasAppSession(secrets),
           await SfcAdapter.captureAppSession(cookies: web.sessionCookies, secrets: secrets) {
            message = "sfc app session captured — VIP chapters you bought download as text"
        }
        sessionVersion += 1
    }

    func signOutOfSite(_ site: SessionSite) async {
        await sessions.signOut(site)
        sessionVersion += 1
        message = "Signed out of \(site.source.rawValue)"
    }

    /// The Browser tab signed out of `domain` (or of everything when nil): the saved copies must go too,
    /// or the next start would restore the login.
    func forgetSavedSessions(forDomain domain: String?) {
        let sites = domain.map { HardSessions.forHost($0) } ?? HardSessions.all
        for site in sites { sessions.forgetSavedCopy(site) }
        sessionVersion += 1
    }

    var sfcSignedIn: Bool {
        SfcAdapter.hasAppSession(secrets)
    }

    /// Signs in to the sfc app API; the session cookie is kept in the Keychain.
    func signInToSfc(username: String, password: String) async {
        guard !sfcWorking else { return }
        sfcWorking = true
        let ok = (try? await SfcAdapter.login(username: username, password: password, http: URLSessionHTTPClient(maxRetries: 1, minTimeout: TimeInterval(settings.shared.requestTimeoutSeconds)), secrets: secrets)) ?? false
        sfcWorking = false
        sessionVersion += 1
        message = ok ? "Signed in to sfc" : "sfc sign-in failed: check the username and password"
    }

    /// Saves a cookie header copied from the sfc app (it must hold `session_APP`).
    @discardableResult
    func saveSfcCookie(_ cookie: String) -> Bool {
        let ok = SfcAdapter.saveAppCookie(cookie, secrets: secrets)
        sessionVersion += 1
        message = ok ? "sfc app cookie saved" : "That cookie has no session_APP"
        return ok
    }

    func signOutOfSfc() {
        SfcAdapter.signOut(secrets)
        sessionVersion += 1
        message = "Signed out of sfc"
    }

    // MARK: - Site engines

    /// Looks for newer rule engines. `automatic` is the daily check of the engines the user opted
    /// in to; otherwise every engine (or just `only`) is checked now.
    func checkEngineUpdates(automatic: Bool, only engines: [RuleEngine]? = nil) async {
        guard !checkingEngineUpdates else { return }
        let wanted = (engines ?? RuleEngine.allCases).filter { !automatic || settings.engines.autoUpdate($0.siteEngine) }
        guard !wanted.isEmpty else { return }
        checkingEngineUpdates = true
        for engine in wanted {
            let result: EngineUpdateResult?
            if automatic {
                result = await EngineUpdater.shared.checkIfDue(engine)
            } else {
                result = await EngineUpdater.shared.check(engine)
            }
            if let result { engineUpdateMessages[engine] = result.message }
        }
        checkingEngineUpdates = false
        await refreshEngineBundles()
    }

    /// Reads which copy of each rule engine is in use (off the main thread: it checks the files).
    func refreshEngineBundles() async {
        let labels = await Task.detached(priority: .utility) { () -> [String: String] in
            var found: [String: String] = [:]
            for engine in RuleEngine.allCases {
                found[engine.rawValue] = EngineStore.shared.current(engine)?.label ?? "missing from this build"
            }
            return found
        }.value
        for engine in RuleEngine.allCases {
            engineBundleLabels[engine] = labels[engine.rawValue]
        }
    }

    // MARK: - App updates

    /// Looks for a newer build of NovelDB. `automatic` is the check at start and on returning to the
    /// foreground: it only runs when the setting is on and a day has passed since the last check.
    /// Otherwise it is the "Check for app updates now" button.
    func checkAppUpdate(automatic: Bool) async {
        await appUpdate.check(automatic: automatic, enabled: settings.appUpdateCheck)
    }

    #if DEBUG
    /// The live download check of the iOS workflow (its `live_url` input). Launched with
    /// `-NovelDBLiveDownload <url>`, the app queues the book's first three chapters as EPUB and TXT
    /// and writes `live-download-report.txt` to Documents when the job ends.
    func runLiveDownloadIfAsked() async {
        let arguments = ProcessInfo.processInfo.arguments
        guard let flag = arguments.firstIndex(of: "-NovelDBLiveDownload"), arguments.indices.contains(flag + 1),
              let url = URL(string: arguments[flag + 1]) else { return }
        var run = settings
        run.shared.formats = [.epub, .txt]
        run.shared.rangeFrom = 1
        run.shared.rangeTo = 3
        run.shared.intervalMin = 0.5
        run.shared.intervalMax = 1
        run.upload.autoUpload = false
        enqueue([url]) { _ in run }
        guard let id = jobs.first?.id else { return }
        while let job = job(id), job.state.isActive {
            try? await Task.sleep(nanoseconds: 1_000_000_000)
        }
        // Log lines reach the job through the main actor; let the last ones land.
        try? await Task.sleep(nanoseconds: 1_000_000_000)
        guard let job = job(id) else { return }
        let lines = [
            "state: \(job.state.rawValue)",
            "status: \(job.status)",
            "title: \(job.displayTitle)",
            "files: \(job.fileNames.joined(separator: ", "))",
            "skipped: \(job.skipped.count)",
            "log:",
        ] + job.log
        try? lines.joined(separator: "\n").write(
            to: outputDirectory.appendingPathComponent("live-download-report.txt"), atomically: true, encoding: .utf8)
    }
    #endif

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
