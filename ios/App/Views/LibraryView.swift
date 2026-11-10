import NpiaSources
import NpiaCore
import SwiftUI

/// A book in the Library: every output sharing a name ("Title.epub", "Title.txt", a "Title/" TXT folder).
struct LibraryBook: Identifiable, Sendable {
    let name: String
    let files: [URL]
    let modified: Date
    var id: String { name.lowercased() }
    var epub: URL? { files.first { $0.pathExtension.lowercased() == "epub" } }
    var readerFile: URL? { Self.readerFile(in: files) }

    static func readerFile(in files: [URL]) -> URL? {
        for ext in ["epub", "txt"] {
            if let file = files.first(where: {
                $0.pathExtension.lowercased() == ext && (try? $0.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true
            }) { return file }
        }
        return nil
    }

    /// "EPUB · TXT · 5.3 MB": the formats and size, for the shelf tile.
    var formats: String {
        let manager = FileManager.default
        var bytes = 0
        for file in files {
            if let inside = manager.enumerator(at: file, includingPropertiesForKeys: [.fileSizeKey]), (try? file.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true {
                for case let url as URL in inside { bytes += (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0 }
            } else {
                bytes += (try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
            }
        }
        let kinds = files.map(Self.kind)
        var seen: [String] = []
        for kind in kinds where !seen.contains(kind) { seen.append(kind) }
        return (seen + [ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)]).joined(separator: " · ")
    }

    static let extensions: Set<String> = ["epub", "pdf", "txt", "html", "htm", "cbz", "zip"]

    static func kind(_ file: URL) -> String {
        if file.hasDirectoryPath {
            return file.pathExtension.lowercased() == "folder" ? "Folder" : "TXT"
        }
        return file.pathExtension.uppercased()
    }

    /// The books in the app's download folder, newest first.
    static func scan(_ folder: URL) -> [LibraryBook] {
        let manager = FileManager.default
        let keys: [URLResourceKey] = [.isDirectoryKey, .contentModificationDateKey]
        let entries = (try? manager.contentsOfDirectory(at: folder, includingPropertiesForKeys: keys, options: [.skipsHiddenFiles])) ?? []
        var groups: [String: (name: String, files: [URL], modified: Date)] = [:]
        for url in entries {
            let isFolder = (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) ?? false
            if isFolder {
                let inside = (try? manager.contentsOfDirectory(at: url, includingPropertiesForKeys: nil)) ?? []
                let chapterFolder = url.pathExtension.lowercased() == "folder" && !inside.isEmpty
                guard chapterFolder || inside.contains(where: { ["txt", "html", "jpg", "png", "webp"].contains($0.pathExtension.lowercased()) }) else { continue }
            } else if !extensions.contains(url.pathExtension.lowercased()) {
                continue
            }
            let name = isFolder && url.pathExtension.lowercased() != "folder" ? url.lastPathComponent : url.deletingPathExtension().lastPathComponent
            let date = (try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            var group = groups[name.lowercased()] ?? (name, [], .distantPast)
            group.files.append(url)
            group.modified = max(group.modified, date)
            groups[name.lowercased()] = group
        }
        return groups.values.map { LibraryBook(name: $0.name, files: $0.files.sorted { $0.lastPathComponent < $1.lastPathComponent }, modified: $0.modified) }
            .sorted { $0.modified > $1.modified }
    }
}

/// The Library tab: the books downloaded on this device, as a shelf of covers. A tap reads an EPUB in
/// the Library's own reader (the built-in Catalog page's reader, opened here: it does not depend on the
/// Catalog tab or its site); ⋯ shows the book's files with the same actions as a finished download, and
/// Delete. A long press selects books (taps then pick more), for their files to go to another app at once, or to be deleted.
struct LibraryView: View {
    @Environment(AppModel.self) private var model
    @State private var books: [LibraryBook] = []
    @State private var query = ""
    @State private var open: LibraryBook?
    @State private var deleting: LibraryBook?
    @State private var reading: URL?
    @State private var librarySettings = false
    /// The books picked by a long press (their ids), and the file kinds left out of what they share.
    @State private var selected: Set<String> = []
    @State private var kindsOff: Set<String> = []
    /// The picked books' files (of the kinds left on) waiting for the delete to be confirmed.
    @State private var deletingPicked: [URL]?
    private var selecting: Bool { !selected.isEmpty }

    private func toggle(_ book: LibraryBook) {
        if selected.contains(book.id) { selected.remove(book.id) } else { selected.insert(book.id) }
    }

    private var shown: [LibraryBook] {
        let text = query.trimmingCharacters(in: .whitespaces)
        return text.isEmpty ? books : books.filter { $0.name.localizedCaseInsensitiveContains(text) }
    }

    var body: some View {
        ScrollView {
            if shown.isEmpty {
                Text(books.isEmpty ? "No books yet. Downloads are saved here and show up as soon as they finish." : "No book matches.")
                    .foregroundStyle(.secondary)
                    .padding(32)
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 104), spacing: 10)], spacing: 14) {
                ForEach(shown) { book in
                    VStack(alignment: .leading, spacing: 2) {
                        ZStack(alignment: .topTrailing) {
                            ShelfCover(book: book)
                            if selecting {
                                // Whether this book is picked.
                                Image(systemName: selected.contains(book.id) ? "checkmark.circle.fill" : "circle")
                                    .font(.title3).foregroundStyle(selected.contains(book.id) ? Color.accentColor : .secondary)
                                    .frame(width: 28, height: 28).background(.ultraThinMaterial, in: Circle())
                                    .padding(4)
                                    .accessibilityLabel(selected.contains(book.id) ? "Selected" : "Not selected")
                            } else {
                                Button { open = book } label: {
                                    Image(systemName: "ellipsis").font(.caption.bold()).frame(width: 28, height: 28)
                                        .background(.ultraThinMaterial, in: Circle())
                                }
                                .buttonStyle(.plain)
                                .padding(4)
                                .accessibilityLabel("Files and actions")
                            }
                        }
                        if let epub = book.epub {
                            let active = model.jobs.first { $0.state.isActive && $0.libraryTarget == epub.lastPathComponent }
                            if active != nil || model.libraryAllowed(epub) {
                                Text(active == nil ? "Auto-update" : (active?.state == .queued ? "Update queued" : "Updating…"))
                                    .font(.caption2.bold()).padding(4)
                                    .background(Color.accentColor.opacity(0.18), in: RoundedRectangle(cornerRadius: 5))
                            }
                        }
                        Text(book.name).font(.footnote.weight(.medium)).lineLimit(2).padding(.top, 4)
                        Text(book.formats).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    }
                    .contentShape(Rectangle())
                    .onTapGesture {
                        if selecting { toggle(book) } else {
                            if let epub = book.epub { model.requestLibraryUpdate(epub) }
                            if let file = book.readerFile { reading = file } else { open = book }
                        }
                    }
                    .onLongPressGesture { toggle(book) }
                }
            }
            .padding(.horizontal, 12)
            .padding(.bottom, 24)
        }
        .searchable(text: $query, prompt: "Find a book")
        .navigationTitle(selecting ? "\(selected.count) selected" : "Library")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) { Button("Library settings") { librarySettings = true } }
            if selecting {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { selected = [] } }
                ToolbarItem(placement: .primaryAction) {
                    let all = shown.allSatisfy { selected.contains($0.id) }
                    Button(all ? "None" : "All") { selected = all ? [] : Set(shown.map(\.id)) }
                }
            }
        }
        .safeAreaInset(edge: .bottom) { if selecting { selectionBar } }
        .refreshable { reload() }
        .onAppear { reload() }
        .onChange(of: model.jobs.filter { !$0.state.isActive }.count) { _, _ in reload() }
        .sheet(item: $open) { book in
            NavigationStack {
                List {
                    Section {
                        if let epub = book.epub {
                            Toggle("Auto-update this book", isOn: Binding(
                                get: { model.libraryUpdates.books[epub.lastPathComponent]?.enabled == true },
                                set: { model.setLibraryAutoUpdate([epub], enabled: $0) }))
                                .disabled(model.libraryUpdates.books[epub.lastPathComponent]?.url.isEmpty != false)
                        }
                        HStack(spacing: 12) {
                            ShelfCover(book: book).frame(width: 64)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(book.name).font(.headline).lineLimit(3)
                                Text(book.modified.formatted(date: .abbreviated, time: .omitted) + " · " + book.formats)
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        if let file = book.readerFile {
                            Button {
                                if let epub = book.epub { model.requestLibraryUpdate(epub) }
                                open = nil; reading = file
                            } label: { Label("Open", systemImage: "book") }
                        }
                        // The book in the Catalog: its site's page (where it came from) and its title searched, to
                        // download what an unfinished or outdated copy is missing.
                        Button {
                            open = nil
                            model.openInCatalog(book.epub.flatMap(EpubCover.book(for:)) ?? CatalogBook(title: book.name, url: nil))
                        } label: { Label("Open in Catalog", systemImage: "magnifyingglass") }
                        Button(role: .destructive) { open = nil; deleting = book } label: { Label("Delete", systemImage: "trash") }
                    }
                    Section("Files") {
                        ForEach(book.files, id: \.self) { file in
                            FileRow(file: file, jobID: model.jobs.first { $0.files(in: model.outputDirectory).contains(file) }?.id)
                        }
                    }
                }
                .navigationTitle(book.name)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { open = nil } } }
            }
            .presentationDetents([.medium, .large])
        }
        .fullScreenCover(isPresented: Binding(get: { reading != nil }, set: { if !$0 { reading = nil } })) {
            if let file = reading { LibraryReaderView(file: file) { reading = nil } }
        }
        .sheet(isPresented: $librarySettings) {
            NavigationStack {
                Form {
                    Toggle("Allow Library auto-updates", isOn: Binding(
                        get: { model.libraryUpdates.enabled }, set: { value in model.configureLibrary { $0.enabled = value } }))
                    Toggle("Background auto-update", isOn: Binding(
                        get: { model.libraryUpdates.background }, set: { value in model.configureLibrary { $0.background = value } }))
                        .disabled(!model.libraryUpdates.enabled)
                    Toggle("Upload refreshed EPUBs to Google Drive", isOn: Binding(
                        get: { model.libraryUpdates.uploadDrive }, set: { value in
                            if value { UserDefaults.standard.set(true, forKey: AppModel.shareAgreementKey) }
                            model.configureLibrary { $0.uploadDrive = value }
                        }))
                        .disabled(!model.libraryUpdates.enabled)
                    Text("Enabled books refresh the full novel. Background refresh runs every 15 minutes while the app is active. Ranged downloads and books without range history start off. Google Drive uploads are shared with anyone who has the link.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                .navigationTitle("Library settings")
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { librarySettings = false } } }
            }
        }
        .confirmationDialog("Delete \(deleting?.name ?? "")?", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                            titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                deleting?.files.forEach { try? FileManager.default.removeItem(at: $0) }
                deleting = nil
                reload()
            }
        }
        // The kinds switched off stay: picking EPUB only deletes the EPUBs and keeps the TXT copies.
        .confirmationDialog("Delete \(deletingPicked?.count ?? 0) file\(deletingPicked?.count == 1 ? "" : "s")? This cannot be undone.",
                            isPresented: Binding(get: { deletingPicked != nil }, set: { if !$0 { deletingPicked = nil } }),
                            titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                deletingPicked?.forEach { try? FileManager.default.removeItem(at: $0) }
                deletingPicked = nil
                selected = []
                reload()
            }
        }
    }

    private func reload() {
        books = LibraryBook.scan(model.outputDirectory)
        model.discoverLibrary(books.compactMap(\.epub))
    }

    /// "EPUB", "TXT" (a TXT folder too), "PDF": a file's kind, for the selection's filter.
    private static func kind(_ file: URL) -> String {
        LibraryBook.kind(file)
    }

    /// The picked books' files, to open in another app together (the share sheet: a reader, Files, a chat),
    /// less the kinds switched off here. A TXT folder goes as its files.
    private var selectionBar: some View {
        let picked = books.filter { selected.contains($0.id) }
        let all = picked.flatMap(\.files)
        var kinds: [String] = []
        for kind in all.map(Self.kind) where !kinds.contains(kind) { kinds.append(kind) }
        let kept = all.filter { !kindsOff.contains(Self.kind($0)) }
        let files = kept.map(SharedOutput.init(url:))
        return VStack(spacing: 8) {
            HStack {
                Button("Enable auto-update") { model.setLibraryAutoUpdate(picked.compactMap(\.epub), enabled: true) }
                Button("Disable auto-update") { model.setLibraryAutoUpdate(picked.compactMap(\.epub), enabled: false) }
            }
            if kinds.count > 1 {
                HStack(spacing: 8) {
                    ForEach(kinds.sorted(), id: \.self) { kind in
                        Toggle(kind, isOn: Binding(get: { !kindsOff.contains(kind) },
                                                   set: { if $0 { kindsOff.remove(kind) } else { kindsOff.insert(kind) } }))
                            .toggleStyle(.button)
                    }
                    Spacer()
                }
            }
            HStack {
                Text("\(picked.count) book\(picked.count == 1 ? "" : "s") · \(files.count) file\(files.count == 1 ? "" : "s")")
                    .font(.subheadline)
                Spacer()
                Button(role: .destructive) { deletingPicked = kept } label: { Label("Delete", systemImage: "trash") }
                    .buttonStyle(.bordered)
                    .disabled(kept.isEmpty)
                ShareLink(items: files, preview: { SharePreview($0.url.lastPathComponent) }) {
                    Label("Open in app", systemImage: "square.and.arrow.up")
                }
                    .buttonStyle(.borderedProminent)
                    .disabled(files.isEmpty)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.bar)
    }
}

/// A book's cover on the shelf: its EPUB's own, else its initial on a tinted tile, at 2:3.
private struct ShelfCover: View {
    let book: LibraryBook
    @State private var cover: UIImage?

    var body: some View {
        RoundedRectangle(cornerRadius: 8)
            .fill(Color(uiColor: .secondarySystemFill))
            .aspectRatio(2 / 3, contentMode: .fit)
            .overlay {
                if let cover {
                    Image(uiImage: cover).resizable().scaledToFill()
                } else {
                    Text(book.name.prefix(1).uppercased()).font(.title.bold()).foregroundStyle(Color.accentColor)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .shadow(color: .black.opacity(0.15), radius: 2, y: 1)
            .task(id: book.modified) {
                guard let epub = book.epub else { return }
                cover = await Task.detached(priority: .utility) { EpubCover.image(for: epub) }.value
            }
    }
}

/// A Library book in the reader, full screen over the Library: a page of its own with the built-in
/// Catalog page, so the book opens whatever the Catalog tab shows. Closing the reader closes it.
struct LibraryReaderView: View {
    let file: URL
    let close: () -> Void
    @Environment(AppModel.self) private var model
    @StateObject private var state = CatalogState(site: URL(string: CatalogSite.genericAddress)!)
    @AppStorage(Accent.storageKey) private var accent = Accent.halgakosRose.rawValue
    @Environment(\.colorScheme) private var colorScheme

    init(file: URL, close: @escaping () -> Void) {
        self.file = file
        self.close = close
    }

    var body: some View {
        CatalogWebView(state: state)
            .ignoresSafeArea(edges: state.readerFullScreen ? .all : [])
            .statusBarHidden(state.readerFullScreen)
            .onAppear {
                state.model = model
                state.onReaderClosed = close
                CatalogTransport.libraryFolder = model.outputDirectory
                state.setTheme((Accent(rawValue: accent) ?? .halgakosRose).webTheme(dark: colorScheme == .dark))
                state.readLocal(file)
            }
            .onDisappear { state.onReaderClosed = nil }
    }
}
