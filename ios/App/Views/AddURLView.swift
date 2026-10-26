import NpiaCore
import NpiaSources
import SwiftUI

/// One entry point for every source: paste URLs, see which source handles them, and adjust
/// only the options that matter for it before queueing.
struct AddURLView: View {
    @Environment(AppModel.self) private var model
    var onQueued: () -> Void = {}

    @State private var text = ""
    /// Working copy: shared values are the effective ones for the detected source.
    @State private var draft = AppSettings()
    @State private var shared = SharedSettings()
    @State private var remember = false
    @State private var showMore = false

    private var urls: [URL] { SourceInfo.urls(in: text) }
    private var supported: [URL] { urls.filter { SourceCatalog.adapterType(for: $0) != nil } }
    private var source: SourceID? { supported.first.flatMap { SourceCatalog.adapterType(for: $0)?.id } }

    var body: some View {
        Form {
            Section {
                TextField("Novel URL (one per line)", text: $text, axis: .vertical)
                    .lineLimit(1...6)
                    .textInputAutocapitalization(.never)
                    .keyboardType(.URL)
                    .autocorrectionDisabled()
                HStack {
                    PasteButton(payloadType: String.self) { strings in
                        guard let first = strings.first else { return }
                        Task { @MainActor in
                            text = text.isEmpty ? first : text + "\n" + first
                        }
                    }
                    .labelStyle(.titleAndIcon)
                    Spacer()
                    if !text.isEmpty {
                        Button("Clear", role: .destructive) { text = "" }
                    }
                }
            } footer: {
                detectionSummary
            }

            if let source {
                Section("Output") {
                    FormatPicker(formats: $shared.formats)
                    if shared.formats.contains(.txt) {
                        Toggle("One TXT file per chapter", isOn: $shared.separateTXT)
                    }
                }

                Section("Chapters") {
                    OptionalNumberField(title: "From chapter", value: $shared.rangeFrom, defaultValue: 1)
                    OptionalNumberField(title: "To chapter", value: $shared.rangeTo, defaultValue: shared.rangeFrom ?? 1)
                    Toggle("Skip paid chapters", isOn: $shared.skipPaid)
                    Toggle("Number chapter titles", isOn: $shared.numberChapters)
                }

                sourceSections(source)

                Section {
                    DisclosureGroup("Speed and images", isExpanded: $showMore) {
                        Stepper("Chapter threads: \(shared.threads)", value: $shared.threads, in: 1...16)
                        Stepper("Image downloads: \(shared.imageWorkers)", value: $shared.imageWorkers, in: 1...16)
                        DecimalField(title: "Delay min (s)", value: $shared.intervalMin)
                        DecimalField(title: "Delay max (s)", value: $shared.intervalMax)
                        Toggle("Use chapter cache", isOn: $shared.useCache)
                        Toggle("Compress images", isOn: $draft.images.compress)
                        if draft.images.compress {
                            Picker("Image format", selection: $draft.images.format) {
                                ForEach(ImageFormat.allCases, id: \.self) { Text($0.label).tag($0) }
                            }
                        }
                    }
                }

                Section {
                    Toggle("Save as defaults for \(source.rawValue)", isOn: $remember)
                    Button {
                        queue()
                    } label: {
                        Label(supported.count > 1 ? "Download \(supported.count) books" : "Download", systemImage: "arrow.down.circle.fill")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                } footer: {
                    Text("Defaults are stored as per-source overrides; skip-paid and image choices apply to this download only.")
                }
            }
        }
        .navigationTitle("Add")
        .onAppear {
            resetDraft()
            takePendingText()
        }
        .onChange(of: source) { resetDraft() }
        .onChange(of: model.pendingAddText) { takePendingText() }
    }

    private func takePendingText() {
        guard let pending = model.pendingAddText else { return }
        model.pendingAddText = nil
        text = text.isEmpty ? pending : text + "\n" + pending
    }

    @ViewBuilder
    private var detectionSummary: some View {
        if urls.isEmpty {
            Text("Paste a book page or chapter list URL. The source is detected automatically.")
        } else {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(urls, id: \.self) { url in
                    if let type = SourceCatalog.adapterType(for: url) {
                        Label("\(type.id.rawValue): \(SourceInfo.tierLabel(type.id))", systemImage: "checkmark.circle")
                    } else {
                        Label("Not supported: \(url.host ?? url.absoluteString)", systemImage: "xmark.circle")
                            .foregroundStyle(.red)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func sourceSections(_ source: SourceID) -> some View {
        let groups = SourceInfo.groups(for: source)
        if !groups.isEmpty {
            Section("\(source.rawValue) options") {
                ForEach(groups, id: \.self) { group in
                    switch group {
                    case .npia:
                        Toggle("Include notices", isOn: $draft.npia.includeNotices)
                        Toggle("Browser fallback", isOn: $draft.npia.useBrowserFallback)
                    case .kpage:
                        Toggle("Skip last page", isOn: $draft.sources.kpage.skipLastPage)
                        Toggle("Keep filler pages", isOn: $draft.sources.kpage.keepFiller)
                        Toggle("Remove duplicate images", isOn: $draft.sources.kpage.dedupeImages)
                        if draft.sources.kpage.dedupeImages {
                            Stepper("Leading images to compare: \(draft.sources.kpage.dedupeLeadingImages)", value: $draft.sources.kpage.dedupeLeadingImages, in: 1...10)
                        }
                    case .ntkCover:
                        Toggle("Use npia cover when available", isOn: $draft.sources.ntkNpiaCover)
                    case .nwebCover:
                        Toggle("Prefer store cover", isOn: $draft.sources.nwebStoreCover)
                    case .koboLayout:
                        Toggle("Horizontal layout", isOn: $draft.sources.koboHorizontalLayout)
                    case .longImages:
                        Toggle("Long image layout", isOn: $draft.images.longImageLayout)
                    }
                }
            }
        }
    }

    private func resetDraft() {
        draft = model.settings
        shared = source.map { model.settings.effective(for: $0) } ?? model.settings.shared
    }

    private func queue() {
        let draft = draft
        let shared = shared
        model.enqueue(supported) { jobSource in
            var settings = draft
            settings.shared.skipPaid = shared.skipPaid
            settings.shared.imageWorkers = shared.imageWorkers
            if let jobSource {
                let difference = SharedOverrides.difference(shared, from: settings.shared)
                settings.overrides[jobSource.rawValue] = difference.isEmpty ? nil : difference
            }
            return settings
        }
        if remember, let source {
            var saved = model.settings
            let difference = SharedOverrides.difference(shared, from: saved.shared)
            saved.overrides[source.rawValue] = difference.isEmpty ? nil : difference
            saved.npia = draft.npia
            saved.sources = draft.sources
            saved.images.longImageLayout = draft.images.longImageLayout
            model.settings = saved
        }
        text = ""
        remember = false
        onQueued()
    }
}
