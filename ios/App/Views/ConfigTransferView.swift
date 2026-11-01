import NpiaCore
import SwiftUI
import UniformTypeIdentifiers

/// Imports and exports the desktop `config.json` and the app's own settings file.
struct ConfigTransferView: View {
    @Environment(AppModel.self) private var model
    @State private var importing = false
    @State private var exportDocument: JSONDocument?
    @State private var exportName = "config"
    @State private var confirmReset = false

    var body: some View {
        Form {
            Section {
                Button {
                    importing = true
                } label: {
                    Label("Import desktop config.json", systemImage: "square.and.arrow.down")
                }
                Button {
                    export(name: "config") { try model.exportDesktopConfig() }
                } label: {
                    Label("Export as desktop config.json", systemImage: "square.and.arrow.up")
                }
            } header: {
                Text("Desktop downloader")
            } footer: {
                Text("Import merges download, image, text, and source settings. Credentials in the file go to the Keychain and are never exported.")
            }

            Section("App settings") {
                Button {
                    export(name: "npia-mobile-settings") { try SettingsCoding.encode(model.settings) }
                } label: {
                    Label("Export app settings", systemImage: "square.and.arrow.up")
                }
                Button("Reset all settings", role: .destructive) { confirmReset = true }
            }
        }
        .themedBackground()
        .navigationTitle("Import / export")
        .fileImporter(isPresented: $importing, allowedContentTypes: [.json, .plainText, .data]) { result in
            handleImport(result)
        }
        .fileExporter(
            isPresented: Binding(get: { exportDocument != nil }, set: { if !$0 { exportDocument = nil } }),
            document: exportDocument,
            contentType: .json,
            defaultFilename: exportName
        ) { result in
            if case .failure(let error) = result {
                model.message = "Export failed: \(error.localizedDescription)"
            }
            exportDocument = nil
        }
        .confirmationDialog("Reset all settings to defaults?", isPresented: $confirmReset, titleVisibility: .visible) {
            Button("Reset", role: .destructive) { model.settings = AppSettings() }
        }
    }

    private func export(name: String, _ make: () throws -> Data) {
        do {
            exportName = name
            exportDocument = JSONDocument(data: try make())
        } catch {
            model.message = "Export failed: \(error.localizedDescription)"
        }
    }

    private func handleImport(_ result: Result<URL, Error>) {
        do {
            let url = try result.get()
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let data = try Data(contentsOf: url)
            if let settings = try? SettingsCoding.decode(data), isAppSettingsFile(data) {
                model.settings = settings
                model.message = "Imported app settings."
                return
            }
            let stored = try model.importDesktopConfig(data)
            model.message = stored > 0
                ? "Imported settings; \(stored) credential values moved to the Keychain."
                : "Imported settings."
        } catch {
            model.message = "Import failed: \(error.localizedDescription)"
        }
    }

    /// App exports carry `schemaVersion` and a `shared` object; desktop files are flat.
    private func isAppSettingsFile(_ data: Data) -> Bool {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
        return object["schemaVersion"] != nil && object["shared"] is [String: Any]
    }
}

struct JSONDocument: FileDocument {
    static var readableContentTypes: [UTType] { [.json] }
    var data: Data

    init(data: Data) {
        self.data = data
    }

    init(configuration: ReadConfiguration) throws {
        data = configuration.file.regularFileContents ?? Data()
    }

    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        FileWrapper(regularFileWithContents: data)
    }
}
