import NpiaCore
import SwiftUI
import UIKit

struct QueueView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        List {
            if model.jobs.isEmpty {
                ContentUnavailableView("No downloads yet", systemImage: "arrow.down.circle", description: Text("Add a URL to start."))
            }
            ForEach(model.jobs) { job in
                NavigationLink(value: job.id) {
                    JobRow(job: job)
                }
                .swipeActions(edge: .trailing) {
                    if job.state.isActive {
                        Button("Cancel", role: .destructive) { model.cancel(job.id) }
                    } else {
                        Button("Remove", role: .destructive) { model.remove(job.id, deleteFiles: false) }
                        if job.state != .finished {
                            Button("Retry") { model.retry(job.id) }.tint(.blue)
                        }
                    }
                }
                .contextMenu {
                    if job.state.isActive {
                        Button("Cancel", systemImage: "xmark.circle") { model.cancel(job.id) }
                    } else {
                        Button("Retry", systemImage: "arrow.clockwise") { model.retry(job.id) }
                    }
                    let files = job.files(in: model.outputDirectory)
                    if !files.isEmpty {
                        ShareLink(items: files) { Label("Share files", systemImage: "square.and.arrow.up") }
                        Button("Show in Files", systemImage: "folder") { FilesApp.reveal(files[0]) }
                    }
                }
            }
        }
        .navigationTitle("Queue")
        .navigationDestination(for: UUID.self) { id in
            JobDetailView(jobID: id)
        }
        .toolbar {
            if model.jobs.contains(where: { !$0.state.isActive }) {
                Button("Clear finished") { model.clearFinished() }
            }
        }
    }
}

struct JobRow: View {
    let job: JobItem

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                stateIcon
                Text(job.displayTitle).lineLimit(1)
                Spacer()
                if let source = job.source {
                    Text(source.rawValue).font(.caption2).padding(.horizontal, 6).padding(.vertical, 2)
                        .background(.quaternary, in: Capsule())
                }
            }
            if job.state == .running {
                ProgressView(value: job.fraction)
            }
            Text(job.status).font(.caption).foregroundStyle(job.state == .failed ? .red : .secondary).lineLimit(2)
        }
        .padding(.vertical, 2)
    }

    @ViewBuilder
    private var stateIcon: some View {
        switch job.state {
        case .queued: Image(systemName: "clock").foregroundStyle(.secondary)
        case .running: Image(systemName: "arrow.down.circle").foregroundStyle(.blue)
        case .finished: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case .failed: Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
        case .cancelled: Image(systemName: "xmark.circle").foregroundStyle(.secondary)
        }
    }
}

struct JobDetailView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let jobID: UUID
    @State private var confirmDelete = false

    var body: some View {
        if let job = model.job(jobID) {
            content(job)
        } else {
            ContentUnavailableView("Removed", systemImage: "trash")
        }
    }

    private func content(_ job: JobItem) -> some View {
        let files = job.files(in: model.outputDirectory)
        return List {
            Section("Status") {
                JobRow(job: job)
                LabeledContent("URL") {
                    Link(job.url.host ?? job.url.absoluteString, destination: job.url)
                }
                if let error = job.error {
                    Text(error).foregroundStyle(.red).textSelection(.enabled)
                }
                if job.state.isActive {
                    Button("Cancel", role: .destructive) { model.cancel(job.id) }
                } else {
                    Button("Retry") { model.retry(job.id) }
                }
            }

            if !files.isEmpty {
                Section("Files") {
                    ForEach(files, id: \.self) { file in
                        FileRow(file: file, jobID: job.id)
                    }
                    ShareLink(items: files) { Label("Share all", systemImage: "square.and.arrow.up") }
                }
            }

            if !job.uploads.isEmpty {
                Section("Links") {
                    ForEach(job.uploads, id: \.self) { upload in
                        UploadRow(upload: upload)
                    }
                }
            }

            if !job.skipped.isEmpty {
                Section("Skipped chapters (\(job.skipped.count))") {
                    ForEach(job.skipped, id: \.self) { Text($0).font(.caption) }
                }
            }

            if !job.log.isEmpty {
                Section("Log") {
                    ForEach(Array(job.log.suffix(200).enumerated()), id: \.offset) { _, line in
                        Text(line).font(.caption.monospaced()).textSelection(.enabled)
                    }
                }
            }

            if !job.state.isActive {
                Section {
                    Button("Remove from queue", role: .destructive) { confirmDelete = true }
                }
            }
        }
        .navigationTitle(job.displayTitle)
        .navigationBarTitleDisplayMode(.inline)
        .confirmationDialog("Remove this download?", isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("Remove and delete files", role: .destructive) {
                model.remove(job.id, deleteFiles: true)
                dismiss()
            }
            Button("Remove, keep files") {
                model.remove(job.id, deleteFiles: false)
                dismiss()
            }
        }
    }
}

struct FileRow: View {
    @Environment(AppModel.self) private var model
    let file: URL
    let jobID: UUID?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Image(systemName: icon)
                Text(file.lastPathComponent).lineLimit(2)
                Spacer()
                if let size {
                    Text(size).font(.caption).foregroundStyle(.secondary)
                }
            }
            HStack(spacing: 16) {
                ShareLink(item: file) { Label("Share", systemImage: "square.and.arrow.up") }
                Button { FilesApp.reveal(file) } label: { Label("Files", systemImage: "folder") }
                if !file.hasDirectoryPath {
                    if model.isUploading(file) {
                        ProgressView()
                    } else {
                        Button { model.upload(file, jobID: jobID) } label: { Label("Upload", systemImage: "icloud.and.arrow.up") }
                    }
                }
            }
            .labelStyle(.iconOnly)
            .buttonStyle(.borderless)
        }
    }

    private var icon: String {
        if file.hasDirectoryPath { return "folder" }
        switch file.pathExtension.lowercased() {
        case "epub": return "book"
        case "pdf": return "doc.richtext"
        case "txt": return "doc.plaintext"
        default: return "doc"
        }
    }

    private var size: String? {
        guard let bytes = try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize else { return nil }
        return ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
    }
}

enum FilesApp {
    /// Opens the Files app at the item (the app's Documents folder is shared with Files).
    static func reveal(_ file: URL) {
        var components = URLComponents(url: file, resolvingAgainstBaseURL: false)
        components?.scheme = "shareddocuments"
        if let target = components?.url {
            UIApplication.shared.open(target)
        }
    }
}
