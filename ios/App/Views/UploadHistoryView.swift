import NpiaUpload
import SwiftUI
import UIKit

struct UploadHistoryView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmClear = false

    var body: some View {
        List {
            // Finished uploads move to the history below; running and failed ones, and finished
            // ones with a ZIP that no host took, stay here.
            let pending = model.uploadActivities.filter { activity in
                if case .finished = activity.phase { return !activity.failedUploads.isEmpty }
                return true
            }
            if !pending.isEmpty {
                Section("In progress") {
                    ForEach(pending) { activity in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(activity.title).lineLimit(2)
                            UploadActivityView(
                                activity: activity,
                                retry: { model.upload(activity.request) },
                                dismiss: { model.dismissUploadActivity(activity.id) }
                            )
                        }
                        .padding(.vertical, 2)
                    }
                }
            }
            if model.uploadHistory.isEmpty && pending.isEmpty {
                ContentUnavailableView(
                    "No uploads yet", systemImage: "icloud.and.arrow.up",
                    description: Text("Finished files can be uploaded from the queue, or automatically in Settings > Upload.")
                )
            }
            ForEach(model.uploadHistory, id: \.self) { upload in
                UploadRow(upload: upload)
            }
            .onDelete { model.removeUploads(at: $0) }
        }
        .themedBackground()
        .navigationTitle("Uploads")
        .toolbar {
            if !model.uploadHistory.isEmpty {
                Button("Clear") { confirmClear = true }
            }
        }
        .confirmationDialog("Clear upload history?", isPresented: $confirmClear, titleVisibility: .visible) {
            Button("Clear history", role: .destructive) { model.clearUploadHistory() }
        } message: {
            Text("Links stay valid on the hosts; only the list here is cleared.")
        }
    }
}

struct UploadRow: View {
    let upload: UploadResult

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(upload.summary).lineLimit(2)
            HStack {
                Text(hostName)
                Text(upload.uploadedAt, style: .date)
                if let expires = upload.expiresAt {
                    if expires < Date() {
                        Text("expired").foregroundStyle(.red)
                    } else {
                        Text("expires \(expires, style: .relative)")
                    }
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            if upload.contents != nil {
                // What the ZIP holds, with the room a detail row has.
                DisclosureGroup("\(upload.titlesInside.count) inside") {
                    ForEach(Array(upload.titlesInside.enumerated()), id: \.offset) { _, title in
                        Text(title).font(.caption).foregroundStyle(.secondary)
                    }
                }
                .font(.caption)
            }
            HStack(spacing: 16) {
                Button {
                    UIPasteboard.general.url = upload.link
                } label: {
                    Label("Copy link", systemImage: "doc.on.doc")
                }
                if upload.contents != nil {
                    ShareLink(item: upload.linkLine) { Label("Share", systemImage: "square.and.arrow.up") }
                } else {
                    ShareLink(item: upload.link) { Label("Share", systemImage: "square.and.arrow.up") }
                }
                Link(destination: upload.link) { Label("Open", systemImage: "safari") }
                if let delete = upload.deleteLink {
                    Link(destination: delete) { Label("Delete page", systemImage: "trash") }
                }
            }
            .labelStyle(.iconOnly)
            .buttonStyle(.borderless)
        }
        .padding(.vertical, 2)
    }

    private var hostName: String {
        UploaderCatalog.uploader(id: upload.hostID)?.displayName ?? upload.hostID
    }
}
