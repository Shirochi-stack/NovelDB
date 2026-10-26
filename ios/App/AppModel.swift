import Foundation
import NpiaCore
import NpiaPipeline
import NpiaSources
import Observation

struct JobItem: Identifiable {
    let id = UUID()
    var url: URL
    var title: String?
    var phase: JobPhase = .queued
    var files: [URL] = []
    var links: [URL] = []
}

/// App-wide state. The merged-UI workstream grows this into the full queue and settings screens.
@MainActor
@Observable
final class AppModel {
    var settings: AppSettings
    var jobs: [JobItem] = []

    private let store = SettingsStore.appDefault()
    private let secrets = KeychainSecretStore()
    private var tasks: [UUID: Task<Void, Never>] = [:]

    init() {
        settings = store.load()
    }

    var outputDirectory: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }

    func saveSettings() {
        try? store.save(settings)
    }

    func canDownload(_ text: String) -> Bool {
        guard let url = URL(string: text.trimmingCharacters(in: .whitespaces)) else { return false }
        return SourceCatalog.adapterType(for: url) != nil
    }

    func enqueue(_ text: String) {
        guard let url = URL(string: text.trimmingCharacters(in: .whitespaces)) else { return }
        let job = JobItem(url: url)
        jobs.insert(job, at: 0)
        let pipeline = DownloadPipeline(secrets: secrets, outputDirectory: outputDirectory)
        let settings = settings
        let id = job.id
        tasks[id] = Task {
            do {
                let outcome = try await pipeline.run(url: url, settings: settings) { phase in
                    Task { @MainActor in self.update(id) { $0.phase = phase } }
                }
                update(id) {
                    $0.title = outcome.book.title
                    $0.files = outcome.files
                    $0.links = outcome.uploads.map(\.link)
                }
            } catch is CancellationError {
                update(id) { $0.phase = .failed("Cancelled") }
            } catch {
                update(id) { $0.phase = .failed(error.localizedDescription) }
            }
            tasks[id] = nil
        }
    }

    func cancel(_ id: UUID) {
        tasks[id]?.cancel()
    }

    private func update(_ id: UUID, _ change: (inout JobItem) -> Void) {
        guard let index = jobs.firstIndex(where: { $0.id == id }) else { return }
        change(&jobs[index])
    }
}
