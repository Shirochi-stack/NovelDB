"""Site engines beyond the built-in scrapers and the novel-downloader/WebToEpub bridges.

- `page_engines`: the LNReader plugins and HaruNeko websites, which run inside the browser page.
- `lncrawl_engine`: lightnovel-crawler's Python sources.
- `fanficfare_engine`: FanFicFare's adapters (fan-fiction archives).
- `gallerydl_engine`: gallery-dl's manga and chapter extractors (manga sites).
- `generic`: the generic fallback that reads any site's metadata, chapter list and chapter text.
- `scraper_mixin`: how `ExternalScraper` drives them.

Every engine returns the shapes the novel-downloader bridge uses: a book dict (`bookname`, `author`,
`introduction`, `coverUrl`, `tags`, `chapters` of `{url, name, ...}`) and a chapter dict
(`chapterName`, `contentHtml`, `contentText`), so the download pipeline treats them all alike.
"""
