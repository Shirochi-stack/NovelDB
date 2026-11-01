import NpiaCore
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// The log of every job in the queue, in time order. Each line shows its time, the job and its
/// level; a menu limits it to one job, "Details" adds the debug lines, and the view follows new
/// lines until the user scrolls up.
struct QueueLogView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("queueLogDetails") private var showDetails = false
    /// True until the user scrolls up; back to true when the end comes into view again.
    @State private var following = true
    @State private var confirmClear = false

    private static let tailID = "queue-log-tail"

    var body: some View {
        @Bindable var model = model
        // Reading the revision makes this view redraw as lines arrive.
        let _ = model.logRevision
        let entries = model.logBuffer.entries(jobID: model.logFilterJobID, includeDetails: showDetails)
        VStack(spacing: 0) {
            filterBar(jobs: filterJobs(), selection: $model.logFilterJobID)
            Divider()
            if entries.isEmpty {
                ContentUnavailableView(
                    "No log lines",
                    systemImage: "text.alignleft",
                    description: Text(showDetails ? "Lines appear here as downloads and uploads run." : "Lines appear here as downloads and uploads run. Turn on Details to include debug lines.")
                )
            } else {
                logList(entries)
            }
        }
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button {
                    UIPasteboard.general.string = LogBuffer.text(for: entries)
                } label: {
                    Label("Copy log", systemImage: "doc.on.doc")
                }
                .disabled(entries.isEmpty)
                ShareLink(item: LogExport(entries: entries), preview: SharePreview("Queue log")) {
                    Label("Share log", systemImage: "square.and.arrow.up")
                }
                .disabled(entries.isEmpty)
                Button(role: .destructive) {
                    confirmClear = true
                } label: {
                    Label("Clear log", systemImage: "trash")
                }
                .disabled(model.logBuffer.count == 0)
            }
        }
        .confirmationDialog("Clear the queue log?", isPresented: $confirmClear, titleVisibility: .visible) {
            Button("Clear log", role: .destructive) { model.clearQueueLog() }
        } message: {
            Text("The lines of every job are removed from this list. Each job keeps its own log.")
        }
    }

    // MARK: - Filter

    /// The jobs that have lines, plus the one the filter is on.
    private func filterJobs() -> [(id: UUID, title: String)] {
        var jobs = model.logBuffer.jobs()
        if let selected = model.logFilterJobID, !jobs.contains(where: { $0.id == selected }) {
            jobs.insert((selected, model.job(selected)?.displayTitle ?? "Job"), at: 0)
        }
        return jobs
    }

    private func filterBar(jobs: [(id: UUID, title: String)], selection: Binding<UUID?>) -> some View {
        HStack {
            Picker("Job", selection: selection) {
                Text("All jobs").tag(UUID?.none)
                ForEach(jobs.indices, id: \.self) { index in
                    Text(LogEntry.shortTitle(jobs[index].title, limit: 36)).tag(UUID?.some(jobs[index].id))
                }
            }
            .pickerStyle(.menu)
            Spacer()
            Toggle("Details", isOn: $showDetails)
                .toggleStyle(.switch)
                .fixedSize()
        }
        .padding(.horizontal)
        .padding(.vertical, 4)
    }

    // MARK: - Lines

    private func logList(_ entries: [LogEntry]) -> some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(entries) { entry in
                        LogRow(entry: entry, showJob: model.logFilterJobID == nil)
                        Divider().opacity(0.4)
                    }
                    Color.clear
                        .frame(height: 1)
                        .id(Self.tailID)
                        .onAppear { following = true }
                }
                .padding(.horizontal)
            }
            // Dragging the list down (reading older lines) stops the automatic scrolling.
            .simultaneousGesture(
                DragGesture(minimumDistance: 12).onChanged { drag in
                    if drag.translation.height > 12 { following = false }
                }
            )
            .overlay(alignment: .bottomTrailing) {
                if !following {
                    Button {
                        following = true
                        withAnimation { proxy.scrollTo(Self.tailID, anchor: .bottom) }
                    } label: {
                        Label("Latest", systemImage: "arrow.down.to.line")
                            .font(.footnote.weight(.semibold))
                            .padding(.horizontal, 12)
                            .padding(.vertical, 8)
                            .background(.regularMaterial, in: Capsule())
                    }
                    .padding()
                }
            }
            .onAppear {
                proxy.scrollTo(Self.tailID, anchor: .bottom)
            }
            .onChange(of: entries.last?.id) { _, _ in
                if following { proxy.scrollTo(Self.tailID, anchor: .bottom) }
            }
        }
    }
}

private struct LogRow: View {
    let entry: LogEntry
    let showJob: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text(entry.date, format: .dateTime.hour().minute().second())
                    .monospacedDigit()
                Text(entry.level.rawValue.uppercased())
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(levelColor)
                if showJob, !entry.shortTitle.isEmpty {
                    Text(entry.shortTitle)
                        .lineLimit(1)
                }
            }
            .font(.caption2)
            .foregroundStyle(.secondary)
            Text(entry.message)
                .font(.caption2)
                .foregroundStyle(entry.level == .debug ? Color.secondary : Color.primary)
                .textSelection(.enabled)
        }
        .padding(.vertical, 5)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var levelColor: Color {
        switch entry.level {
        case .debug: return .gray
        case .info: return .blue
        case .warning: return .orange
        case .error: return .red
        }
    }
}

/// The log as a plain-text file for the share sheet; the text is built only when the user shares.
private struct LogExport: Transferable {
    let entries: [LogEntry]

    static var transferRepresentation: some TransferRepresentation {
        DataRepresentation(exportedContentType: .plainText) { export in
            Data(LogBuffer.text(for: export.entries).utf8)
        }
    }
}
