from urllib.parse import urlsplit

import pytest

from tests.test_reader_toc_browser import browser, MOBILE


@pytest.mark.parametrize("assets", MOBILE, ids=["android", "ios"])
def test_mobile_google_dorks_reject_unrelated_titles_and_profiles(browser, assets):
    with browser.new_context() as context:
        page = context.new_page()
        def serve(route):
            name = urlsplit(route.request.url).path.rsplit("/", 1)[-1]
            if name == "generic_sites.json":
                route.fulfill(json={"sites": [{"host": "books.test", "site": "books.test"}] +
                                     [{"host": f"obscure{i}.test", "site": f"obscure{i}.test"} for i in range(100)]})
            elif name == "search_profile.json":
                route.fulfill(json={"preferred": ["books.test"], "measurements": {}})
            elif name in {"index.html", "catalog-start.js", "generic.js", "generic-lib.js", "catalog-app.js"}:
                route.fulfill(path=assets / name)
            elif name.endswith(".json"):
                route.fulfill(json={})
            else:
                route.fulfill(body="")
        page.route("**/*", serve)
        page.add_init_script("""localStorage.setItem('noveldb.generic.searches',JSON.stringify({noveldb:false,deep:false}));
            window.requests=[];
            window.NovelDBTransport=request=>{
                requests.push(request);
                const url=new URL(request.url||'https://empty.test/');
                let html='';
                if(url.hostname==='www.google.com')html=`
                    <a href="https://books.test/book/shadow-slave"><h3>Shadow Slave</h3></a>
                    <a href="https://books.test/book/shadow-warrior"><h3>Shadow Warrior</h3></a>
                    <a href="https://books.test/profile/123"><h3>Shadow Slave</h3></a>`;
                else if(url.hostname==='books.test')html=`<html><head><title>Shadow Slave</title>
                    <meta property="og:title" content="Shadow Slave"><meta property="og:image" content="https://books.test/cover.jpg"></head>
                    <body><main><h1>Shadow Slave</h1><p>A long adventure story about a shadow.</p></main></body></html>`;
                return Promise.resolve({status:200,finalUrl:url.href,responseText:html});
            };""")
        page.goto("https://catalog.test/index.html")
        page.fill("#query", "Shadow Slave")
        page.locator("#form").dispatch_event("submit")
        page.wait_for_function("document.querySelectorAll('#grid .card').length===1")
        assert page.locator("#grid .card .title").inner_text() == "Shadow Slave"
        page.wait_for_timeout(300)
        requests = page.evaluate("requests.filter(request=>request.url).map(request=>request.url)")
        google = [url for url in requests if urlsplit(url).hostname == "www.google.com"]
        assert google and all("-inurl%3Aprofile" in url and "site%3Abooks.test" in google[0] for url in google)
        assert not any("obscure" in url for url in requests)
        assert page.locator("#grid .card").count() == 1
