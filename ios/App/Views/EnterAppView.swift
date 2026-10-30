import NpiaCore
import NpiaSources
import SwiftUI

/// The desktop's "Enter App": one place to sign in to the sources whose chapters need the user's own
/// account. qdn, rbooks and the sfc website sign in on their websites, in the app's own browser, and
/// downloads share that browser's cookies. sfc can also sign in through its app API (or take a pasted app
/// cookie), which gets VIP chapters as text. The desktop reads the sfc app's login out of an emulator; on a
/// phone that login stays inside the sfc app, so its website sign-in (with the same Facebook, QQ and Weibo
/// options) stands in for it.
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
            sfcSection(signedIn: model.sfcSignedIn, site: statuses[.sfc])
            ForEach(HardSessions.all.filter { $0.source != .sfc }, id: \.source) { site in
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
    private func sfcSection(signedIn: Bool, site: SessionStatus?) -> some View {
        Section {
            Button(site?.usable == true ? "Sign in on the website again" : "Sign in on the website") {
                Task { await model.signInToSite(HardSessions.sfc) }
            }
            if let site, !site.present.isEmpty || site.hasSavedCopy {
                Button("Sign out of the website", role: .destructive) {
                    Task { await model.signOutOfSite(HardSessions.sfc) }
                }
            }
            Text("Sign in on the sfc website with your password, Facebook, QQ or Weibo, then tap Done. The app signs in as the sfc app, so if sfc hands it an app session, VIP chapters you bought download as text; if it does not, they download as the website's pictures. Signing in with a username and password below always gets the app session (text).")
                .font(.caption)
                .foregroundStyle(.secondary)
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
                Button("Remove the app session", role: .destructive) { model.signOutOfSfc() }
            }
        } header: {
            Text("sfc")
        } footer: {
            VStack(alignment: .leading, spacing: 4) {
                Text(sfcSummary(signedIn: signedIn, site: site))
                Text("Stored only in the iOS Keychain, never in settings files or exports.")
            }
        }
    }

    private func sfcSummary(signedIn: Bool, site: SessionStatus?) -> String {
        if signedIn { return "App session saved: VIP chapters you bought download as text through the app API." }
        if site?.usable == true { return "Signed in on the website: VIP chapters you bought download from it." }
        if site?.hasSavedCopy == true { return "Not signed in on the website. A saved copy exists and is restored when the app starts." }
        return "Not signed in. Only free chapters download."
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
