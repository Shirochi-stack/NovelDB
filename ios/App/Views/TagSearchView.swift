import NpiaCore
import NpiaSources
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// Plain text a search result is saved as.
struct TextFileDocument: FileDocument {
    static var readableContentTypes: [UTType] { [.plainText] }

    var text: String

    init(text: String) {
        self.text = text
    }

    init(configuration: ReadConfiguration) throws {
        text = String(decoding: configuration.file.regularFileContents ?? Data(), as: UTF8.self)
    }

    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        FileWrapper(regularFileWithContents: Data(text.utf8))
    }
}

/// npia tag search: pick tags or build a query, then retrieve novel ids, scrape the whole
/// catalog, or read the Top 100 lists. The run lives in `AppModel.tagSearch`, so it keeps
/// going when this screen is left.
struct TagSearchView: View {
    @Environment(AppModel.self) private var model
    @State private var exporting = false
    @State private var exportDocument = TextFileDocument(text: "")
    @State private var exportName = "tag_search"
    @State private var confirmCart = false

    /// Cart additions above this ask first.
    private static let cartConfirmLimit = 200
    /// Tags that adult-only filters cannot match.
    private static let adultTag = "19\u{ae08}"

    var body: some View {
        @Bindable var model = model
        let search = model.tagSearch
        let npia = model.settings.npia
        Form {
            Section {
                FlowLayout(spacing: 8) {
                    ForEach(NpiaSearchTerms.commonTags) { tag in
                        ChipToggle(title: tag.title, isOn: selectionBinding(tag.value))
                            .disabled(npia.tagScrapeAll || (tag.value == Self.adultTag && !npia.tagAgeFilter.isEmpty))
                    }
                }
                .padding(.vertical, 4)
                TextField("Custom tags (comma-separated)", text: $model.settings.npia.tagCustomTags)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .disabled(npia.tagScrapeAll)
            } header: {
                Text("Select tags to search for novels")
            }

            queryBuilder(npia)

            Section {
                Picker("Age", selection: $model.settings.npia.tagAgeFilter) {
                    Text("All").tag("")
                    Text("Non-adult only").tag("15")
                    Text("Adult only").tag("19")
                }
                Toggle("Scrape all novels", isOn: $model.settings.npia.tagScrapeAll)
                if npia.tagScrapeAll {
                    Stepper("Search queries: \(npia.scrapeMaxQueries)", value: $model.settings.npia.scrapeMaxQueries, in: 1...NpiaSettings.maxSearchQueries)
                }
            } header: {
                Text("Options")
            } footer: {
                if npia.tagScrapeAll {
                    Text("More queries = better coverage but slower. \(NpiaSettings.maxSearchQueries) = full current tag + sweep coverage. Requests are spaced by the npia rate limiting minimum and use its thread count.")
                } else {
                    Text("Requests are spaced by the npia rate limiting minimum and use its thread count.")
                }
            }

            runSection(search, npia)

            logSection(search)
        }
        .themedBackground()
        .navigationTitle("Tag search")
        .navigationBarTitleDisplayMode(.inline)
        .onChange(of: npia.tagAgeFilter) { _, age in
            if !age.isEmpty { model.settings.npia.tagSelectedTags.removeAll { $0 == Self.adultTag } }
        }
        .fileExporter(isPresented: $exporting, document: exportDocument, contentType: .plainText, defaultFilename: exportName) { result in
            if case .failure(let error) = result {
                model.message = "Could not save the list: \(error.localizedDescription)"
            }
        }
        .confirmationDialog(
            "Add \(search.outcome?.ids.count ?? 0) novels to the link cart?", isPresented: $confirmCart, titleVisibility: .visible
        ) {
            Button("Add to link cart") { addToCart() }
        } message: {
            Text("They are added as npia links and downloaded from the Add tab.")
        }
    }

    // MARK: - Query builder

    @ViewBuilder
    private func queryBuilder(_ npia: NpiaSettings) -> some View {
        Section {
            if npia.tagQueryGroups.isEmpty {
                Text("No groups defined. Select tags and tap Add Selected as Group.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            ForEach(Array(npia.tagQueryGroups.enumerated()), id: \.offset) { index, group in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(index == 0 ? "" : "AND")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.secondary)
                        .frame(width: 32, alignment: .leading)
                    Text("Group \(index + 1): " + group.map(NpiaSearchTerms.displayName(forTag:)).joined(separator: " OR "))
                        .font(.subheadline)
                    Spacer(minLength: 4)
                    Button {
                        guard model.settings.npia.tagQueryGroups.indices.contains(index) else { return }
                        model.settings.npia.tagQueryGroups.remove(at: index)
                    } label: {
                        Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel("Remove group \(index + 1)")
                }
            }
            HStack {
                Button("Add Selected as Group") { addGroup() }
                    .disabled(npia.tagScrapeAll)
                Spacer()
                Button("Clear All", role: .destructive) { model.settings.npia.tagQueryGroups = [] }
                    .disabled(npia.tagQueryGroups.isEmpty)
            }
            .buttonStyle(.borderless)
        } header: {
            Text("Query Builder")
        } footer: {
            Text("Groups are AND'd, tags within a group are OR'd. With no groups, every selected or custom tag must match.")
        }
    }

    private func selectionBinding(_ tag: String) -> Binding<Bool> {
        Binding(
            get: { model.settings.npia.tagSelectedTags.contains(tag) },
            set: { on in
                var tags = model.settings.npia.tagSelectedTags
                if on {
                    if !tags.contains(tag) { tags.append(tag) }
                } else {
                    tags.removeAll { $0 == tag }
                }
                model.settings.npia.tagSelectedTags = tags
            }
        )
    }

    private func addGroup() {
        let npia = model.settings.npia
        let group = NpiaSearchPlan.selection(selected: npia.tagSelectedTags, custom: npia.tagCustomTags)
        guard !group.isEmpty else {
            model.message = "Select at least one tag to add as a group."
            return
        }
        model.settings.npia.tagQueryGroups.append(group)
        model.settings.npia.tagSelectedTags = []
        model.settings.npia.tagCustomTags = ""
    }

    // MARK: - Run

    @ViewBuilder
    private func runSection(_ search: TagSearchController, _ npia: NpiaSettings) -> some View {
        Section {
            Button {
                if let problem = search.startSearch(settings: model.settings, secrets: model.secrets, background: model.background) {
                    model.message = problem
                }
            } label: {
                Label("Retrieve Novel IDs", systemImage: "magnifyingglass").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(search.isRunning)

            Button {
                search.startTop100(settings: model.settings, secrets: model.secrets, background: model.background)
            } label: {
                Label("Retrieve Top 100", systemImage: "list.number").frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .disabled(search.isRunning)

            Button(role: .destructive) {
                search.stop()
            } label: {
                Label("Stop", systemImage: "stop.circle").frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .disabled(!search.isRunning)

            if search.isRunning {
                HStack(spacing: 8) {
                    ProgressView()
                    Text(search.summary).font(.callout)
                }
            } else if !search.summary.isEmpty {
                Text(search.summary)
                    .font(.callout)
                    .textSelection(.enabled)
            }

            if let outcome = search.outcome, !search.isRunning {
                Button {
                    exportName = (outcome.fileName as NSString).deletingPathExtension
                    exportDocument = TextFileDocument(text: outcome.fileText)
                    exporting = true
                } label: {
                    Label("Save list", systemImage: "square.and.arrow.down")
                }
                Button {
                    if outcome.ids.count > Self.cartConfirmLimit {
                        confirmCart = true
                    } else {
                        addToCart()
                    }
                } label: {
                    Label("Add to link cart (\(outcome.ids.count))", systemImage: "cart.badge.plus")
                }
            }
        } header: {
            Text("Run")
        }
    }

    private func addToCart() {
        guard let outcome = model.tagSearch.outcome else { return }
        let added = model.cart.add(contentsOf: outcome.cartItems())
        let skipped = outcome.ids.count - added
        model.message = "Added \(added) novel(s) to the link cart" + (skipped > 0 ? " (\(skipped) already there)." : ".")
    }

    // MARK: - Log

    @ViewBuilder
    private func logSection(_ search: TagSearchController) -> some View {
        Section {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 1) {
                        if search.lines.isEmpty {
                            Text("The log appears here while a search runs.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        ForEach(search.lines) { line in
                            Text(line.text)
                                .font(.caption.monospaced())
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .id(line.id)
                        }
                    }
                    .textSelection(.enabled)
                }
                .frame(height: 240)
                .onChange(of: search.lines.last?.id) { _, id in
                    if let id { proxy.scrollTo(id, anchor: .bottom) }
                }
                .onAppear {
                    if let id = search.lines.last?.id { proxy.scrollTo(id, anchor: .bottom) }
                }
            }
            HStack {
                Button {
                    UIPasteboard.general.string = search.logText
                } label: {
                    Label("Copy log", systemImage: "doc.on.doc")
                }
                .disabled(search.lines.isEmpty)
                Spacer()
                Button("Clear", role: .destructive) { search.clearLog() }
                    .disabled(search.lines.isEmpty || search.isRunning)
            }
            .buttonStyle(.borderless)
        } header: {
            Text("Log")
        }
    }
}
