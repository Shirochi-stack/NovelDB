import NpiaCore
import NpiaSources
import SwiftUI

/// The desktop's "Enter App": one place to sign in to the sources whose chapters need the user's own
/// account. sfc signs in through its app API (or takes a pasted app cookie); qdn and rbooks sign in on their
/// websites, in the app's own browser, and downloads share that browser's cookies.
struct EnterAppView: View {
    @Environment(AppModel.self) private var model
    @State private var username = ""
    @State private var password = ""
    @State private var cookie = ""
    @State private var statuses: [SourceID: SessionStatus] = [:]
    @FocusState private var typing: Bool

    var body: some View {
        // Reading the version makes the screen read the sessions again when a sign-in or sign-out ends.
        let version = model.sessionVersion
        Form {
            sfcSection(signedIn: model.sfcSignedIn)
            ForEach(HardSessions.all, id: \.source) { site in
                browserSection(site)
            }
            Section {
                EmptyView()
            } footer: {
                Text("Downloads use the same browser, so a login here is a login for downloads. Chapters your account has not bought are skipped; nothing is ever purchased.")
            }
        }
        .themedBackground()
        .scrollDismissesKeyboard(.interactively)
        .navigationTitle("Enter App")
        .task(id: version) { await refresh() }
    }

    private func refresh() async {
        var found: [SourceID: SessionStatus] = [:]
        for site in HardSessions.all { found[site.source] = await model.sessions.status(site) }
        statuses = found
    }

    // MARK: sfc

    @ViewBuilder
    private func sfcSection(signedIn: Bool) -> some View {
        Section {
            TextField("Username", text: $username)
                .textContentType(.username)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .focused($typing)
            SecureField("Password", text: $password)
                .textContentType(.password)
            Button(model.sfcWorking ? "Signing in..." : "Sign in") {
                let name = username.trimmingCharacters(in: .whitespacesAndNewlines)
                let secret = password
                password = ""
                typing = false
                Task { await model.signInToSfc(username: name, password: secret) }
            }
            .disabled(model.sfcWorking || username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || password.isEmpty)
            TextField(".SFCommunity=...; session_APP=...", text: $cookie, axis: .vertical)
                .lineLimit(2...4)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .font(.callout)
            Text("Or paste the cookie header from the sfc app. It must contain session_APP.")
                .font(.caption)
                .foregroundStyle(.secondary)
            Button("Save cookie") {
                if model.saveSfcCookie(cookie) { cookie = "" }
            }
            .disabled(cookie.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            if signedIn {
                Button("Sign out", role: .destructive) { model.signOutOfSfc() }
            }
        } header: {
            Text("sfc")
        } footer: {
            VStack(alignment: .leading, spacing: 4) {
                Text(signedIn
                    ? "App session saved: VIP chapters you bought download through the app API."
                    : "No app session saved. Only free chapters can be read.")
                Text("Stored only in the iOS Keychain, never in settings files or exports.")
            }
        }
    }

    // MARK: qdn and rbooks

    @ViewBuilder
    private func browserSection(_ site: SessionSite) -> some View {
        let status = statuses[site.source]
        Section {
            Text(status?.summary(for: site) ?? "Reading the sign-in status...")
                .font(.callout)
                .foregroundStyle(status?.usable == true ? Color.green : Color.secondary)
            Button(status?.usable == true ? "Sign in again" : "Sign in") {
                Task { await model.signInToSite(site) }
            }
            if let status, !status.present.isEmpty || status.hasSavedCopy {
                Button("Sign out", role: .destructive) {
                    Task { await model.signOutOfSite(site) }
                }
            }
        } header: {
            Text(site.source.rawValue)
        } footer: {
            Text(help(for: site))
        }
    }

    private func help(for site: SessionSite) -> String {
        switch site.source {
        case .qdn:
            return "Opens your qdn account in the app's browser, which asks you to sign in. Sign in there, then tap Done; if the site shows a verification, complete it first. qdn keeps this login only for the browser session, so the app also saves a copy and puts it back if the browser loses it."
        default:
            return "Opens the \(site.source.rawValue) sign-in page in the app's browser. Sign in there (and finish adult verification if asked), then tap Done."
        }
    }
}
