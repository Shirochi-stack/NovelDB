import NpiaCore
import NpiaUpload
import SwiftUI

/// Hosts are tried in order until one accepts the file.
struct UploadSettingsForm: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Form {
            Section {
                Toggle("Upload finished files", isOn: $model.settings.upload.autoUpload)
                Toggle("Copy link to clipboard", isOn: $model.settings.upload.copyLinkToClipboard)
            }

            Section {
                ForEach(model.settings.upload.hosts, id: \.self) { id in
                    HostRow(id: id)
                }
                .onMove { model.settings.upload.hosts.move(fromOffsets: $0, toOffset: $1) }
                .onDelete { offsets in
                    guard model.settings.upload.hosts.count > offsets.count else { return }
                    model.settings.upload.hosts.remove(atOffsets: offsets)
                }
                let available = UploaderCatalog.all.filter { !model.settings.upload.hosts.contains($0.id) }
                if !available.isEmpty {
                    Menu {
                        ForEach(available, id: \.id) { uploader in
                            Button(uploader.displayName) { model.settings.upload.hosts.append(uploader.id) }
                        }
                    } label: {
                        Label("Add host", systemImage: "plus")
                    }
                }
            } header: {
                Text("Hosts, in order")
            } footer: {
                Text("If a host refuses the file (too large, offline) the next one is tried.")
            }

            if model.settings.upload.hosts.contains("gdrive") {
                Section {
                    TextField("Folder ID (optional)", text: Binding(
                        get: { model.settings.upload.googleDriveFolderID ?? "" },
                        set: { model.settings.upload.googleDriveFolderID = $0.isEmpty ? nil : $0 }
                    ))
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                } header: {
                    Text("Google Drive")
                } footer: {
                    Text("Drive needs an account; sign-in is handled by the uploader.")
                }
            }
        }
        .themedBackground()
        .navigationTitle("Upload")
        .toolbar { EditButton() }
    }
}

private struct HostRow: View {
    let id: String

    var body: some View {
        if let uploader = UploaderCatalog.uploader(id: id) {
            VStack(alignment: .leading, spacing: 2) {
                Text(uploader.displayName)
                Text(details(uploader)).font(.caption).foregroundStyle(.secondary)
            }
        } else {
            Text(id).foregroundStyle(.secondary)
        }
    }

    private func details(_ uploader: any Uploader) -> String {
        var parts: [String] = []
        if let maxBytes = uploader.maxBytes {
            parts.append("up to " + ByteCountFormatter.string(fromByteCount: maxBytes, countStyle: .file))
        } else {
            parts.append("no size limit listed")
        }
        if let retention = uploader.retention {
            let formatter = DateComponentsFormatter()
            formatter.allowedUnits = [.day, .hour]
            formatter.unitsStyle = .full
            formatter.maximumUnitCount = 1
            parts.append("kept " + (formatter.string(from: retention) ?? "temporarily"))
        }
        if uploader.requiresAccount { parts.append("account") }
        return parts.joined(separator: " · ")
    }
}
