# NovelpiaDownloader

A fork of that enhances the user experience and output quality. This version adds comprehensive metadata (tags, author, synopsis), improves EPUB formatting with HTML tag and newline support, includes file size optimization, and much more\!

-----

## 📚 Table of Contents

  - [✨ Features]
  - [🌐 External Novel Downloader](#-external-novel-downloader)
  - [🚀 Usage]
  - [💾 Space-Saving Tips]
  - [🛠️ Command-Line Arguments]
  - [❓ FAQ (Frequently Asked Questions]
  - [📜 Legal & Disclaimer]

-----

## ✨ Features

  - **Rich Metadata:** Automatically grabs and embeds tags, author names, and synopses into the EPUB file metadata.
  - **Improved EPUB Formatting:** Supports HTML tags and newlines, preserving the original formatting of the novel.
  - **File Size Optimization:** Includes WebP / AVIF / PNG / JPEG image compression to significantly reduce the final file size without a noticeable loss in quality. (AVIF requires the optional `pillow-avif-plugin` package; see `requirements.txt`.)
  - **Command-Line Interface:** Offers a robust command-line interface for automated and scripted downloads.
  - **Bulk Downloads:** Allows you to easily redownload your library to fix formatting, optimize file sizes, and more. (Format is ``outputname, id`` with each novel on a new line.) 
  - **Improved Downloads:** Offers one-click downloads, with novels automatically named and placed in whichever directory you choose. Also includes auto-retries and error detection.
  - **Author Notices Support:** Download author notices and illustrations ! 

<img width="880" height="698" alt="image" src="https://github.com/user-attachments/assets/81b5a264-cc22-4f82-8a4b-341d342c9fc3" />

-----

## 🌐 External Novel Downloader

In addition to Novelpia, NpiaDownloader supports downloading novels from **100+ external sites** via the built-in **External Novel** button. This feature is powered by the [novel-downloader](https://github.com/404-novel-project/novel-downloader) project's rule engine.

### Supported Sites (partial list)

| Platform | URL |
|---|---|
| Kakuyomu | kakuyomu.jp |
| Syosetu (なろう) | ncode.syosetu.com |
| Pixiv Novel | novel.pixiv.net |
| Qidian | book.qidian.com |
| JJWXC | jjwxc.net |
| Kakao Page | page.kakao.com |
| SFACG | book.sfacg.com |
| Hameln | syosetu.org |
| …and 90+ more | See [novel-downloader](https://github.com/404-novel-project/novel-downloader) |

### How it works

1. Click **External Novel** in the main window.
2. Paste the novel URL and click **Fetch Info**.
3. Choose one or more formats (EPUB / TXT / PDF / CBZ), the chapter range, and thread count.
4. Click **Download**.

The scraper uses a headless Chromium browser with the novel-downloader rules injected at runtime. Login sessions are persistent — use the **Enter Browser** button to log in to sites that require authentication, and your cookies will be reused for all future downloads.

### Updating Rules

The site-specific scraping rules are compiled from the upstream [novel-downloader](https://github.com/404-novel-project/novel-downloader) project. To update to the latest rules, run `update_rules.bat` in the project root. This clones/pulls the upstream repo, applies a bridge-mode patch, builds via webpack, and copies the output as `rules-lib.js`.


## 🚀 Usage

To download paid chapters, you'll need a `LOGINKEY`. You can get it by logging into your Novelpia account in a web browser, opening the developer tools (F12), and navigating to the **Storage** tab. Copy the value of your `LOGINKEY` from there.
*(You must have access to the content that you intend to download on your account.)*

A higher thread count and a lower interval can speed up your downloads, but be aware that this increases the risk of an IP ban.
*(Going above 10 threads can cause the website to rate-limit you, resulting in chapters failing to download and eventually a 24-48 hour IP ban.)*
<img width="450" height="42" alt="image" src="https://github.com/user-attachments/assets/a702e637-1825-4e2c-923c-94def6ef06d0" />

-----

## 💾 Space-Saving Tips

### Image Compression

Built-in image compression can dramatically reduce file size. Use the `-compressimages` and `-jpegquality` arguments to enable this feature. (Or the checkbox)
**Image Compression Workers** only controls concurrent image compression; chapter scraping still uses **Threads**.

  - **80% Quality:** Provides large savings with no noticeable quality difference (e.g., 1MB -\> 65KB).
  - **50% Quality:** Offers massive savings with only a small difference in quality (e.g., 1MB -\> 30KB).
  - **10-30% Quality:** For extreme savings, though the quality difference will be noticeable (e.g., 1MB -\> \<10KB).
![Comparison of uncompressed and 10% quality compressed images](https://github.com/user-attachments/assets/09161c74-92d8-4b3e-8e72-8ac574db719d)

### Post-Processing with Calibre

For even greater space savings (10-50%), you can use the Calibre EPUB editor. Converting and saving a new `.epub` file with Calibre optimizes the CSS, HTML, and embedded fonts. This is a manual step, as implementing these optimizations directly is currently outside the scope of this project.

-----

## 📦 Batch Download / Paste Batch (GUI)

Two sibling buttons feed the same sequential batch engine:

- **Batch Download** — pick a list file (`.txt` / `.csv`).
- **Paste Batch** — paste the list directly into a dialog window (no file needed).

Both accept the same line formats, one entry per line:

- `NovelID`
- `https://novelpia.com/novel/NovelID` (URL auto-resolved)
- `Title,NovelID` — the title is used in the output filename.
- `Title,https://novelpia.com/novel/NovelID`
- `# comment` — any line beginning with `#` is skipped.

The main **Novel ID / URL** field in the single-download form also accepts a full Novelpia URL — it's resolved to the numeric ID automatically and written back into the field.

**How it works:**

1. Click **Batch Download** (and pick a list file) or **Paste Batch** (and paste entries).
2. If **Quick Download** is enabled, its folder is used as the output directory. Otherwise you'll be prompted for one.
3. Every novel is downloaded with the **current UI settings** — selected format(s) (any combination of EPUB/TXT/PDF), chapter range, compression, image format (WEBP/JPEG/PNG/**AVIF**), cover format, notices, cache, threads, image compression workers, and interval. Configure these **before** pressing the button.
4. Blank lines, `#` comments, and entries that cannot be resolved to a numeric ID are skipped. A 2 second pause is inserted between novels to reduce server load.
5. Pressing **Stop** cancels the batch — the current novel finishes, then the run exits.
6. Each novel is saved as `[<id>] <title>.<ext>` in the chosen folder, with one file per selected format. A final `OK / Failed / Skipped` summary is logged.

**Tip:** you can generate a batch list automatically from **Tag Retrieval** (by tag or Top 100), then feed that file straight into Batch Download, or copy its contents into Paste Batch. An in-app help dialog is also available via the **?** button next to **Batch Download**.

### Image format notes

- The **Format** dropdown under *Compress Images* controls the output format for chapter images.
- **GIFs are preserved as `.gif` by default** so that animation is kept. If you'd rather have GIFs re-encoded to the selected format (WEBP/JPEG/PNG/AVIF, first frame only), tick the **Convert GIFs** checkbox next to the format dropdown. The toggle state is saved automatically to `config.json` (`convert_gifs`).
- The same Format dropdown applies to the cover image via *Compress Cover* → **Format**.
- Selecting **AVIF** requires `pillow-avif-plugin` to be installed; otherwise the downloader automatically falls back to WEBP with a warning in the log.

-----

## 🛠️ Command-Line Arguments

The NovelpiaDownloader can be operated directly from the command line, ideal for automated and scripted downloads.

*(Keep the table of arguments and usage examples exactly as you have them, they are very clear and well-formatted.)*

-----

## ❓ FAQ (Frequently Asked Questions)

Here are some solutions to common problems you might encounter.

**Q: I'm getting an error that says I'm not logged in, but I've entered my LOGINKEY.**
A: Ensure your `LOGINKEY` is still valid. Novelpia keys expire after a period of time. Try logging out and back in on the website, then get a new `LOGINKEY` from the storage tab and use that. Make sure you click the "Log In" button in the application.

**Q: The download process seems to be stuck or is extremely slow.**
A: This could be due to a temporary IP ban from Novelpia's servers, which can happen with a high thread count. Try the following:

1.  Reduce your thread count and increase the interval in the settings.
2.  If the problem persists, wait for a few hours and try again, as the IP ban is usually temporary.
3.  Check your network connection.

**Q: The downloaded file is missing chapters or content.**
A: Double-check the `-from` and `-to` arguments to make sure they cover the desired chapter range. Ensure you have a valid `LOGINKEY` for any paid chapters. Lastly, make sure your account has access to the content you are downloading.

**Q: How do I find the `Novel ID`?**
A: The `Novel ID` is the number in the novel's URL. For example, if the URL is `https://novelpia.com/novel/123456`, the `Novel ID` is `123456`.

**Q: My EPUB reader is throwing errors when I try to open the EPUB.**
A: This can be caused by missing chapters (e.g., R19 chapters being skipped due to account permissions). The easiest fix is to open the EPUB in [Calibre](https://calibre-ebook.com/download) (an open-source e-book & EPUB manager) and convert it to a new EPUB file. This can be done in bulk.

**Q: Epub fails to load still**
A: Use Moon+ Reader, ReadEra, or Calibre. Lithium and some other readers may not work, but this will be fixed in a future update.



-----

## 📜 Legal & Disclaimer

This project is a fork of CjangCjengh's NovelpiaDownloader and is intended for personal use to create backups of content you have legally accessed. I am not affiliated with Novelpia. Please respect their terms of service and copyright laws.

### Credits

- **[novel-downloader](https://github.com/404-novel-project/novel-downloader)** by [404-novel-project](https://github.com/404-novel-project) — Powers the external novel download feature with support for 100+ sites.
- **[NTKDownloader](https://github.com/tyuop077/NTKDownloader/tree/main)** by [tyuop077](https://github.com/tyuop077) — Reference implementation for the NewToki `curl_cffi` Chrome impersonation, `nv` issue flow, HMAC-signed `/api/novel-content` requests, and payload decryption approach used by this project.
- **[CjangCjengh/NovelpiaDownloader](https://github.com/CjangCjengh/NovelpiaDownloader)** — Original Novelpia downloader this project is forked from.

-----
