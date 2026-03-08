(() => {
    "use strict";

    // === State ===
    let allNovels = [];
    let filtered = [];
    let activeTags = new Set();
    let tagMode = "AND";
    let displayCount = 60;
    const BATCH = 60;

    // === DOM refs ===
    const $ = (sel) => document.querySelector(sel);
    const searchInput = $("#searchInput");
    const sortSelect = $("#sortSelect");
    const orderSelect = $("#orderSelect");
    const tagContainer = $("#tagContainer");
    const resultsEl = $("#results");
    const resultCount = $("#resultCount");
    const loadMoreWrap = $("#loadMore");
    const loadMoreBtn = $("#loadMoreBtn");
    const tagModeAnd = $("#tagModeAnd");
    const tagModeOr = $("#tagModeOr");
    const clearTagsBtn = $("#clearTags");

    // === Format numbers ===
    function fmt(n) {
        if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
        if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
        return String(n);
    }

    // === Build tag chips ===
    function buildTags(novels) {
        const counts = {};
        for (const n of novels) {
            for (const t of n.tags) {
                counts[t] = (counts[t] || 0) + 1;
            }
        }
        const sorted = Object.entries(counts)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 80);

        tagContainer.innerHTML = "";
        for (const [tag, count] of sorted) {
            const chip = document.createElement("span");
            chip.className = "tag-chip";
            chip.textContent = `${tag} (${fmt(count)})`;
            chip.dataset.tag = tag;
            chip.addEventListener("click", () => toggleTag(tag, chip));
            tagContainer.appendChild(chip);
        }
    }

    function toggleTag(tag, chip) {
        if (activeTags.has(tag)) {
            activeTags.delete(tag);
            chip.classList.remove("active");
        } else {
            activeTags.add(tag);
            chip.classList.add("active");
        }
        applyFilters();
    }

    // === Filter & Sort ===
    function applyFilters() {
        const query = searchInput.value.trim().toLowerCase();
        const sortBy = sortSelect.value;
        const order = orderSelect.value;

        filtered = allNovels.filter((n) => {
            // Search filter
            if (query) {
                const inTitle = n.title.toLowerCase().includes(query);
                const inAuthor = n.author.toLowerCase().includes(query);
                if (!inTitle && !inAuthor) return false;
            }

            // Tag filter
            if (activeTags.size > 0) {
                const novelTags = new Set(n.tags);
                if (tagMode === "AND") {
                    for (const t of activeTags) {
                        if (!novelTags.has(t)) return false;
                    }
                } else {
                    let match = false;
                    for (const t of activeTags) {
                        if (novelTags.has(t)) { match = true; break; }
                    }
                    if (!match) return false;
                }
            }

            return true;
        });

        // Sort
        filtered.sort((a, b) => {
            let va, vb;
            switch (sortBy) {
                case "views": va = a.views; vb = b.views; break;
                case "likes": va = a.likes; vb = b.likes; break;
                case "chapters": va = a.chapters; vb = b.chapters; break;
                case "updated": va = a.updated; vb = b.updated; break;
                case "title": va = a.title; vb = b.title; break;
                default: va = a.views; vb = b.views;
            }
            if (sortBy === "title" || sortBy === "updated") {
                const cmp = String(va).localeCompare(String(vb));
                return order === "asc" ? cmp : -cmp;
            }
            return order === "asc" ? va - vb : vb - va;
        });

        displayCount = BATCH;
        render();
    }

    // === Render Cards ===
    function render() {
        const slice = filtered.slice(0, displayCount);

        resultCount.textContent = `${filtered.length.toLocaleString()} novel(s) found`;

        resultsEl.innerHTML = "";
        for (const n of slice) {
            const card = document.createElement("a");
            card.className = "novel-card";
            card.href = `https://novelpia.com/novel/${n.id}`;
            card.target = "_blank";
            card.rel = "noopener";

            const coverHTML = n.cover
                ? `<img class="card-cover" src="${escHtml(n.cover)}" alt="" loading="lazy" onerror="this.outerHTML='<div class=\\'card-cover no-img\\'>📖</div>'">`
                : `<div class="card-cover no-img">📖</div>`;

            const badgeHTML = n.complete
                ? `<span class="card-badge badge-complete">Complete</span>`
                : "";

            const tagsHTML = n.tags
                .slice(0, 6)
                .map((t) => `<span class="card-tag">${escHtml(t)}</span>`)
                .join("");

            card.innerHTML = `
                <div class="card-cover-wrap">
                    ${coverHTML}
                    ${badgeHTML}
                </div>
                <div class="card-body">
                    <div class="card-title">${escHtml(n.title)}</div>
                    <div class="card-author">${escHtml(n.author)}</div>
                    <div class="card-tags">${tagsHTML}</div>
                    <div class="card-stats">
                        <span class="stat">👁 ${fmt(n.views)}</span>
                        <span class="stat">❤ ${fmt(n.likes)}</span>
                        <span class="stat">📄 ${fmt(n.chapters)}</span>
                    </div>
                </div>
            `;

            resultsEl.appendChild(card);
        }

        loadMoreWrap.style.display = displayCount < filtered.length ? "" : "none";
    }

    function escHtml(s) {
        if (!s) return "";
        return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    // === Event Listeners ===
    let searchTimer;
    searchInput.addEventListener("input", () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(applyFilters, 200);
    });

    sortSelect.addEventListener("change", applyFilters);
    orderSelect.addEventListener("change", applyFilters);

    loadMoreBtn.addEventListener("click", () => {
        displayCount += BATCH;
        render();
    });

    tagModeAnd.addEventListener("click", () => {
        tagMode = "AND";
        tagModeAnd.classList.add("active");
        tagModeOr.classList.remove("active");
        if (activeTags.size) applyFilters();
    });

    tagModeOr.addEventListener("click", () => {
        tagMode = "OR";
        tagModeOr.classList.add("active");
        tagModeAnd.classList.remove("active");
        if (activeTags.size) applyFilters();
    });

    clearTagsBtn.addEventListener("click", () => {
        activeTags.clear();
        tagContainer.querySelectorAll(".tag-chip.active").forEach((c) => c.classList.remove("active"));
        applyFilters();
    });

    // === Load Data ===
    const COVER_PREFIX = "https://novelpia.com";

    async function init() {
        resultsEl.innerHTML = `<div class="loading-spinner">Loading novel database...</div>`;

        try {
            const resp = await fetch("data/novels.json");
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const raw = await resp.json();

            // Parse array format: [id, title, author, cover, tags, views, likes, chapters, complete, updated]
            allNovels = raw.map((r) => ({
                id: r[0],
                title: r[1],
                author: r[2],
                cover: r[3] ? COVER_PREFIX + r[3] : "",
                tags: r[4],
                views: r[5],
                likes: r[6],
                chapters: r[7],
                complete: r[8],
                updated: r[9],
            }));

            buildTags(allNovels);
            applyFilters();
        } catch (err) {
            resultsEl.innerHTML = `<div class="loading-spinner" style="animation:none">
                ❌ Failed to load data.<br>
                <small style="color:var(--text-muted)">Run <code>python scripts/scrape_npia.py</code> first to generate the data.</small>
            </div>`;
        }
    }

    init();
})();
