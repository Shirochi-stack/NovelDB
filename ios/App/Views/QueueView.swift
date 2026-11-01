import NpiaCore
import NpiaUpload
import SwiftUI
import UIKit

/// The two halves of the Queue tab.
enum QueueSection: String, CaseIterable, Identifiable {
    case jobs, log

    var id: String { rawValue }

    var title: String {
        switch self {
        case .jobs: return "Jobs"
        case .log: return "Log"
        }
    }
}

struct QueueView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmStopAll = false
    /// Selection mode: a checkbox on every finished job, with Share and Upload at the bottom.
    @State private var selecting = false
    @State private var selected: Set<UUID> = []

    var body: some View {
        @Bindable var model = model
        VStack(spacing: 0) {
            Picker("Show", selection: $model.queueSection) {
                ForEach(QueueSection.allCases) { section in
                    Text(section.title).tag(section)
                }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal)
            .padding(.vertical, 8)

            switch model.queueSection {
            case .jobs:
                jobsList
            case .log:
                QueueLogView()
            }
        }
        .themedBackground()
        .navigationTitle("Queue")
        .navigationDestination(for: UUID.self) { id in
            JobDetailView(jobID: id)
        }
        .toolbar { toolbarContent }
        .confirmationDialog("Stop \(model.activeJobCount) downloads?", isPresented: $confirmStopAll, titleVisibility: .visible) {
            Button("Stop all", role: .destructive) { model.cancelAll() }
        } message: {
            Text("Running downloads are cancelled and queued ones will not start.")
        }
        .onChange(of: model.queueSection) { _, section in
            if section == .log { endSelection() }
        }
    }

    // MARK: - Toolbar

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            NavigationLink { UploadHistoryView() } label: { Label("Uploads", systemImage: "icloud.and.arrow.up") }
        }
        if model.queueSection == .jobs {
            if model.jobs.contains(where: { model.isUploadable($0) }) {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(selecting ? "Done" : "Select") {
                        if selecting { endSelection() } else { selecting = true }
                    }
                }
            }
            if !selecting, model.jobs.contains(where: { !$0.state.isActive }) {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Clear finished") { model.clearFinished() }
                }
            }
            if !selecting, model.activeJobCount > 0 {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Stop all", role: .destructive) {
                        if model.activeJobCount > 1 {
                            confirmStopAll = true
                        } else {
                            model.cancelAll()
                        }
                    }
                }
            }
        }
    }

    // MARK: - Jobs

    @ViewBuilder
    private var jobsList: some View {
        if selecting {
            List(selection: $selected) {
                rows
            }
            .environment(\.editMode, .constant(.active))
            .safeAreaInset(edge: .bottom, spacing: 0) { selectionBar }
        } else {
            List {
                rows
            }
        }
    }

    @ViewBuilder
    private var rows: some View {
        if model.jobs.isEmpty {
            ContentUnavailableView("No downloads yet", systemImage: "arrow.down.circle", description: Text("Add a URL to start."))
        }
        ForEach(model.jobs) { job in
            NavigationLink(value: job.id) {
                JobRow(job: job)
            }
            .selectionDisabled(!model.isUploadable(job))
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
            .swipeActions(edge: .leading) {
                if model.isUploadable(job) {
                    Button("Upload") { model.upload(jobs: [job.id]) }.tint(.indigo)
                }
            }
            .contextMenu {
                if job.state.isActive {
                    Button("Cancel", systemImage: "xmark.circle") { model.cancel(job.id) }
                } else {
                    Button("Retry", systemImage: "arrow.clockwise") { model.retry(job.id) }
                }
                let files = model.existingFiles(of: job)
                if !files.isEmpty {
                    ShareLink(items: files) { Label("Share files", systemImage: "square.and.arrow.up") }
                    Button("Upload", systemImage: "icloud.and.arrow.up") { model.upload(jobs: [job.id]) }
                    Button("Show in Files", systemImage: "folder") { FilesApp.reveal(files[0]) }
                    Button("Select", systemImage: "checkmark.circle") {
                        selected = [job.id]
                        selecting = true
                    }
                }
                Button("Show log", systemImage: "text.alignleft") { model.showLog(for: job.id) }
            }
        }
    }

    // MARK: - Selection

    private var selectableJobs: [JobItem] {
        model.jobs.filter { model.isUploadable($0) }
    }

    private func endSelection() {
        selecting = false
        selected = []
    }

    private var selectionBar: some View {
        let chosen = selectableJobs.filter { selected.contains($0.id) }
        let files = chosen.flatMap { model.existingFiles(of: $0) }
        return VStack(spacing: 8) {
            HStack {
                Text(chosen.count == 1 ? "1 selected" : "\(chosen.count) selected")
                    .font(.subheadline.weight(.semibold))
                Spacer()
                Button("Select all finished") {
                    selected = Set(selectableJobs.map(\.id))
                }
                .font(.subheadline)
            }
            HStack(spacing: 12) {
                ShareLink(items: files) {
                    Label("Share", systemImage: "square.and.arrow.up").frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .disabled(files.isEmpty)
                Button {
                    model.upload(jobs: Set(chosen.map(\.id)))
                    endSelection()
                } label: {
                    Label("Upload", systemImage: "icloud.and.arrow.up").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .disabled(files.isEmpty)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 10)
        .background(.bar)
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
                    if model.isUploadable(job) {
                        Button { model.upload(jobs: [job.id]) } label: {
                            Label("Upload all", systemImage: "icloud.and.arrow.up")
                        }
                    }
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
                    Button { model.showLog(for: job.id) } label: {
                        Label("Open in the Queue log", systemImage: "text.alignleft")
                    }
                    ForEach(Array(job.log.enumerated()), id: \.offset) { _, line in
                        Text(line).font(.caption2.monospaced()).textSelection(.enabled)
                    }
                }
            }

            if !job.state.isActive {
                Section {
                    Button("Remove from queue", role: .destructive) { confirmDelete = true }
                }
            }
        }
        .themedBackground()
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
                if !file.hasDirectoryPath, !model.isUploading(file) {
                    Button { model.upload(file, jobID: jobID) } label: { Label("Upload", systemImage: "icloud.and.arrow.up") }
                }
            }
            .labelStyle(.iconOnly)
            .buttonStyle(.borderless)
            if let activity = model.uploadActivity(for: file) {
                UploadActivityView(
                    activity: activity,
                    retry: { model.upload(activity.request) },
                    dismiss: { model.dismissUploadActivity(activity.id) }
                )
            }
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
