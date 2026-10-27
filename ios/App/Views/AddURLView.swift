import NpiaCore
import NpiaSources
import SwiftUI

/// One entry point for every source: paste URLs, check the detected source, and set the
/// download options on one compact screen. Every option is saved immediately, the way the
/// desktop External dialog saves its `ext_*` keys (npia edits the main window keys).
struct AddURLView: View {
    @Environment(AppModel.self) private var model
    var onQueued: () -> Void = {}

    @State private var text = ""

    private var urls: [URL] { SourceInfo.urls(in: text) }
    private var supported: [URL] { urls.filter { SourceCatalog.adapterType(for: $0) != nil } }
    private var source: SourceID? { supported.first.flatMap { SourceCatalog.adapterType(for: $0)?.id } }
    private var effective: SharedSettings { source.map { model.settings.effective(for: $0) } ?? model.settings.shared }

    var body: some View {
        Form {
            Section {
                TextField("Novel URL (one per line)", text: $text, axis: .vertical)
                    .lineLimit(1...4)
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
                    .controlSize(.small)
                    Spacer()
                    if !text.isEmpty {
                        Button("Clear", role: .destructive) { text = "" }
                            .controlSize(.small)
                    }
                }
            } footer: {
                detectionSummary
            }

            Section {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Format").font(.caption).foregroundStyle(.secondary)
                    FlowLayout(spacing: 8) {
                        ForEach(OutputFormat.allCases, id: \.self) { format in
                            ChipToggle(title: format.rawValue.uppercased(), isOn: formatBinding(format))
                        }
                    }
                    if effective.formats.contains(.txt) {
                        Toggle("Separate TXT files", isOn: overridable(\.separateTXT, \.separateTXT))
                            .font(.subheadline)
                    }
                }
                FlowLayout(spacing: 20) {
                    CompactStepper(title: "Threads", value: overridable(\.threads, \.threads), range: 1...16)
                    CompactStepper(title: "Image Workers", value: sharedOnly(\.imageWorkers), range: 1...16)
                }
                HStack(spacing: 8) {
                    Text("Rate Limiting").font(.subheadline)
                    CompactDecimal(title: "Min", value: overridable(\.intervalMin, \.intervalMin))
                    CompactDecimal(title: "Max", value: overridable(\.intervalMax, \.intervalMax))
                    Text("s").font(.caption).foregroundStyle(.secondary)
                    Spacer(minLength: 0)
                }
                HStack(spacing: 8) {
                    Text("Chapter Range").font(.subheadline)
                    CompactNumber(title: "From", value: rangeBinding(\.rangeFrom, \.rangeFrom))
                    CompactNumber(title: "To", value: rangeBinding(\.rangeTo, \.rangeTo))
                    Spacer(minLength: 0)
                }
                FlowLayout(spacing: 8) {
                    ChipToggle(title: "Skip paid", isOn: sharedOnly(\.skipPaid))
                    ChipToggle(title: "Number chapters", isOn: overridable(\.numberChapters, \.numberChapters))
                    ChipToggle(title: "Generate on stop", isOn: sharedOnly(\.generateOnStop))
                    ChipToggle(title: "Use Cache", isOn: overridable(\.useCache, \.useCache))
                    ChipToggle(title: "Cache Images", isOn: overridable(\.cacheImages, \.cacheImages))
                }
            } header: {
                Text(source.map { "Download settings (\($0.rawValue))" } ?? "Download settings")
            }

            if let source {
                sourceSection(source)
            }

            Section {
                Button {
                    queue()
                } label: {
                    Label(supported.count > 1 ? "Download \(supported.count) books" : "Download", systemImage: "arrow.down.circle.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .disabled(supported.isEmpty)
            }
        }
        .themedBackground()
        .environment(\.defaultMinListRowHeight, 34)
        .scrollDismissesKeyboard(.interactively)
        .navigationTitle("Add")
        .onAppear { takePendingText() }
        .onChange(of: model.pendingAddText) { takePendingText() }
    }

    private func takePendingText() {
        guard let pending = model.pendingAddText else { return }
        model.pendingAddText = nil
        text = text.isEmpty ? pending : text + "\n" + pending
    }

    // MARK: Bindings that save straight into the app settings

    private func overridable<T>(_ sharedPath: WritableKeyPath<SharedSettings, T>, _ overridePath: WritableKeyPath<SharedOverrides, T?>) -> Binding<T> {
        let source = source
        return Binding(
            get: { effective[keyPath: sharedPath] },
            set: { model.settings.setShared(sharedPath, overridePath, $0, for: source) }
        )
    }

    private func sharedOnly<T>(_ path: WritableKeyPath<SharedSettings, T>) -> Binding<T> {
        Binding(get: { model.settings.shared[keyPath: path] }, set: { model.settings.shared[keyPath: path] = $0 })
    }

    private func rangeBinding(_ sharedPath: WritableKeyPath<SharedSettings, Int?>, _ overridePath: WritableKeyPath<SharedOverrides, Int?>) -> Binding<Int?> {
        let source = source
        return Binding(
            get: { effective[keyPath: sharedPath] },
            set: { model.settings.setRange(sharedPath, overridePath, $0, for: source) }
        )
    }

    /// At least one format stays selected; order follows `OutputFormat.allCases`.
    private func formatBinding(_ format: OutputFormat) -> Binding<Bool> {
        let formats = overridable(\.formats, \.formats)
        return Binding(
            get: { formats.wrappedValue.contains(format) },
            set: { on in
                let current = formats.wrappedValue
                if on {
                    formats.wrappedValue = OutputFormat.allCases.filter { current.contains($0) || $0 == format }
                } else if current.count > 1 {
                    formats.wrappedValue = current.filter { $0 != format }
                }
            }
        )
    }

    private func settingsBinding<T>(_ path: WritableKeyPath<AppSettings, T>) -> Binding<T> {
        Binding(get: { model.settings[keyPath: path] }, set: { model.settings[keyPath: path] = $0 })
    }

    // MARK: Sections

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
    private func sourceSection(_ source: SourceID) -> some View {
        let groups = SourceInfo.groups(for: source)
        if !groups.isEmpty {
            Section("\(source.rawValue) options") {
                FlowLayout(spacing: 8) {
                    ForEach(groups, id: \.self) { group in
                        switch group {
                        case .npia:
                            ChipToggle(title: "Download Author Notices", isOn: settingsBinding(\.npia.includeNotices))
                            ChipToggle(title: "Browser fallback", isOn: settingsBinding(\.npia.useBrowserFallback))
                        case .kpage:
                            ChipToggle(title: "Skip last page", isOn: settingsBinding(\.sources.kpage.skipLastPage))
                            ChipToggle(title: "Keep filler", isOn: settingsBinding(\.sources.kpage.keepFiller))
                            ChipToggle(title: "Dedupe images", isOn: settingsBinding(\.sources.kpage.dedupeImages))
                        case .ntkCover:
                            ChipToggle(title: "Prefer source cover", isOn: settingsBinding(\.sources.ntkNpiaCover))
                        case .nwebCover:
                            ChipToggle(title: "Store cover", isOn: settingsBinding(\.sources.nwebStoreCover))
                        case .koboLayout:
                            ChipToggle(title: "Horizontal Japanese text", isOn: settingsBinding(\.sources.koboHorizontalLayout))
                        case .longImages:
                            ChipToggle(title: "Long image layout", isOn: settingsBinding(\.images.longImageLayout))
                        }
                    }
                }
                if groups.contains(.kpage), model.settings.sources.kpage.dedupeImages {
                    CompactStepper(title: "Leading images to compare", value: settingsBinding(\.sources.kpage.dedupeLeadingImages), range: 1...10)
                }
            }
        }
    }

    private func queue() {
        let settings = model.settings
        model.enqueue(supported) { _ in settings }
        text = ""
        onQueued()
    }
}
