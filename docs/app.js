(() => {
    "use strict";

    // === Tag Translation (Korean → English) ===
    const TAG_MAP = {
        // === Novelpia (Korean) ===
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
        // === Additional Novelpia Tags ===
        "NTR": "NTR", "여성향": "Female-oriented", "연재중": "Ongoing",
        "사이다": "Satisfying/Cathartic", "우울": "Melancholy", "완결": "Completed",
        "19": "R-19", "성전환": "Gender Swap", "감동": "Emotional",
        "능욕": "Violation", "강간": "Assault", "VRMMORPG": "VRMMORPG",
        "소년만화": "Shounen", "동양": "Eastern", "촉수": "Tentacle",
        "잔인": "Cruel/Gory", "귀족": "Aristocrat", "하극상": "Subordinate Uprising",
        "스릴러": "Thriller", "글러먹은판타지": "Cursed Fantasy",
        "나루토": "Naruto", "마인크래프트": "Minecraft", "건담": "Gundam",
        "체인소맨": "Chainsaw Man", "전략": "Strategy", "정주행추천": "Binge-worthy",
        "연인": "Lovers", "고뢰": "Torture", "암살": "Assassination",
        "약NTR": "Soft NTR", "사도": "Sadism", "공략": "Conquest",
        "의사": "Doctor", "황제": "Emperor", "왕자": "Prince",
        "공주": "Princess", "대모험": "Grand Adventure", "감금": "Confinement",
        "변태": "Pervert", "마피아": "Mafia", "선배": "Senior/Senpai",
        "후배": "Junior/Kouhai", "호랑이": "Tiger", "관찰": "Observation",
        "세뇌": "Brainwashing", "최면": "Hypnosis", "조련": "Taming",
        "오버로드": "Overlord", "용사물": "Hero Story", "영성": "Spirituality",
        "갑옷": "Armor", "연금술": "Alchemy", "흡수": "Absorption",
        "쉬운문장": "Easy Reading", "미래": "Future", "우주": "Space",
        "타임슬립": "Time Slip", "수치": "Humiliation", "3인칭": "3rd Person",
        "창작": "Original", "약얀데레": "Soft Yandere", "집유": "Home Parody",
        "봉인": "Sealed", "상실": "Loss", "여학생": "Female Student",
        "대리만족": "Vicarious Satisfaction", "의뢰": "Commission/Quest",
        "해적": "Pirate", "니케": "Nikke", "스텔라": "Stellar",
        "마스터": "Master", "BL물": "BL Story", "꼬마": "Child",
        "왕녀": "Princess(Royal)", "폭력": "Violence", "레이드": "Raid",
        "도적": "Thief/Rogue", "약착각": "Soft Misunderstanding",
        "긴장감": "Tension/Suspense", "논리": "Logic",
        "재회": "Reunion", "동인": "Doujin", "비정기연재": "Irregular Updates",
        "1인칭": "1st Person", "백합물": "Yuri Story", "왕도": "Mainstream/Classic",
        "음악": "Music", "뱀파이어": "Vampire", "야구": "Baseball",
        "츤데레": "Tsundere", "각성": "Awakening", "퓨전판타지": "Fusion Fantasy",
        "NTS": "NTS", "축구": "Football/Soccer", "착각계": "Misunderstanding-type",
        "메이드": "Maid", "크툴루": "Cthulhu", "악당": "Villain",
        "여장": "Cross-dressing(M)", "네크로맨서": "Necromancer",
        "역사": "History", "서큐버스": "Succubus", "크로스오버": "Crossover",
        "멜로": "Melodrama", "천마": "Heavenly Demon", "요괴": "Yokai/Monster",
        "근미래": "Near-future", "역하렘": "Reverse Harem", "기억상실": "Amnesia",
        "동양판타지": "Eastern Fantasy", "코즈믹호러": "Cosmic Horror",
        "오컬트": "Occult", "데스게임": "Death Game", "용병": "Mercenary",
        "서바이벌": "Survival", "악역영애": "Villainess", "천사": "Angel",
        "군상극": "Ensemble Drama", "엑스트라": "Extra/Background Character",
        "다크히어로": "Dark Hero", "변신": "Transformation",
        "탐정": "Detective", "종말": "Apocalypse/End", "우정": "Friendship",
        "캠퍼스": "Campus", "여신": "Goddess", "중세판타지": "Medieval Fantasy",
        "괴수": "Kaiju/Monster", "프로게이머": "Pro Gamer",
        // === KakaoPage (Korean) ===
        "로판": "Romance Fantasy", "현판": "Modern Fantasy", "BL": "Boys' Love",
        // === SFACG (Chinese) ===
        "奇幻": "Fantasy", "玄幻": "Xuanhuan", "魔幻": "Magic Fantasy",
        "都市": "Urban", "科幻": "Sci-Fi", "末世": "Apocalypse",
        "武侠": "Wuxia", "仙侠": "Xianxia", "游戏": "Game",
        "竞技": "Sports/Competition", "变身": "Transformation",
        "百合": "Yuri/GL", "悬疑": "Suspense", "灵异": "Supernatural",
        "历史": "History", "军事": "Military", "二次元": "ACG/Otaku",
        "轻小说": "Light Novel", "同人": "Fan Fiction", "其他": "Other",
        "恋爱": "Romance", "后宫": "Harem", "冒险": "Adventure",
        "搞笑": "Comedy", "热血": "Action/Hot-blooded", "战争": "War",
        "异世界": "Isekai", "穿越": "Transmigration", "重生": "Rebirth",
        "系统": "System", "校园": "School", "日常": "Slice of Life",
        "种田": "Farming/Building", "推理": "Mystery/Detective",
        "恐怖": "Horror", "治愈": "Healing", "复仇": "Revenge",
        "职场": "Workplace", "青春": "Youth", "机甲": "Mecha",
        "末日": "Doomsday", "丧尸": "Zombie", "赛博朋克": "Cyberpunk",
        "星际": "Interstellar", "无限流": "Infinite Flow",
        "克苏鲁": "Cthulhu", "废土": "Wasteland",
        "诡秘": "Occult/Mystery", "修仙": "Cultivation",
        "升级": "Level Up", "异能": "Superpowers",
        "召唤": "Summoning", "魔法": "Magic", "龙": "Dragon",
        "吸血鬼": "Vampire", "狼人": "Werewolf", "精灵": "Elf",
        "骑士": "Knight", "魔王": "Demon King", "勇者": "Hero",
        "女主": "Female MC", "男主": "Male MC", "群像": "Ensemble Cast",
        "反派": "Villain", "无敌": "Invincible", "天才": "Genius",
        "养成": "Raising/Training", "惊悚": "Thriller",
        "奶爸": "Doting Father", "萌宠": "Cute Pets",
        "甜宠": "Sweet Romance", "豪门": "Elite Family",
        "兄妹": "Siblings", "姐弟": "Older Sister",
        "总裁": "CEO", "明星": "Celebrity", "电竞": "Esports",
        "经营": "Management", "直播": "Live Streaming",
        "网游": "Online Game", "宫斗": "Palace Intrigue",
        "宅斗": "Family Intrigue", "古风": "Ancient Style",
        "权谋": "Political Intrigue", "策略": "Strategy",
        "耽美": "BL/Danmei", "纯爱": "Pure Love",
        "暗恋": "Secret Crush", "虐恋": "Tragic Love",
        "战斗": "Combat", "女性主角": "Female MC",
        "脑洞": "Brain-hole/Creative", "倒追": "Reverse Chase",
        "现实": "Realistic", "西幻": "Western Fantasy",
        "橘味": "GL/Yuri", "致郁": "Depressing/Dark",
        "综漫": "Multi-Anime Crossover", "嫁人": "Marriage",
        "分支小说": "Branching Novel", "魔王勇者": "Demon King & Hero",
        "神话": "Mythology", "运动": "Sports",
        "魔法少女": "Magical Girl", "超级英雄": "Superhero",
        "追夫": "Chasing Husband", "多女主": "Multiple Heroines",
        "救赎": "Redemption", "魔女": "Witch",
        "童话": "Fairy Tale", "数据": "Data/Numbers",
        "轻松": "Light-hearted", "蒸朋": "Steampunk",
        "大小姐": "Ojou-sama", "女尊": "Matriarchy",
        "怪谈": "Ghost Stories", "爽文": "Power Fantasy",
        "凤傲天": "Mary Sue", "玩梗": "Meme/References",
        "女武神": "Valkyrie", "刀客塔": "Arknights",
        "师尊": "Master/Shifu", "约战": "Date A Live",
        "女帝": "Empress", "单女主": "Single Heroine",
        "硬核": "Hardcore", "舰长": "Captain/Honkai",
        "暗黑": "Dark", "迪化": "Mastermind Schemer",
        "SCP": "SCP Foundation", "后悔": "Regret",
        "黑化": "Blackened/Corrupted", "型月": "Type-Moon/Fate",
        "假面骑士": "Kamen Rider", "配角": "Side Character",
        "退队": "Left the Party", "火影": "Naruto",
        "时间回溯": "Time Rewind", "舰娘": "Kantai Collection",
        "幻想乡": "Gensokyo/Touhou", "卧底": "Undercover",
        "神奇宝贝": "Pokémon", "修罗场": "Love Triangle Drama",
        "卡牌": "Card Game", "海贼": "One Piece",
        "人生赢家": "Life Winner", "枪娘": "Girls' Frontline",
        "单身": "Single", "废柴": "Useless/Loser MC",
        "柯南": "Detective Conan", "死神": "Bleach",
        "骨王": "Overlord", "医生": "Doctor",
        "女性向": "Female-oriented", "冒险类": "Adventure-type",
    };

    function tl(tag) { return TAG_MAP[tag] || tag; }

    // === State ===
    let allNovels = [];
    let titleTranslations = {};  // id -> english title
    let filtered = [];
    let activeTags = new Set();
    let excludeTags = new Set();
    let tagMode = "AND";
    let displayCount = 32;
    let BATCH = 32;
    let currentPage = 1;

    // === DOM refs ===
    const $ = (sel) => document.querySelector(sel);
    const searchInput = $("#searchInput");
    const sortSelect = $("#sortSelect");
    const orderSelect = $("#orderSelect");
    const tagContainer = $("#tagContainer");
    const excludeTagContainer = $("#excludeTagContainer");
    const resultsEl = $("#results");
    const resultCount = $("#resultCount");
    const paginationBars = [$("#paginationTop"), $("#paginationBottom")];
    const tagModeAnd = $("#tagModeAnd");
    const tagModeOr = $("#tagModeOr");
    const clearTagsBtn = $("#clearTags");
    const clearExcludeBtn = $("#clearExclude");
    const batchSelect = $("#batchSelect");
    const statusSelect = $("#statusSelect");
    const audienceSelect = $("#audienceSelect");

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
        excludeTagContainer.innerHTML = "";
        for (const [tag, count] of sorted) {
            // Include chip
            const chip = document.createElement("span");
            chip.className = "tag-chip";
            chip.textContent = `${tl(tag)} (${fmt(count)})`;
            chip.title = tag;
            chip.dataset.tag = tag;
            chip.addEventListener("click", () => toggleTag(tag, chip));
            tagContainer.appendChild(chip);

            // Exclude chip
            const echip = document.createElement("span");
            echip.className = "tag-chip";
            echip.textContent = `${tl(tag)} (${fmt(count)})`;
            echip.title = tag;
            echip.dataset.tag = tag;
            echip.addEventListener("click", () => toggleExcludeTag(tag, echip));
            excludeTagContainer.appendChild(echip);
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

    function toggleExcludeTag(tag, chip) {
        if (excludeTags.has(tag)) {
            excludeTags.delete(tag);
            chip.classList.remove("excluded");
        } else {
            excludeTags.add(tag);
            chip.classList.add("excluded");
        }
        applyFilters();
    }

    // Add a tag to include filter (from card click)
    function addIncludeTag(tag) {
        if (activeTags.has(tag)) return;
        activeTags.add(tag);
        // Highlight the chip in the tag grid
        tagContainer.querySelectorAll(".tag-chip").forEach((c) => {
            if (c.dataset.tag === tag) c.classList.add("active");
        });
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
                const inTitleEn = n.titleEn && n.titleEn.toLowerCase().includes(query);
                const inAuthor = n.author.toLowerCase().includes(query);
                const inId = String(n.id) === query;
                if (!inTitle && !inTitleEn && !inAuthor && !inId) return false;
            }

            // Status filter
            const status = statusSelect.value;
            if (status === "complete" && !n.complete) return false;
            if (status === "ongoing" && n.complete) return false;

            // Audience filter
            const audience = audienceSelect.value;
            if (audience === "adult" && n.age !== 19) return false;
            if (audience === "general" && n.age === 19) return false;

            // Tag filter (include)
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

            // Tag filter (exclude)
            if (excludeTags.size > 0) {
                for (const t of excludeTags) {
                    if (n.tags.includes(t)) return false;
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
                case "weekly":
                    // Ranked novels first (lower rank = better), unranked last
                    const ra = a.weeklyRank || 9999;
                    const rb = b.weeklyRank || 9999;
                    if (ra !== rb) return order === "asc" ? rb - ra : ra - rb;
                    return b.views - a.views; // tie-break by views
                default: va = a.views; vb = b.views;
            }
            if (sortBy === "title" || sortBy === "updated") {
                const cmp = String(va).localeCompare(String(vb));
                return order === "asc" ? cmp : -cmp;
            }
            return order === "asc" ? va - vb : vb - va;
        });

        displayCount = BATCH;
        currentPage = 1;
        render();
    }

    // === Render Cards ===

    function renderCard(n) {
        const card = document.createElement("a");
        card.className = "novel-card";
        const cfg = SOURCES[currentSource] || SOURCES.novelpia;
        card.href = `${cfg.linkPrefix}${n.id}`;
        card.target = "_blank";
        card.rel = "noopener";

        // Novelpia fallback covers
        const NPIA_COVER_R19 = "https://images.novelpia.com/img/novel/adult_cover_img.jpg";
        const NPIA_COVER_DEFAULT = "https://images.novelpia.com/img/layout/readycover4.png";

        let coverSrc = n.cover;
        if ((!coverSrc || coverSrc === "") && currentSource === "novelpia") {
            coverSrc = (n.age === 19) ? NPIA_COVER_R19 : NPIA_COVER_DEFAULT;
        }
        const fallbackSrc = (currentSource === "novelpia")
            ? ((n.age === 19) ? NPIA_COVER_R19 : NPIA_COVER_DEFAULT)
            : "";

        const coverHTML = coverSrc
            ? `<img class="card-cover" src="${escHtml(coverSrc)}" alt="" decoding="async" onerror="${fallbackSrc ? `this.onerror=null;this.src='${fallbackSrc}'` : `this.outerHTML='<div class=\\'card-cover no-img\\'>📖</div>'`}">`
            : `<div class="card-cover no-img">📖</div>`;

        const badgeHTML = n.weeklyRank
            ? `<span class="card-badge badge-rank">#${n.weeklyRank}</span>`
            : n.complete
            ? `<span class="card-badge badge-complete">Complete</span>`
            : "";

        const tagsHTML = n.tags
            .slice(0, 6)
            .map((t) => `<span class="card-tag" title="${escHtml(t)}" data-tag="${escHtml(t)}">${escHtml(tl(t))}</span>`)
            .join("");

        const ageLabel = (currentSource === "sfacg") ? "15" : "19";
        const r19Badge = (n.age === 19) ? `<span class="badge-r19">${ageLabel}</span>` : "";

        card.innerHTML = `
            <div class="card-cover-wrap">
                ${coverHTML}
                ${r19Badge}
                ${badgeHTML}
            </div>
            <div class="card-body">
                <div class="card-title">${n.titleEn ? escHtml(n.titleEn) : escHtml(n.title)}</div>
                ${n.titleEn ? `<div class="card-title-kr">${escHtml(n.title)}</div>` : ""}
                <div class="card-author">${escHtml(n.author)}</div>
                <div class="card-tags">${tagsHTML}</div>
                <div class="card-stats">
                    <span class="stat">👁 ${fmt(n.views)}</span>
                    <span class="stat">❤ ${fmt(n.likes)}</span>
                    <span class="stat">📄 ${fmt(n.chapters)}</span>
                </div>
            </div>
        `;

        // Make card tags clickable (add to include filter)
        card.querySelectorAll(".card-tag").forEach((tagEl) => {
            tagEl.addEventListener("click", (e) => {
                e.preventDefault();
                e.stopPropagation();
                addIncludeTag(tagEl.dataset.tag);
            });
        });

        return card;
    }

    function render() {
        const totalPages = Math.max(1, Math.ceil(filtered.length / BATCH));
        if (currentPage > totalPages) currentPage = totalPages;

        const start = (currentPage - 1) * BATCH;
        const end = Math.min(start + BATCH, filtered.length);

        resultCount.textContent = `${filtered.length.toLocaleString()} novel(s) found — page ${currentPage} of ${totalPages}`;

        resultsEl.innerHTML = "";
        for (let i = start; i < end; i++) {
            resultsEl.appendChild(renderCard(filtered[i]));
        }

        // Update both pagination bars
        const show = totalPages > 1;
        const pages = buildPageRange(currentPage, totalPages);
        for (const bar of paginationBars) {
            bar.style.display = show ? "" : "none";
            bar.querySelector(".prev-page").disabled = currentPage === 1;
            bar.querySelector(".next-page").disabled = currentPage === totalPages;

            const numsEl = bar.querySelector(".page-numbers");
            numsEl.innerHTML = "";
            for (const p of pages) {
                if (p === "...") {
                    const span = document.createElement("span");
                    span.className = "page-ellipsis";
                    span.textContent = "...";
                    numsEl.appendChild(span);
                } else {
                    const btn = document.createElement("button");
                    btn.className = "page-btn" + (p === currentPage ? " active" : "");
                    btn.textContent = p;
                    btn.addEventListener("click", () => {
                        currentPage = p;
                        render();
                        window.scrollTo({ top: resultsEl.offsetTop - 80, behavior: "smooth" });
                    });
                    numsEl.appendChild(btn);
                }
            }
        }
    }

    function buildPageRange(current, total) {
        // Show: 1 ... [current-2..current+2] ... last
        const delta = 2;
        const pages = [];
        const rangeStart = Math.max(2, current - delta);
        const rangeEnd = Math.min(total - 1, current + delta);

        pages.push(1);
        if (rangeStart > 2) pages.push("...");
        for (let i = rangeStart; i <= rangeEnd; i++) pages.push(i);
        if (rangeEnd < total - 1) pages.push("...");
        if (total > 1) pages.push(total);

        return pages;
    }

    function escHtml(s) {
        if (!s) return "";
        return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    // === Tag section toggle ===
    function setupToggle(headerId, containerId) {
        const header = document.getElementById(headerId);
        const container = document.getElementById(containerId);
        header.addEventListener("click", (e) => {
            // Don't toggle when clicking buttons inside the header
            if (e.target.closest("button")) return;
            const collapsed = container.classList.toggle("collapsed");
            const label = header.querySelector("span");
            label.textContent = label.textContent.replace(/^[▶▼]/, collapsed ? "▶" : "▼");
        });
    }
    setupToggle("tagToggle", "tagContainer");
    setupToggle("excludeToggle", "excludeTagContainer");

    // === Event Listeners ===
    let searchTimer;
    searchInput.addEventListener("input", () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(applyFilters, 200);
    });

    sortSelect.addEventListener("change", applyFilters);
    orderSelect.addEventListener("change", applyFilters);
    statusSelect.addEventListener("change", applyFilters);
    audienceSelect.addEventListener("change", applyFilters);

    batchSelect.addEventListener("change", () => {
        BATCH = parseInt(batchSelect.value);
        currentPage = 1;
        render();
    });

    for (const bar of paginationBars) {
        bar.querySelector(".prev-page").addEventListener("click", () => {
            if (currentPage > 1) {
                currentPage--;
                render();
                window.scrollTo({ top: resultsEl.offsetTop - 80, behavior: "smooth" });
            }
        });
        bar.querySelector(".next-page").addEventListener("click", () => {
            const totalPages = Math.ceil(filtered.length / BATCH);
            if (currentPage < totalPages) {
                currentPage++;
                render();
                window.scrollTo({ top: resultsEl.offsetTop - 80, behavior: "smooth" });
            }
        });
    }

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

    clearExcludeBtn.addEventListener("click", () => {
        excludeTags.clear();
        excludeTagContainer.querySelectorAll(".tag-chip.excluded").forEach((c) => c.classList.remove("excluded"));
        applyFilters();
    });

    // === DOM ref ===
    const sourceSelect = $("#sourceSelect");

    // === Source configs ===
    const SOURCES = {
        novelpia: {
            dataUrl: "data/novels.json",
            translationsUrl: "data/titles_en.txt",
            format: "array",  // optimized array format
            coverPrefix: "https://novelpia.com",
            linkPrefix: "https://novelpia.com/novel/",
        },
        kakao: {
            dataUrl: "data/kakao_novels.json",
            translationsUrl: "data/kakao_titles_en.txt",
            format: "array",
            coverPrefix: "",  // full URLs in data
            linkPrefix: "https://page.kakao.com/content/",
        },
        sfacg: {
            dataUrl: "data/sfacg_novels.json",
            translationsUrl: "data/sfacg_titles_en.txt",
            format: "array",
            coverPrefix: "",  // full URLs in data
            linkPrefix: "https://book.sfacg.com/Novel/",
        },
    };

    let currentSource = "novelpia";

    async function fetchWithProgress(url) {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        const contentLength = resp.headers.get("content-length");
        if (contentLength && resp.body) {
            const total = parseInt(contentLength);
            const reader = resp.body.getReader();
            const chunks = [];
            let received = 0;
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                chunks.push(value);
                received += value.length;
                const mb = (received / 1024 / 1024).toFixed(1);
                const totalMb = (total / 1024 / 1024).toFixed(1);
                const pct = Math.round((received / total) * 100);
                resultsEl.innerHTML = `<div class="loading-spinner">Downloading... ${mb}/${totalMb} MB (${pct}%)</div>`;
            }
            const blob = new Blob(chunks);
            resultsEl.innerHTML = `<div class="loading-spinner">Parsing ${(received / 1024 / 1024).toFixed(1)} MB...</div>`;
            await new Promise((r) => setTimeout(r, 50));
            return JSON.parse(await blob.text());
        } else {
            return await resp.json();
        }
    }

    async function loadSource(source) {
        currentSource = source;
        const cfg = SOURCES[source];
        resultsEl.innerHTML = `<div class="loading-spinner">Loading ${source} database...</div>`;
        for (const bar of paginationBars) bar.style.display = "none";

        // Clear filters
        activeTags.clear();
        excludeTags.clear();
        searchInput.value = "";
        currentPage = 1;

        try {
            const raw = await fetchWithProgress(cfg.dataUrl);

            allNovels = raw.map((r) => {
                let tags = r[4];
                if (!Array.isArray(tags)) tags = tags ? Object.values(tags) : [];
                return {
                    id: r[0],
                    title: r[1] || "",
                    author: r[2] || "",
                    cover: r[3] ? (cfg.coverPrefix && !r[3].startsWith("http") ? cfg.coverPrefix + r[3] : r[3]) : "",
                    tags,
                    views: r[5] || 0,
                    likes: r[6] || 0,
                    chapters: r[7] || 0,
                    complete: r[8] || 0,
                    updated: r[9] || "",
                    weeklyRank: r[10] || 0,
                    age: r[11] || 0,
                    titleEn: "",
                    source: source,
                };
            });

            // Load title translations if available
            titleTranslations = {};
            try {
                const tResp = await fetch(cfg.translationsUrl);
                if (tResp.ok) {
                    const text = await tResp.text();
                    let count = 0;
                    for (const line of text.split("\n")) {
                        const parts = line.split("|||");
                        if (parts.length >= 3 && parts[2].trim()) {
                            titleTranslations[parts[0]] = parts[2].trim();
                            count++;
                        }
                    }
                    if (count > 0) {
                        for (const n of allNovels) {
                            const en = titleTranslations[String(n.id)];
                            if (en) n.titleEn = en;
                        }
                        console.log(`Loaded ${count} title translations for ${source}`);
                    }
                }
            } catch (e) {
                // translations not found, skip
            }

            buildTags(allNovels);
            applyFilters();
        } catch (err) {
            console.error("Load error:", err);
            resultsEl.innerHTML = `<div class="loading-spinner" style="animation:none">
                ❌ Failed to load ${source} data.<br>
                <small style="color:var(--text-muted)">${err.message}</small>
            </div>`;
        }
    }

    // Source change listener
    sourceSelect.addEventListener("change", () => {
        loadSource(sourceSelect.value);
    });

    loadSource("novelpia");
})();
