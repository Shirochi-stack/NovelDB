import XCTest
import NpiaCore
@testable import NpiaSources

final class EngineUpdaterTests: XCTestCase {
    func testANewerReleaseIsDownloadedAndUsed() async throws {
        for engine in RuleEngine.allCases {
            let fixture = try EngineFixture()
            let release = FakeRelease()
            let body = Data("/* newer */".utf8)
            release.publish(engine, body: body)
            let updater = release.updater(fixture.store)

            let result = await updater.check(engine)
            XCTAssertEqual(result.status, .updated, result.message)
            XCTAssertTrue(result.message.hasPrefix("Updated the \(engine.title) to "), result.message)
            let bundle = try XCTUnwrap(fixture.store.current(engine))
            XCTAssertTrue(bundle.updated)
            XCTAssertEqual(try bundle.read(), "/* newer */")
            XCTAssertEqual(bundle.manifest.file, engine.lib)
            XCTAssertEqual(release.calls, [engine.releaseManifest, engine.lib + "-new.js"])
            XCTAssertTrue(FileManager.default.fileExists(atPath: fixture.updates(engine).appendingPathComponent("manifest.json").path))

            // Checking again finds nothing new and downloads nothing.
            let again = await updater.check(engine)
            XCTAssertEqual(again.status, .current, again.message)
            XCTAssertEqual(release.calls.count, 3, "only the manifest was fetched the second time")
        }
    }

    func testARebuildOfTheBundleInUseIsNotDownloaded() async throws {
        // Same upstream commit and shim as the shipped parsers; only the build time is later.
        let fixture = try EngineFixture()
        let release = FakeRelease()
        release.publish(.webtoepub, body: Data("/* same parsers, built again */".utf8), overrides: [
            "commit": String(repeating: "a", count: 40), "shim": "s1", "date": "2026-10-27T04:19:22Z",
            "built": "2026-11-09T00:00:00Z",
        ])
        let result = await release.updater(fixture.store).check(.webtoepub)
        XCTAssertEqual(result.status, .current, result.message)
        XCTAssertTrue(result.message.contains("up to date"), result.message)
        XCTAssertEqual(release.calls, ["webtoepub-manifest.json"], "nothing was downloaded")
        XCTAssertFalse(try XCTUnwrap(fixture.store.current(.webtoepub)).updated)

        // The same commit with another shim is different parsers and is downloaded.
        let patched = FakeRelease()
        patched.publish(.webtoepub, body: Data("/* patched shim */".utf8), overrides: [
            "commit": String(repeating: "a", count: 40), "shim": "s2", "date": "2026-10-27T04:19:22Z",
            "built": "2026-11-09T00:00:00Z",
        ])
        let next = await patched.updater(fixture.store).check(.webtoepub)
        XCTAssertEqual(next.status, .updated, next.message)
    }

    func testABundleWithoutACommitIsNeverTakenForARebuild() async throws {
        // The shipped rules only know their version, so an empty commit and shim never match the release's.
        let fixture = try EngineFixture()
        let release = FakeRelease()
        release.publish(.novelDownloader, body: Data("/* newer rules */".utf8), overrides: ["commit": "", "shim": ""])
        let result = await release.updater(fixture.store).check(.novelDownloader)
        XCTAssertEqual(result.status, .updated, result.message)
    }

    func testOlderOrIncompatibleReleasesAreNotDownloaded() async throws {
        let fixture = try EngineFixture()
        let body = Data("/* other */".utf8)

        // A lower version loses even with a later date.
        let older = FakeRelease()
        older.publish(.novelDownloader, body: body, overrides: ["version": "5.2.1200", "date": "2027-01-01T00:00:00Z"])
        var result = await older.updater(fixture.store).check(.novelDownloader)
        XCTAssertEqual(result.status, .current, result.message)
        XCTAssertEqual(older.calls, ["novel-downloader-manifest.json"])

        // The same version from the same commit date with an earlier build loses too.
        let same = FakeRelease()
        same.publish(.webtoepub, body: body, overrides: [
            "version": "1.0.14.61", "date": "2026-10-27T04:19:22Z", "built": "2026-10-27T00:00:00Z",
        ])
        result = await same.updater(fixture.store).check(.webtoepub)
        XCTAssertEqual(result.status, .current, result.message)

        // Another API is not for this app, for either engine.
        for engine in RuleEngine.allCases {
            let release = FakeRelease()
            release.publish(engine, body: body, overrides: ["api": 2])
            let incompatible = await release.updater(fixture.store).check(engine)
            XCTAssertEqual(incompatible.status, .current, incompatible.message)
            XCTAssertTrue(incompatible.message.contains("newer NovelDB"), incompatible.message)
            XCTAssertEqual(release.calls.count, 1)
        }

        // A manifest for the other engine is refused.
        let wrong = FakeRelease()
        wrong.publish(.webtoepub, body: body, overrides: ["engine": "novel_downloader"])
        result = await wrong.updater(fixture.store).check(.webtoepub)
        XCTAssertEqual(result.status, .failed)

        XCTAssertFalse(try XCTUnwrap(fixture.store.current(.novelDownloader)).updated)
        XCTAssertFalse(try XCTUnwrap(fixture.store.current(.webtoepub)).updated)
    }

    func testAManifestWithoutAnEngineNameIsAccepted() async throws {
        let fixture = try EngineFixture()
        let release = FakeRelease()
        var manifest = release.publish(.webtoepub, body: Data("/* x */".utf8))
        manifest.removeValue(forKey: "engine")
        release.serve(RuleEngine.webtoepub.releaseManifest, try JSONSerialization.data(withJSONObject: manifest))
        let result = await release.updater(fixture.store).check(.webtoepub)
        XCTAssertEqual(result.status, .updated, result.message)
    }

    func testABadChecksumSizeOrFileNameIsRefused() async throws {
        let body = Data("/* tampered */".utf8)
        let cases: [(String, [String: Any])] = [
            ("checksum", ["sha256": String(repeating: "0", count: 64)]),
            ("size", ["size": body.count + 1]),
            ("parent folder", ["file": "../evil.js"]),
            ("folder", ["file": "sub/evil.js"]),
            ("backslash", ["file": "sub" + EngineFixture.backslash + "evil.js"]),
            ("no file", ["file": ""]),
        ]
        for (name, overrides) in cases {
            let fixture = try EngineFixture()
            let release = FakeRelease()
            release.publish(.novelDownloader, body: body, overrides: overrides)
            let result = await release.updater(fixture.store).check(.novelDownloader)
            XCTAssertEqual(result.status, .failed, "\(name): \(result.message)")
            XCTAssertFalse(try XCTUnwrap(fixture.store.current(.novelDownloader)).updated, name)
            XCTAssertFalse(FileManager.default.fileExists(atPath: fixture.updates(.novelDownloader).appendingPathComponent("rules-lib.js").path), name)
        }
        // A bad file name is refused before anything is downloaded.
        let fixture = try EngineFixture()
        let release = FakeRelease()
        release.publish(.novelDownloader, body: body, overrides: ["file": "../evil.js"])
        _ = await release.updater(fixture.store).check(.novelDownloader)
        XCTAssertEqual(release.calls, ["novel-downloader-manifest.json"])
    }

    func testAFailedCheckIsReportedAndRemembered() async throws {
        let fixture = try EngineFixture()
        let release = FakeRelease()
        release.failEverything(with: URLError(.notConnectedToInternet))
        let result = await release.updater(fixture.store).check(.webtoepub)
        XCTAssertEqual(result.status, .failed)
        XCTAssertTrue(result.message.hasPrefix("Could not check for \(RuleEngine.webtoepub.title) updates: "), result.message)
        XCTAssertNotNil(fixture.store.lastChecked(.webtoepub))
        XCTAssertNil(fixture.store.lastChecked(.novelDownloader))
    }

    func testAMissingReleaseIsAnOrdinaryFailedCheck() async throws {
        // Before the `engines` release exists every download is a 404.
        let fixture = try EngineFixture()
        let result = await FakeRelease().updater(fixture.store).check(.novelDownloader)
        XCTAssertEqual(result.status, .failed)
        XCTAssertTrue(result.message.hasPrefix("Could not check for novel-downloader rules updates: "), result.message)
        XCTAssertFalse(try XCTUnwrap(fixture.store.current(.novelDownloader)).updated)
    }

    func testAManifestThatIsNotAnObjectFails() async throws {
        let fixture = try EngineFixture()
        let release = FakeRelease()
        release.serve(RuleEngine.webtoepub.releaseManifest, Data("[1, 2]".utf8))
        let result = await release.updater(fixture.store).check(.webtoepub)
        XCTAssertEqual(result.status, .failed)
        XCTAssertTrue(result.message.contains("not an object"), result.message)
    }

    func testARejectedUpdateFallsBackAndIsNotFetchedAgain() async throws {
        let fixture = try EngineFixture()
        let release = FakeRelease()
        let body = Data("/* broken */".utf8)
        release.publish(.webtoepub, body: body)
        let updater = release.updater(fixture.store)
        let first = await updater.check(.webtoepub)
        XCTAssertEqual(first.status, .updated, first.message)

        fixture.store.reject(try XCTUnwrap(fixture.store.current(.webtoepub)), reason: "SyntaxError")
        XCTAssertFalse(try XCTUnwrap(fixture.store.current(.webtoepub)).updated)

        let callsBefore = release.calls.count
        let result = await updater.check(.webtoepub)
        XCTAssertEqual(result.status, .current, result.message)
        XCTAssertTrue(result.message.contains("failed to load before"), result.message)
        XCTAssertEqual(release.calls.count, callsBefore + 1, "only the manifest was fetched")

        // A later build is a different release and is tried.
        release.publish(.webtoepub, body: Data("/* fixed */".utf8), overrides: ["commit": String(repeating: "c", count: 40), "built": "2026-11-05T00:00:00Z"])
        let later = await updater.check(.webtoepub)
        XCTAssertEqual(later.status, .updated, later.message)
    }

    func testTheAutomaticCheckRunsOnceADay() async throws {
        let fixture = try EngineFixture()
        let release = FakeRelease()
        let clock = TestClock()
        let updater = release.updater(fixture.store, now: { clock.now })

        let first = await updater.checkIfDue(.novelDownloader)
        XCTAssertNotNil(first, "the first check is always due")
        XCTAssertEqual(release.calls.count, 1)
        let tooSoon = await updater.checkIfDue(.novelDownloader)
        XCTAssertNil(tooSoon)
        clock.advance(23 * 3600)
        let stillSoon = await updater.checkIfDue(.novelDownloader)
        XCTAssertNil(stillSoon)
        XCTAssertEqual(release.calls.count, 1)
        clock.advance(2 * 3600)
        let due = await updater.checkIfDue(.novelDownloader)
        XCTAssertNotNil(due)
        XCTAssertEqual(release.calls.count, 2)

        // Each engine keeps its own clock, and a manual check counts as a check.
        let other = await updater.checkIfDue(.webtoepub)
        XCTAssertNotNil(other)
        _ = await updater.check(.novelDownloader)
        let afterManual = await updater.checkIfDue(.novelDownloader)
        XCTAssertNil(afterManual)
    }

    func testAClockSetBackDoesNotBlockChecksForever() async throws {
        let fixture = try EngineFixture()
        let release = FakeRelease()
        let clock = TestClock()
        let updater = release.updater(fixture.store, now: { clock.now })
        _ = await updater.check(.novelDownloader)
        clock.advance(-48 * 3600)
        let result = await updater.checkIfDue(.novelDownloader)
        XCTAssertNotNil(result)
    }
}
