import NpiaCore
import NpiaSources
import SwiftUI
import UniformTypeIdentifiers

struct SettingsView: View {
    var body: some View {
        List {
            Section("Downloads") {
                NavigationLink { SharedSettingsForm() } label: { Label("General", systemImage: "slider.horizontal.3") }
                NavigationLink { SourceOverridesList() } label: { Label("Per-source overrides", systemImage: "square.stack.3d.up") }
                NavigationLink { SourceOptionsForm() } label: { Label("Source options", systemImage: "puzzlepiece") }
                NavigationLink { NpiaSettingsForm() } label: { Label("npia", systemImage: "person.crop.circle") }
            }
            Section("Output") {
                NavigationLink { ImageSettingsForm() } label: { Label("Images", systemImage: "photo") }
                NavigationLink { TextSettingsForm() } label: { Label("Text and PDF", systemImage: "doc.text") }
            }
            Section("Sharing") {
                NavigationLink { UploadSettingsForm() } label: { Label("Upload", systemImage: "icloud.and.arrow.up") }
            }
            Section("App") {
                NavigationLink { AppearanceSettingsForm() } label: { Label("Appearance", systemImage: "paintpalette") }
                NavigationLink { CatalogSiteForm() } label: { Label("Catalog site", systemImage: "books.vertical") }
                NavigationLink { BackgroundSettingsForm() } label: { Label("Screen and background", systemImage: "moon.zzz") }
                NavigationLink { ConfigTransferView() } label: { Label("Import / export", systemImage: "arrow.up.arrow.down") }
            }
        }
        .themedBackground()
        .navigationTitle("Settings")
    }
}

// MARK: - General

struct SharedSettingsForm: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Form {
            Section("Output") {
                FormatPicker(formats: $model.settings.shared.formats)
                Picker("File names", selection: $model.settings.shared.namingMode) {
                    Text("Title").tag(NamingMode.title)
                    Text("Book ID").tag(NamingMode.id)
                }
                Toggle("Append chapter range to title", isOn: $model.settings.shared.appendRange)
                Toggle("Separate TXT files", isOn: $model.settings.shared.separateTXT)
                Toggle("Number chapters", isOn: $model.settings.shared.numberChapters)
            }
            Section("Chapters") {
                OptionalNumberField(title: "From chapter", value: $model.settings.shared.rangeFrom, defaultValue: 1)
                OptionalNumberField(title: "To chapter", value: $model.settings.shared.rangeTo, defaultValue: model.settings.shared.rangeFrom ?? 1)
                Toggle("Skip paid", isOn: $model.settings.shared.skipPaid)
                Toggle("Generate on stop", isOn: $model.settings.shared.generateOnStop)
            }
            Section("Speed (external downloader)") {
                Stepper("Threads: \(model.settings.shared.threads)", value: $model.settings.shared.threads, in: 1...16)
                Stepper("Image Workers: \(model.settings.shared.imageWorkers)", value: $model.settings.shared.imageWorkers, in: 1...16)
                DecimalField(title: "Rate Limiting Min (s)", value: $model.settings.shared.intervalMin)
                DecimalField(title: "Rate Limiting Max (s)", value: $model.settings.shared.intervalMax)
                Stepper("Retries: \(model.settings.shared.maxRetries)", value: $model.settings.shared.maxRetries, in: 0...30)
                Stepper("Downloads at once: \(model.maxConcurrentJobs)", value: $model.maxConcurrentJobs, in: 1...3)
            }
            Section("Cache") {
                Toggle("Use Cache", isOn: $model.settings.shared.useCache)
                Toggle("Cache Images", isOn: $model.settings.shared.cacheImages)
            }
        }
        .themedBackground()
        .navigationTitle("General")
    }
}

// MARK: - Per-source overrides

struct SourceOverridesList: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        List(SourceID.allCases, id: \.self) { source in
            NavigationLink {
                SourceOverrideForm(source: source)
            } label: {
                HStack {
                    Text(source.rawValue)
                    Spacer()
                    if let count = overrideCount(source), count > 0 {
                        Text("\(count) changed").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .themedBackground()
        .navigationTitle("Per-source overrides")
    }

    private func overrideCount(_ source: SourceID) -> Int? {
        guard let value = model.settings.overrides[source.rawValue] else { return nil }
        let fields: [Bool] = [
            value.threads != nil, value.intervalMin != nil, value.intervalMax != nil, value.formats != nil,
            value.rangeFrom != nil, value.rangeTo != nil, value.useCache != nil, value.cacheImages != nil,
            value.numberChapters != nil, value.separateTXT != nil,
        ]
        return fields.filter { $0 }.count
    }
}

struct SourceOverrideForm: View {
    @Environment(AppModel.self) private var model
    let source: SourceID

    private var overrides: Binding<SharedOverrides> {
        Binding(
            get: { model.settings.overrides[source.rawValue] ?? SharedOverrides() },
            set: { model.settings.overrides[source.rawValue] = $0.isEmpty ? nil : $0 }
        )
    }

    var body: some View {
        let shared = model.settings.shared
        Form {
            Section {
                OverrideRow(title: "Formats", value: overrides.formats, shared: shared.formats, describe: { $0.map { $0.rawValue.uppercased() }.joined(separator: ", ") }) { binding in
                    FormatPicker(formats: binding)
                }
                OverrideRow(title: "Threads", value: overrides.threads, shared: shared.threads, describe: { "\($0)" }) { binding in
                    Stepper("\(binding.wrappedValue)", value: binding, in: 1...16)
                }
                OverrideRow(title: "Rate Limiting Min", value: overrides.intervalMin, shared: shared.intervalMin, describe: { "\($0) s" }) { binding in
                    DecimalField(title: "Seconds", value: binding)
                }
                OverrideRow(title: "Rate Limiting Max", value: overrides.intervalMax, shared: shared.intervalMax, describe: { "\($0) s" }) { binding in
                    DecimalField(title: "Seconds", value: binding)
                }
                OverrideRow(title: "From chapter", value: overrides.rangeFrom, shared: shared.rangeFrom ?? 1, describe: { "\($0)" }) { binding in
                    Stepper("\(binding.wrappedValue)", value: binding, in: 1...100_000)
                }
                OverrideRow(title: "To chapter", value: overrides.rangeTo, shared: shared.rangeTo ?? 1, describe: { "\($0)" }) { binding in
                    Stepper("\(binding.wrappedValue)", value: binding, in: 1...100_000)
                }
                OverrideRow(title: "Use Cache", value: overrides.useCache, shared: shared.useCache, describe: onOff) { binding in
                    Toggle("Enabled", isOn: binding)
                }
                OverrideRow(title: "Cache Images", value: overrides.cacheImages, shared: shared.cacheImages, describe: onOff) { binding in
                    Toggle("Enabled", isOn: binding)
                }
                OverrideRow(title: "Number chapters", value: overrides.numberChapters, shared: shared.numberChapters, describe: onOff) { binding in
                    Toggle("Enabled", isOn: binding)
                }
                OverrideRow(title: "Separate TXT files", value: overrides.separateTXT, shared: shared.separateTXT, describe: onOff) { binding in
                    Toggle("Enabled", isOn: binding)
                }
            } footer: {
                Text("Switched-off rows follow General settings.")
            }
            if model.settings.overrides[source.rawValue] != nil {
                Section {
                    Button("Reset \(source.rawValue) to shared", role: .destructive) {
                        model.settings.overrides[source.rawValue] = nil
                    }
                }
            }
        }
        .themedBackground()
        .navigationTitle(source.rawValue)
    }

    private func onOff(_ value: Bool) -> String { value ? "on" : "off" }
}

// MARK: - Source options

struct SourceOptionsForm: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Form {
            Section("kpage") {
                Toggle("Skip last page", isOn: $model.settings.sources.kpage.skipLastPage)
                Toggle("Keep filler", isOn: $model.settings.sources.kpage.keepFiller)
                Toggle("Dedupe images", isOn: $model.settings.sources.kpage.dedupeImages)
                Stepper("Leading images to compare: \(model.settings.sources.kpage.dedupeLeadingImages)", value: $model.settings.sources.kpage.dedupeLeadingImages, in: 1...10)
                    .disabled(!model.settings.sources.kpage.dedupeImages)
            }
            Section("ntk") {
                Toggle("Prefer source cover", isOn: $model.settings.sources.ntkNpiaCover)
            }
            Section("nweb") {
                Toggle("Prefer store cover", isOn: $model.settings.sources.nwebStoreCover)
            }
            Section("kobo") {
                Toggle("Horizontal Japanese text (kobo)", isOn: $model.settings.sources.koboHorizontalLayout)
            }
        }
        .themedBackground()
        .navigationTitle("Source options")
    }
}

// MARK: - npia

struct NpiaSettingsForm: View {
    @Environment(AppModel.self) private var model
    @State private var email = ""
    @State private var password = ""
    @State private var hasPassword = false
    @State private var hasLoginKey = false
    @State private var mappedCharacters = 0
    @State private var importingMapping = false
    @State private var mappingError: String?

    var body: some View {
        @Bindable var model = model
        Form {
            Section("Downloads") {
                Toggle("Download Author Notices", isOn: $model.settings.npia.includeNotices)
                Toggle("Browser fallback", isOn: $model.settings.npia.useBrowserFallback)
                Toggle("Auto-login on startup", isOn: $model.settings.npia.autoLogin)
            }
            Section {
                LabeledContent("Mapping", value: mappedCharacters > 0 ? "\(mappedCharacters) characters mapped" : "None")
                Button("Import mapping JSON") { importingMapping = true }
                if mappedCharacters > 0 {
                    Button("Clear", role: .destructive) {
                        FontMapping.clear()
                        reloadMapping()
                    }
                }
                if let mappingError {
                    Text(mappingError).font(.caption).foregroundStyle(.red)
                }
            } header: {
                Text("Font Mapping")
            } footer: {
                Text("The desktop font mapping file (a JSON object of character replacements). It is applied to npia chapter text and kept on this device only.")
            }
            .fileImporter(isPresented: $importingMapping, allowedContentTypes: [.json, .plainText, .data]) { result in
                importMapping(result)
            }
            Section {
                TextField("Email", text: $email)
                    .textContentType(.username)
                    .keyboardType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                SecureField(hasPassword ? "Password (saved)" : "Password", text: $password)
                    .textContentType(.password)
                Button("Save to Keychain") {
                    model.secrets.set(email.trimmingCharacters(in: .whitespaces), for: .npiaEmail)
                    if !password.isEmpty { model.secrets.set(password, for: .npiaPassword) }
                    password = ""
                    reload()
                }
                if hasLoginKey {
                    Button("Forget saved session key", role: .destructive) {
                        model.secrets.set(nil, for: .npiaLoginKey)
                        reload()
                    }
                }
                if hasPassword || !email.isEmpty {
                    Button("Remove account", role: .destructive) {
                        model.secrets.set(nil, for: .npiaEmail)
                        model.secrets.set(nil, for: .npiaPassword)
                        model.secrets.set(nil, for: .npiaLoginKey)
                        reload()
                    }
                }
            } header: {
                Text("Account")
            } footer: {
                Text("Stored only in the iOS Keychain, never in settings files or exports.")
            }
            Section("Tag search") {
                Picker("Age filter", selection: $model.settings.npia.tagAgeFilter) {
                    Text("All").tag("")
                    Text("Non-adult").tag("15")
                    Text("Adult").tag("19")
                }
                TextField("Custom tags", text: $model.settings.npia.tagCustomTags)
                Toggle("Scrape all tags", isOn: $model.settings.npia.tagScrapeAll)
                Stepper("Max queries: \(model.settings.npia.scrapeMaxQueries)", value: $model.settings.npia.scrapeMaxQueries, in: 1...1000, step: 5)
            }
        }
        .themedBackground()
        .navigationTitle("npia")
        .onAppear {
            reload()
            reloadMapping()
        }
    }

    private func reload() {
        email = model.secrets.string(for: .npiaEmail) ?? ""
        hasPassword = model.secrets.string(for: .npiaPassword) != nil
        hasLoginKey = model.secrets.string(for: .npiaLoginKey) != nil
    }

    private func reloadMapping() {
        mappedCharacters = FontMapping.load()?.count ?? 0
    }

    private func importMapping(_ result: Result<URL, Error>) {
        mappingError = nil
        do {
            let url = try result.get()
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            try FontMapping.importFile(url)
        } catch {
            mappingError = "Could not import: " + error.localizedDescription
        }
        reloadMapping()
    }
}

// MARK: - Images

struct ImageSettingsForm: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Form {
            Section("Chapter images") {
                Toggle("Save Image URLs Only", isOn: $model.settings.images.saveImageURLsOnly)
                Toggle("Compress Images", isOn: $model.settings.images.compress)
                Group {
                    Picker("Format", selection: $model.settings.images.format) {
                        ForEach(ImageFormat.allCases, id: \.self) { Text($0.label).tag($0) }
                    }
                    Stepper("Quality: \(model.settings.images.quality)", value: $model.settings.images.quality, in: 10...100, step: 5)
                    Toggle("Convert GIFs", isOn: $model.settings.images.convertGIFs)
                    Toggle("Static images only", isOn: $model.settings.images.staticOnly)
                    Stepper("Image Compression Workers: \(model.settings.images.compressionWorkers)", value: $model.settings.images.compressionWorkers, in: 1...8)
                }
                .disabled(!model.settings.images.compress || model.settings.images.saveImageURLsOnly)
            }
            Section("Cover") {
                Toggle("Compress Cover", isOn: $model.settings.images.compressCover)
                Group {
                    Picker("Cover format", selection: $model.settings.images.coverFormat) {
                        ForEach(ImageFormat.allCases, id: \.self) { Text($0.label).tag($0) }
                    }
                    Stepper("Cover quality: \(model.settings.images.coverQuality)", value: $model.settings.images.coverQuality, in: 10...100, step: 5)
                }
                .disabled(!model.settings.images.compressCover)
            }
            Section {
                OptionalNumberField(title: "Shrink EPUB/PDF to target size (MB)", value: $model.settings.images.recompressTargetMB, defaultValue: 20)
                Toggle("Long image layout", isOn: $model.settings.images.longImageLayout)
                Toggle("ZIP Compress", isOn: $model.settings.images.zipCompressImages)
            } header: {
                Text("Book")
            } footer: {
                Text("The size target re-encodes JPEG and WebP images at lower quality, up to four passes. Long image layout shows webtoon strips edge to edge without chapter titles.")
            }
        }
        .themedBackground()
        .navigationTitle("Images")
    }
}

// MARK: - Text and PDF

struct TextSettingsForm: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Form {
            Section("Text") {
                Toggle("Remove Leading Spaces", isOn: $model.settings.text.stripLeadingSpaces)
                Toggle("Remove Newlines / <br>", isOn: $model.settings.text.removeNewlines)
                Toggle("Blank line after each paragraph", isOn: $model.settings.text.blankParagraphLines)
                Toggle("Save as HTML (instead of TXT)", isOn: $model.settings.text.saveHTML)
            }
            Section("PDF") {
                Toggle("Table of Contents", isOn: $model.settings.text.pdfTOC)
                Toggle("TOC Page Numbers", isOn: $model.settings.text.pdfCounterLayout)
                    .disabled(!model.settings.text.pdfTOC)
                Toggle("Page Numbers", isOn: $model.settings.text.pdfPageNumbers)
            }
        }
        .themedBackground()
        .navigationTitle("Text and PDF")
    }
}

// MARK: - Catalog site

struct CatalogSiteForm: View {
    @AppStorage(CatalogSite.storageKey) private var site = CatalogSite.defaultAddress
    @State private var address = ""

    var body: some View {
        Form {
            Section {
                TextField("Catalog site", text: $address)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .submitLabel(.done)
                    .onSubmit(save)
                Button("Save", action: save)
                    .disabled(address.trimmingCharacters(in: .whitespaces).isEmpty || address == site)
                Button("Use NovelDB") {
                    site = CatalogSite.defaultAddress
                    address = site
                }
                .disabled(site == CatalogSite.defaultAddress)
            } footer: {
                Text("On the NovelDB website, tapping a card opens View Details and the link-cart button on each card adds the book to the Add tab. Other catalog sites have no cart buttons, so tapping a link that leaves the site adds that link to the cart instead of opening it.")
            }
        }
        .themedBackground()
        .navigationTitle("Catalog site")
        .onAppear { address = site }
    }

    private func save() {
        var text = address.trimmingCharacters(in: .whitespaces)
        if !text.contains("://") { text = "https://" + text }
        guard let url = URL(string: text), url.host != nil else { return }
        site = url.absoluteString
        address = site
    }
}

// MARK: - Appearance

struct AppearanceSettingsForm: View {
    @AppStorage(ThemeMode.storageKey) private var theme = ThemeMode.system.rawValue
    @AppStorage(Accent.storageKey) private var accent = Accent.halgakosRose.rawValue

    var body: some View {
        Form {
            Section {
                FlowLayout(spacing: 8) {
                    ForEach(ThemeMode.allCases) { mode in
                        ChipToggle(title: mode.label, isOn: Binding(get: { theme == mode.rawValue }, set: { if $0 { theme = mode.rawValue } }))
                    }
                }
            } header: {
                Text("Theme")
            } footer: {
                Text("AMOLED is the dark theme with pure-black backgrounds.")
            }
            Section {
                ForEach(Accent.allCases) { option in
                    Button {
                        accent = option.rawValue
                    } label: {
                        HStack(spacing: 10) {
                            Circle().fill(option.seed).frame(width: 16, height: 16)
                            Text(option.label).foregroundStyle(Color.primary)
                            Spacer()
                            if accent == option.rawValue {
                                Image(systemName: "checkmark").foregroundStyle(Color.accentColor)
                            }
                        }
                    }
                }
            } header: {
                Text("Accent")
            } footer: {
                Text("Halgakos Rose is the Glossarion theme.")
            }
        }
        .themedBackground()
        .navigationTitle("Appearance")
    }
}

// MARK: - Screen and background

struct BackgroundSettingsForm: View {
    @AppStorage(BackgroundActivity.keepAwakeKey) private var keepAwake = true
    @AppStorage(BackgroundActivity.continuedProcessingKey) private var continueInBackground = true
    @Environment(AppModel.self) private var model

    var body: some View {
        Form {
            Section {
                Toggle("Keep screen awake while downloading", isOn: $keepAwake)
            } footer: {
                Text("iOS pauses apps soon after the screen locks; keeping it awake is the most reliable way to finish long downloads.")
            }
            Section {
                Toggle("Continue in background", isOn: $continueInBackground)
                    .disabled(!BackgroundActivity.supportsContinuedProcessing)
            } footer: {
                if BackgroundActivity.supportsContinuedProcessing {
                    Text("Downloads keep running with a system progress indicator after you leave the app. iOS may still stop them under heavy load.")
                } else {
                    Text("Needs iOS 26. On earlier versions downloads get a short grace period after you leave the app.")
                }
            }
        }
        .themedBackground()
        .navigationTitle("Screen and background")
        .onChange(of: keepAwake) { model.background.settingsChanged() }
    }
}
