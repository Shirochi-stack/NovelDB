(() => {
    "use strict";

    // === Tag Translation (Korean → English) ===
    const TAG_MAP = {
        "판타지": "Fantasy", "패러디": "Parody", "현대": "Modern", "라이트노벨": "Light Novel",
        "일상": "Slice of Life", "하렘": "Harem", "먼치킨": "Munchkin/OP MC", "현대판타지": "Modern Fantasy",
        "TS": "Genderbend (TS)", "로맨스": "Romance", "중세": "Medieval", "전생": "Reincarnation",
        "집착": "Obsession", "아카데미": "Academy", "SF": "Sci-Fi", "순애": "Pure Love",
        "드라마": "Drama", "빙의": "Possession", "착각": "Misunderstanding", "성장": "Growth",
        "피폐": "Suffering/Angst", "블루아카이브": "Blue Archive", "무협": "Martial Arts",
        "후회": "Regret", "코미디": "Comedy", "이세계": "Isekai", "기타": "Other",
        "백합": "Yuri/GL", "회귀": "Regression", "약피폐": "Mild Angst", "환생": "Rebirth",
        "게임": "Game", "헌터": "Hunter", "얀데레": "Yandere", "인터넷방송": "Streaming/VTuber",
        "아포칼립스": "Apocalypse", "복수": "Revenge", "가면라이더": "Kamen Rider",
        "대체역사": "Alternate History", "모험": "Adventure", "원신": "Genshin Impact",
        "다크판타지": "Dark Fantasy", "공포": "Horror", "전쟁": "War", "힐링": "Healing",
        "액션": "Action", "다중": "Crossover", "2차창작": "Fan Fiction", "생존": "Survival",
        "상태창": "Status Window", "마법": "Magic", "스포츠": "Sports", "원피스": "One Piece",
        "구원": "Salvation", "용사": "Hero", "인외": "Non-Human", "여주인공": "Female MC",
        "히어로": "Superhero", "팬픽": "Fanfic", "주술회전": "Jujustu Kaisen",
        "러브코미디": "Romcom", "노맨스": "No Romance", "남녀역전": "Gender Role Reversal",
        "성장형먼치킨": "Growth-type OP", "퓨전": "Fusion", "로맨스판타지": "Romance Fantasy",
        "마법소녀": "Magical Girl", "남성향": "Male-oriented", "단편": "Short Story",
        "빌런": "Villain", "천재": "Genius", "사이버펑크": "Cyberpunk", "좀비": "Zombie",
        "갤러리": "Gallery/Forum", "미스터리": "Mystery", "던전": "Dungeon",
        "포켓몬": "Pokémon", "괴담": "Ghost Stories", "성좌": "Constellation",
        "정치": "Politics", "추리": "Detective", "초능력": "Superpower",
        "원작파괴": "Canon Divergence", "나데나데": "Headpats", "밀리터리": "Military",
        "시스템": "System", "탑등반": "Tower Climbing", "영지": "Territory/Domain",
        "경영": "Management", "커뮤니티": "Community", "귀환": "Return",
        "역키잡": "Reverse Gap", "캣파이트": "Catfight", "느린전개": "Slow Burn",
        "육아": "Childcare", "작가": "Writer/Author", "배우": "Actor",
        "아이돌": "Idol", "버튜버": "VTuber", "요리": "Cooking", "고인물": "Veteran",
        "근친": "Incest", "스트리머": "Streamer", "학원": "School",
        "전문가": "Expert", "재벌": "Chaebol/Rich", "탑": "Tower",
        "메카": "Mecha", "드래곤": "Dragon", "기사": "Knight", "마왕": "Demon King",
        "성녀": "Saintess", "수인": "Beastkin", "엘프": "Elf", "흡혈귀": "Vampire",
        "괴이": "Anomaly", "소환": "Summoning", "제작": "Crafting",
        "히로아카": "My Hero Academia", "림버스": "Limbus Company",
        "프문": "Project Moon", "로보토미": "Lobotomy Corp",
        "전독시": "Omniscient Reader", "데어라": "Date A Live",
        "라오루": "Library of Ruina", "블아": "Blue Archive",
        "스페이스오페라": "Space Opera", "포스트아포칼립스": "Post-Apocalypse",
        "고수위": "Mature/R-rated", "조교": "Training",
        "마법사": "Mage/Wizard", "주딱": "Forum Mod", "소설": "Novel",
        "정통판타지": "Classic Fantasy", "어반": "Urban", "어반판타지": "Urban Fantasy",
        "느와르": "Noir", "피카레스크": "Picaresque", "망나니": "Delinquent",
        "흑막": "Mastermind", "삼국지": "Three Kingdoms", "조선": "Joseon Era",
    };

    function tl(tag) { return TAG_MAP[tag] || tag; }

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
            chip.textContent = `${tl(tag)} (${fmt(count)})`;
            chip.title = tag;
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
                const inId = String(n.id) === query;
                if (!inTitle && !inAuthor && !inId) return false;
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
                .map((t) => `<span class="card-tag" title="${escHtml(t)}">${escHtml(tl(t))}</span>`)
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
            allNovels = raw.map((r) => {
                let tags = r[4];
                if (!Array.isArray(tags)) tags = tags ? Object.values(tags) : [];
                return {
                    id: r[0],
                    title: r[1] || "",
                    author: r[2] || "",
                    cover: r[3] ? COVER_PREFIX + r[3] : "",
                    tags,
                    views: r[5] || 0,
                    likes: r[6] || 0,
                    chapters: r[7] || 0,
                    complete: r[8] || 0,
                    updated: r[9] || "",
                };
            });

            buildTags(allNovels);
            applyFilters();
        } catch (err) {
            console.error("Load error:", err);
            resultsEl.innerHTML = `<div class="loading-spinner" style="animation:none">
                ❌ Failed to load data.<br>
                <small style="color:var(--text-muted)">${err.message}</small>
            </div>`;
        }
    }

    init();
})();
