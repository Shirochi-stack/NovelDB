import Foundation
import XCTest
import NpiaCore
@testable import NpiaWebEngine

@MainActor
final class InstantCatalogTests: XCTestCase {
    func testPackagedCatalogueReturnsRealNovelsWithOnlineRequestsStalled() async throws {
        let engine = WKWebEngine(persistent: false)
        let page = try await engine.newPage(profile: "instant-catalog-test")
        await page.addInitScript("window.NovelDBTransport=()=>new Promise(()=>{});")
        await page.serveLocal { path, _ in
            guard let file = CatalogPage.file(path), let data = try? Data(contentsOf: file) else { return nil }
            return LocalResponse(mimeType: CatalogPage.mimeType(file.pathExtension), body: data)
        }
        do {
            try await page.load(URL(string: "noveldb-local://app/index.html")!, until: .load, timeout: 30)
            struct Result: Decodable { let top: Int; let elapsed: Double; let titles: [String] }
            let result = try await page.evaluate(Result.self, """
                let deadline=performance.now()+10000;
                while(document.querySelectorAll('#grid .card .title').length<100&&performance.now()<deadline)
                    await new Promise(resolve=>setTimeout(resolve,10));
                const top=document.querySelectorAll('#grid .card .title').length;
                const query=document.getElementById('query');
                const started=performance.now();
                query.value='mesugaki';query.dispatchEvent(new Event('input',{bubbles:true}));
                let titles=[];
                deadline=performance.now()+5000;
                while(performance.now()<deadline){
                    titles=Array.from(document.querySelectorAll('#grid .card .title')).map(node=>node.textContent);
                    if(document.getElementById('heading').textContent.includes('mesugaki')&&titles.length>=20)break;
                    await new Promise(resolve=>setTimeout(resolve,10));
                }
                return {top,elapsed:performance.now()-started,titles};
                """)
            XCTAssertEqual(result.top, 100)
            XCTAssertLessThan(result.elapsed, 1000)
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
