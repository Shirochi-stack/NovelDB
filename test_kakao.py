"""Quick test of KakaoPage scraping."""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from external_scraper import ExternalScraper

scraper = ExternalScraper(logger=lambda m: print(m))
scraper.start()

# Test parse_book
book = scraper.parse_book('https://page.kakao.com/content/48787313')
if book:
    print()
    print("Title:", book.get("bookname"))
    print("Author:", book.get("author"))
    print("Chapters:", book.get("chapterCount"))
    cover = book.get("coverUrl", "")
    print("Cover:", cover[:80] if cover else "none")
    chapters = book.get("chapters", [])
    if chapters:
        print("First episode:", chapters[0])
        print("Last episode:", chapters[-1])

    # Test parse_chapter on the first free chapter
    if len(chapters) >= 2:
        ch = chapters[1]  # Episode 1 (index 1, skip prologue)
        print()
        print("Testing chapter parse:", ch.get("name"))
        result = scraper.parse_chapter(1, ch, interval=0.5)
        if result:
            text = result.get("contentText", "")
            print("Text length:", len(text))
            print("First 300 chars:", text[:300])
        else:
            print("FAILED to parse chapter")
else:
    print("FAILED to parse book")

scraper.cleanup()
print("Done.")
