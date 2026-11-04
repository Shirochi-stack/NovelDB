import NpiaSources
import SwiftUI

/// A book in the Library: every output sharing a name ("Title.epub", "Title.txt", a "Title/" TXT folder).
struct LibraryBook: Identifiable {
    let name: String
    let files: [URL]
    let modified: Date
    var id: String { name.lowercased() }
    var epub: URL? { files.first { $0.pathExtension.lowercased() == "epub" } }

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
        let kinds = files.map { (try? $0.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true ? "TXT" : $0.pathExtension.uppercased() }
        var seen: [String] = []
        for kind in kinds where !seen.contains(kind) { seen.append(kind) }
        return (seen + [ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)]).joined(separator: " · ")
    }

    static let extensions: Set<String> = ["epub", "pdf", "txt", "html", "htm", "cbz", "zip"]

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
                guard inside.contains(where: { ["txt", "html", "jpg", "png", "webp"].contains($0.pathExtension.lowercased()) }) else { continue }
            } else if !extensions.contains(url.pathExtension.lowercased()) {
                continue
            }
            let name = isFolder ? url.lastPathComponent : url.deletingPathExtension().lastPathComponent
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
/// Catalog tab or its site); a long press (or ⋯) shows the book's files with the same actions as a
/// finished download, and Delete.
struct LibraryView: View {
    @Environment(AppModel.self) private var model
    @State private var books: [LibraryBook] = []
    @State private var query = ""
    @State private var open: LibraryBook?
    @State private var deleting: LibraryBook?
    @State private var reading: URL?

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
                            Button { open = book } label: {
                                Image(systemName: "ellipsis").font(.caption.bold()).frame(width: 28, height: 28)
                                    .background(.ultraThinMaterial, in: Circle())
                            }
                            .buttonStyle(.plain)
                            .padding(4)
                            .accessibilityLabel("Files and actions")
                        }
                        Text(book.name).font(.footnote.weight(.medium)).lineLimit(2).padding(.top, 4)
                        Text(book.formats).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    }
                    .contentShape(Rectangle())
                    .onTapGesture {
                        if let epub = book.epub { reading = epub } else { open = book }
                    }
                    .onLongPressGesture { open = book }
                }
            }
            .padding(.horizontal, 12)
            .padding(.bottom, 24)
        }
        .searchable(text: $query, prompt: "Find a book")
        .navigationTitle("Library")
        .refreshable { reload() }
        .onAppear { reload() }
        .onChange(of: model.jobs.filter { !$0.state.isActive }.count) { _, _ in reload() }
        .sheet(item: $open) { book in
            NavigationStack {
                List {
                    Section {
                        HStack(spacing: 12) {
                            ShelfCover(book: book).frame(width: 64)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(book.name).font(.headline).lineLimit(3)
                                Text(book.modified.formatted(date: .abbreviated, time: .omitted) + " · " + book.formats)
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        if let epub = book.epub {
                            Button { open = nil; reading = epub } label: { Label("Read", systemImage: "book") }
                        }
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
            if let epub = reading { LibraryReaderView(epub: epub) { reading = nil } }
        }
        .confirmationDialog("Delete \(deleting?.name ?? "")?", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                            titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                deleting?.files.forEach { try? FileManager.default.removeItem(at: $0) }
                deleting = nil
                reload()
            }
        }
    }

    private func reload() {
        books = LibraryBook.scan(model.outputDirectory)
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
            .task(id: book.epub) {
                guard let epub = book.epub else { return }
                cover = await Task.detached(priority: .utility) { EpubCover.image(for: epub) }.value
            }
    }
}

/// A Library book in the reader, full screen over the Library: a page of its own with the built-in
/// Catalog page, so the book opens whatever the Catalog tab shows. Closing the reader closes it.
private struct LibraryReaderView: View {
    let epub: URL
    let close: () -> Void
    @Environment(AppModel.self) private var model
    @StateObject private var state = CatalogState(site: URL(string: CatalogSite.genericAddress)!)
    @AppStorage(Accent.storageKey) private var accent = Accent.halgakosRose.rawValue
    @Environment(\.colorScheme) private var colorScheme

    init(epub: URL, close: @escaping () -> Void) {
        self.epub = epub
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
                state.readLocal(epub)
            }
            .onDisappear { state.onReaderClosed = nil }
    }
}
