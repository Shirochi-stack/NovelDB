import XCTest
@testable import NpiaUpdate

final class CheckerTests: UpdateTestCase {
    private let current = AppBuildInfo(build: 1_800_000_000, tag: "v97", label: "none")

    // MARK: - Reading a manifest

    func testANewerBuildIsOffered() throws {
        let outcome = AppUpdateChecker.evaluate(manifest: FakeRelease().manifest(), build: current)
        guard case .available(let update) = outcome else { return XCTFail("expected an update, got \(outcome)") }
        XCTAssertEqual(update.tag, "v98")
        XCTAssertEqual(update.label, "hotfix3")
        XCTAssertEqual(update.build, 1_800_000_500)
        XCTAssertEqual(update.versionName, "v98 hotfix3")
        XCTAssertEqual(update.title, "NovelDB v98 hotfix3 is available")
        XCTAssertEqual(outcome.message, "NovelDB v98 hotfix3 is available.")
        XCTAssertEqual(update.asset.name, "NovelDB-unsigned.ipa")
        XCTAssertEqual(update.downloadURL.absoluteString, "https://github.com/Shirochi-stack/NovelDB/releases/download/v98/NovelDB-unsigned.ipa")
        XCTAssertEqual(update.releaseURL.absoluteString, "https://github.com/Shirochi-stack/NovelDB/releases/tag/v98")
    }

    func testABuildNoNewerThanTheRunningOneIsNotOffered() {
        // The entry's build is 1_800_000_500.
        let expectations: [(Int, Bool)] = [(1, true), (1_800_000_499, true), (1_800_000_500, false), (1_800_000_501, false), (2_000_000_000, false)]
        for (appBuild, offered) in expectations {
            let outcome = AppUpdateChecker.evaluate(manifest: FakeRelease().manifest(), build: AppBuildInfo(build: appBuild, tag: "v1"))
            if offered {
                guard case .available = outcome else { XCTFail("build \(appBuild): expected an update, got \(outcome)"); continue }
            } else {
                XCTAssertEqual(outcome, .upToDate, "build \(appBuild)")
            }
        }
        XCTAssertEqual(AppUpdateOutcome.upToDate.message, "NovelDB is up to date.")
    }

    func testADevelopmentBuildIsNeverOfferedAnUpdate() {
        let outcome = AppUpdateChecker.evaluate(manifest: FakeRelease().manifest(), build: .development)
        XCTAssertEqual(outcome, .development)
        XCTAssertEqual(outcome.message, "Development build: app updates are off.")
    }

    func testAMissingEntryOrABadManifestIsNoUpdateInformation() {
        let release = FakeRelease()
        let androidOnly = release.manifest(entry: ["kind": "apk", "name": "NovelDB.apk"], top: ["platform": "android"])
        let iosWithoutAnIPA = release.manifest(entry: ["kind": "apk", "name": "NovelDB.apk"])
        let otherVariant = release.manifest(entry: ["variant": "lite"])
        let wrongPlatform = release.manifest(top: ["platform": "android"])
        let wrongSchema = release.manifest(top: ["schema": 2])
        let noAssets = release.manifest(top: ["assets": []])
        let unsafeTag = release.manifest(top: ["tag": "../v98"])
        let unsafeName = release.manifest(entry: ["name": "../NovelDB.ipa"])
        let cases: [(String, Data)] = [
            ("android manifest", androidOnly), ("no ipa entry", iosWithoutAnIPA), ("other variant", otherVariant),
            ("platform mismatch", wrongPlatform), ("wrong schema", wrongSchema), ("no assets", noAssets),
            ("unsafe tag", unsafeTag), ("unsafe file name", unsafeName),
            ("html", Data("<html>Not found</html>".utf8)), ("empty", Data()),
        ]
        for (name, data) in cases {
            XCTAssertEqual(AppUpdateChecker.evaluate(manifest: data, build: current), .noInformation, name)
        }
        XCTAssertEqual(AppUpdateOutcome.noInformation.message, "No update information in the latest release.")
    }

    func testAManifestWithoutAPlatformFieldIsStillRead() {
        var object = (try! JSONSerialization.jsonObject(with: FakeRelease().manifest())) as! [String: Any]
        object.removeValue(forKey: "platform")
        let data = try! JSONSerialization.data(withJSONObject: object)
        guard case .available = AppUpdateChecker.evaluate(manifest: data, build: current) else { return XCTFail("expected an update") }
    }

    func testTheReleasePageComesFromTheManifestOnlyWhenItIsHTTPS() {
        func page(_ releaseURL: Any) -> String? {
            let data = FakeRelease().manifest(top: ["release_url": releaseURL])
            guard case .available(let update) = AppUpdateChecker.evaluate(manifest: data, build: current) else { return nil }
            return update.releaseURL.absoluteString
        }
        XCTAssertEqual(page("https://github.com/Shirochi-stack/NovelDB/releases/tag/v98"), "https://github.com/Shirochi-stack/NovelDB/releases/tag/v98")
        XCTAssertEqual(page("http://example.com/x"), "https://github.com/Shirochi-stack/NovelDB/releases/tag/v98", "plain http is replaced")
        XCTAssertEqual(page("javascript:alert(1)"), "https://github.com/Shirochi-stack/NovelDB/releases/tag/v98")
        XCTAssertEqual(page(""), "https://github.com/Shirochi-stack/NovelDB/releases/tag/v98", "an empty address is replaced")
        XCTAssertEqual(page(42), "https://github.com/Shirochi-stack/NovelDB/releases/tag/v98", "a non-text address is replaced")
    }

    func testAnUpdateWithoutALabelIsNamedByItsTag() {
        let data = FakeRelease(label: "none").manifest()
        guard case .available(let update) = AppUpdateChecker.evaluate(manifest: data, build: current) else { return XCTFail("expected an update") }
        XCTAssertEqual(update.title, "NovelDB v98 is available")
    }

    // MARK: - Fetching the manifest

    func testACheckFetchesTheLatestManifestOfThisPlatform() async {
        let release = FakeRelease()
        StubURLProtocol.handler = { _ in StubURLProtocol.Reply(body: release.manifest()) }
        let outcome = await makeChecker().check()
        guard case .available(let update) = outcome else { return XCTFail("expected an update, got \(outcome)") }
        XCTAssertEqual(update.versionName, "v98 hotfix3")
        XCTAssertEqual(StubURLProtocol.seen.count, 1)
        XCTAssertEqual(StubURLProtocol.seen.first?.url?.absoluteString, "https://github.com/Shirochi-stack/NovelDB/releases/latest/download/update-ios.json")
        XCTAssertEqual(StubURLProtocol.seen.first?.value(forHTTPHeaderField: "User-Agent"), "NovelDB")
    }

    func testAnOverriddenBaseIsWhereTheCheckGoes() async {
        StubURLProtocol.handler = { _ in StubURLProtocol.Reply(status: 404) }
        let endpoints = AppUpdateEndpoints(base: URL(string: "http://127.0.0.1:8765/releases")!)
        _ = await makeChecker(endpoints: endpoints).check()
        XCTAssertEqual(StubURLProtocol.seen.first?.url?.absoluteString, "http://127.0.0.1:8765/releases/latest/download/update-ios.json")
    }

    func testA404IsNoUpdateInformationNotAnError() async {
        StubURLProtocol.handler = { _ in StubURLProtocol.Reply(status: 404) }
        let outcome = await makeChecker().check()
        XCTAssertEqual(outcome, .noInformation)
    }

    func testABadManifestBodyIsNoUpdateInformation() async {
        StubURLProtocol.handler = { _ in StubURLProtocol.Reply(body: Data("not json".utf8)) }
        let outcome = await makeChecker().check()
        XCTAssertEqual(outcome, .noInformation)
    }

    func testAServerErrorOrNoNetworkIsAFailedCheck() async {
        StubURLProtocol.handler = { _ in StubURLProtocol.Reply(status: 503) }
        var outcome = await makeChecker().check()
        XCTAssertEqual(outcome, .failed("the server answered HTTP 503."))
        XCTAssertEqual(outcome.message, "Could not check for updates: the server answered HTTP 503.")

        StubURLProtocol.handler = { _ in throw URLError(.notConnectedToInternet) }
        outcome = await makeChecker().check()
        guard case .failed = outcome else { return XCTFail("expected a failed check, got \(outcome)") }
    }

    func testADevelopmentBuildFetchesNothing() async {
        StubURLProtocol.handler = { _ in StubURLProtocol.Reply(body: FakeRelease().manifest()) }
        let checker = makeChecker(build: .development)
        let manual = await checker.check()
        XCTAssertEqual(manual, .development)
        let automatic = await checker.checkIfDue()
        XCTAssertNil(automatic)
        XCTAssertTrue(StubURLProtocol.seen.isEmpty, "no request was made")
        let recorded = await checker.lastChecked()
        XCTAssertNil(recorded)
    }

    // MARK: - The 24-hour rule

    func testTheRuleItself() {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let hour: TimeInterval = 3600
        XCTAssertTrue(AppUpdateChecker.isDue(last: nil, now: now), "never checked")
        XCTAssertFalse(AppUpdateChecker.isDue(last: now, now: now))
        XCTAssertFalse(AppUpdateChecker.isDue(last: now.addingTimeInterval(-hour), now: now))
        XCTAssertFalse(AppUpdateChecker.isDue(last: now.addingTimeInterval(-(24 * hour - 1)), now: now))
        XCTAssertTrue(AppUpdateChecker.isDue(last: now.addingTimeInterval(-24 * hour), now: now))
        XCTAssertTrue(AppUpdateChecker.isDue(last: now.addingTimeInterval(-72 * hour), now: now))
        XCTAssertTrue(AppUpdateChecker.isDue(last: now.addingTimeInterval(hour), now: now), "the clock was set back")
        XCTAssertEqual(AppUpdateChecker.checkInterval, 86_400)
    }

    func testAutomaticChecksRunAtMostOncePerDay() async {
        StubURLProtocol.handler = { _ in StubURLProtocol.Reply(status: 404) }
        let clock = TestClock()
        let checker = makeChecker(clock: clock)

        var result = await checker.checkIfDue()
        XCTAssertNotNil(result, "the first check runs")
        XCTAssertEqual(StubURLProtocol.seen.count, 1)

        clock.advance(by: 23 * 3600)
        result = await checker.checkIfDue()
        XCTAssertNil(result, "23 hours later it is not due")
        XCTAssertEqual(StubURLProtocol.seen.count, 1)

        clock.advance(by: 2 * 3600)
        result = await checker.checkIfDue()
        XCTAssertNotNil(result, "25 hours after the first check it is due again")
        XCTAssertEqual(StubURLProtocol.seen.count, 2)
    }

    func testCheckNowIgnoresTheRuleAndRestartsTheDay() async {
        StubURLProtocol.handler = { _ in StubURLProtocol.Reply(status: 404) }
        let clock = TestClock()
        let checker = makeChecker(clock: clock)
        _ = await checker.checkIfDue()
        clock.advance(by: 3600)
        _ = await checker.check()
        XCTAssertEqual(StubURLProtocol.seen.count, 2, "a manual check always fetches")

        clock.advance(by: 23 * 3600 + 1800)
        var result = await checker.checkIfDue()
        XCTAssertNil(result, "the day counts from the manual check, 23.5 hours ago")
        clock.advance(by: 1800)
        result = await checker.checkIfDue()
        XCTAssertNotNil(result)
    }

    func testAFailedCheckDoesNotUseUpTheDay() async {
        let clock = TestClock()
        let checker = makeChecker(clock: clock)
        StubURLProtocol.handler = { _ in throw URLError(.notConnectedToInternet) }
        var result = await checker.checkIfDue()
        guard case .failed = result else { return XCTFail("expected a failed check, got \(String(describing: result))") }
        let recorded = await checker.lastChecked()
        XCTAssertNil(recorded)

        StubURLProtocol.handler = { _ in StubURLProtocol.Reply(status: 404) }
        clock.advance(by: 60)
        result = await checker.checkIfDue()
        XCTAssertEqual(result, .noInformation, "the next foreground check tries again")
    }

    func testTheLastCheckIsKeptInUserDefaultsAcrossLaunches() async {
        StubURLProtocol.handler = { _ in StubURLProtocol.Reply(status: 404) }
        let clock = TestClock()
        _ = await makeChecker(clock: clock).check()
        XCTAssertEqual(defaults.object(forKey: AppUpdateChecker.lastCheckKey) as? Double, clock.date.timeIntervalSince1970)

        clock.advance(by: 3600)
        let relaunched = makeChecker(clock: clock)
        let due = await relaunched.isDue()
        XCTAssertFalse(due, "a new checker reads the time the old one stored")
        let last = await relaunched.lastChecked()
        XCTAssertEqual(last, Date(timeIntervalSince1970: 1_800_000_000))
    }
}
