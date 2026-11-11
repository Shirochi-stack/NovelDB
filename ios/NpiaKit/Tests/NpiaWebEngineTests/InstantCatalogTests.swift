import Foundation
import XCTest
import NpiaCore
@testable import NpiaSources
#if canImport(AppKit)
import AppKit
#endif
@testable import NpiaWebEngine

@MainActor
final class InstantCatalogTests: XCTestCase {
    func testBundledCataloguePathsResolveWithinTheResourceFolder() throws {
        let index = try XCTUnwrap(CatalogPage.file("/index.html"))
        XCTAssertTrue(index.lastPathComponent == "index.html")
        XCTAssertNil(CatalogPage.file("/instant/manifest.bin"))
        XCTAssertNil(CatalogPage.file("/instant/missing.bin"))
        XCTAssertNil(CatalogPage.file(""))
    }

    func testPackagedCatalogueReturnsRealNovelsWithOnlineRequestsStalled() async throws {
        let engine = WKWebEngine(persistent: false)
        engine.pageLoadWait = 0
        let page = try await engine.newPage(profile: "instant-catalog-test")
        #if canImport(AppKit)
        // Match the visible catalogue. Detached macOS WebViews throttle input timers.
        let nativePage = try XCTUnwrap(page as? WKWebPage)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 390, height: 844),
                              styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = nativePage.webView
        window.orderFrontRegardless()
        NSApplication.shared.activate(ignoringOtherApps: true)
        defer { window.orderOut(nil); window.contentView = nil }
        #endif
        await page.addInitScript("""
            window.NovelDBTransport=async details=>{
                if(details.op!=='catalogData')return new Promise(()=>{});
                const response=await fetch('noveldb-local://app/catalog-data?request='+encodeURIComponent(JSON.stringify(details)));
                return response.json();
            };
            """)
        var checkout = URL(fileURLWithPath: #filePath)
        for _ in 0..<5 { checkout.deleteLastPathComponent() }
        let archiveFolder = checkout.appendingPathComponent("assets")
        let installed = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: installed) }
        let versionFolder = installed.appendingPathComponent("fixture")
        try FileManager.default.createDirectory(at: versionFolder, withIntermediateDirectories: true)
        for part in 0..<2 {
            try FileManager.default.createSymbolicLink(at: versionFolder.appendingPathComponent("instant-catalog-\(part).zip"),
                withDestinationURL: archiveFolder.appendingPathComponent("instant-catalog-\(part).zip"))
        }
        try Data("{\"revision\":\"fixture\",\"books\":100,\"built\":\"2026-11-11\"}".utf8)
            .write(to: installed.appendingPathComponent("installed.json"))
        let store = CatalogData(directory: installed)
        await page.serveLocal { path, query in
            if path == "catalog-data",
               let encoded = URLComponents(string: "?" + query)?.queryItems?.first(where: { $0.name == "request" })?.value,
               let data = encoded.data(using: .utf8), let details = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                let reply = await store.handle(details)
                return LocalResponse(mimeType: "application/json", body: Data(reply.utf8))
            }
            guard let file = CatalogPage.file(path), let data = try? Data(contentsOf: file) else { return nil }
            return LocalResponse(mimeType: CatalogPage.mimeType(file.pathExtension), body: data)
        }
        do {
            try await page.load(URL(string: "noveldb-local://app/index.html")!, until: .load, timeout: 30)
            struct Result: Decodable { let ready: Bool; let startup: Double; let top: Int; let firstElapsed: Double; let elapsed: Double; let titles: [String] }
            let result = try await page.evaluate(Result.self, """
                const ready=!!window.NovelDBInstant?.ready();
                const startup=performance.getEntriesByName('noveldb-search-ready')[0].startTime;
                const top=document.querySelectorAll('#grid .card .title').length;
                const result=await new Promise(resolve=>{
                    const query=document.getElementById('query');
                    const started=performance.now();
                    let firstElapsed=null;
                    const observer=new MutationObserver(()=>{
                        const titles=Array.from(document.querySelectorAll('#grid .card .title')).map(node=>node.textContent);
                        if(!document.getElementById('heading').textContent.includes('mesugaki'))return;
                        if(firstElapsed==null&&titles.length>=1)firstElapsed=performance.now()-started;
                        if(titles.length>=20){
                            observer.disconnect();clearTimeout(timer);
                            resolve({firstElapsed,elapsed:performance.now()-started,titles});
                        }
                    });
                    observer.observe(document.getElementById('grid'),{childList:true,subtree:true});
                    const timer=setTimeout(()=>{
                        observer.disconnect();
                        resolve({firstElapsed:firstElapsed??5000,elapsed:5000,
                            titles:Array.from(document.querySelectorAll('#grid .card .title')).map(node=>node.textContent)});
                    },5000);
                    query.value='mesugaki';query.dispatchEvent(new Event('input',{bubbles:true}));
                });
                return {ready,startup,top,...result};
                """)
            print("INSTANT-CATALOG WebKit cold startup \(result.startup) ms, first-frame \(result.firstElapsed) ms, expanded \(result.elapsed) ms, \(result.titles.count) novels, Top \(result.top)")
            XCTAssertTrue(result.ready)
            XCTAssertLessThanOrEqual(result.top, 12)
            XCTAssertLessThan(result.firstElapsed, 60)
            // The initial response has a 60 ms budget. More results arrive in the background.
            XCTAssertLessThan(result.elapsed, 2000)
            XCTAssertGreaterThanOrEqual(result.titles.count, 20)
            XCTAssertLessThanOrEqual(result.titles.count, 30)
            XCTAssertEqual(result.titles.first, "Mesugaki Tank Enters The Academy")
            XCTAssertTrue(result.titles.allSatisfy { $0.lowercased().contains("mesugaki") })
            await page.close()
        } catch {
            await page.close()
            throw error
        }
    }
}
