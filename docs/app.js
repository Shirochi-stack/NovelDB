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
        // === Additional Novelpia Tags (IPs & Franchises) ===
        "FATE": "Fate", "FGO": "Fate/Grand Order", "페이트": "Fate", "페그오": "Fate/GO",
        "나루토": "Naruto", "블루아카": "Blue Archive", "원피스": "One Piece",
        "귀멸의칼날": "Demon Slayer", "귀칼": "Demon Slayer",
        "동방": "Touhou", "동방프로젝트": "Touhou Project",
        "유희왕": "Yu-Gi-Oh!", "포켓몬스터": "Pokemon", "블리치": "Bleach",
        "죠죠": "JoJo", "죠죠의기묘한모험": "JoJo's Bizarre Adventure",
        "붕괴3RD": "Honkai Impact 3rd", "붕괴": "Honkai", "붕스": "Honkai: Star Rail",
        "명일방주": "Arknights", "명방": "Arknights",
        "홀로라이브": "Hololive", "소녀전선": "Girls' Frontline", "소전": "Girls' Frontline",
        "벽람항로": "Azur Lane", "벽람": "Azur Lane",
        "코노스바": "KonoSuba", "우마무스메": "Uma Musume",
        "마블": "Marvel", "MCU": "MCU", "DC": "DC Comics",
        "해리포터": "Harry Potter", "나혼렙": "Solo Leveling",
        "리제로": "Re:Zero", "데이트어라이브": "Date A Live",
        "하이스쿨DXD": "High School DxD", "DXD": "DxD",
        "최애의아이": "Oshi no Ko", "나이트런": "Knight Run",
        "갓오하": "God of Highschool", "갓오브하이스쿨": "God of Highschool",
        "프로젝트문": "Project Moon", "림버스컴퍼니": "Limbus Company", "림컴": "Limbus Company",
        "스타레일": "Honkai: Star Rail", "붕괴스타레일": "Honkai: Star Rail",
        "언더테일": "Undertale", "언더테일AU": "Undertale AU",
        "드래곤볼": "Dragon Ball", "진격의거인": "Attack on Titan", "진격거": "AoT",
        "신의탑": "Tower of God", "전지적독자시점": "Omniscient Reader",
        "원펀맨": "One Punch Man", "디지몬": "Digimon",
        "나의히어로아카데미아": "My Hero Academia", "외모지상주의": "Lookism",
        "단간론파": "Danganronpa", "다크소울": "Dark Souls", "엘든링": "Elden Ring",
        "소아온": "SAO", "SAO": "Sword Art Online", "소드아트온라인": "SAO",
        "메이플스토리": "MapleStory", "메이플": "MapleStory",
        "리그오브레전드": "League of Legends", "롤": "LoL", "LOL": "LoL",
        "스타크래프트": "StarCraft", "화산귀환": "Return of Mount Hua",
        "몬스터헌터": "Monster Hunter", "몬헌": "Monster Hunter",
        "페어리테일": "Fairy Tail", "터닝메카드": "Turning Mecard",
        "마인크래프트": "Minecraft", "건담": "Gundam", "체인소맨": "Chainsaw Man",
        "오버로드": "Overlord", "트릭컬": "Trickster",
        "보컬로이드": "Vocaloid", "젠존제": "Zenless Zone Zero", "ZZZ": "ZZZ",
        "젠레스존제로": "Zenless Zone Zero",
        "클로저스": "Closers", "던파": "Dungeon Fighter",
        "전생슬": "Tensura", "도쿄구울": "Tokyo Ghoul",
        "뷰티풀군바리": "Beautiful Gunbari", "뷰군": "Beautiful Gunbari",
        "워해머": "Warhammer", "워해머40K": "Warhammer 40K",
        "라스트오리진": "Last Origin", "블랙클로버": "Black Clover",
        "이터널리턴": "Eternal Return", "카운터사이드": "CounterSide",
        "금서목록": "Index", "초전자포": "Railgun",
        "에반게리온": "Evangelion", "코드기어스": "Code Geass",
        "트랜스포머": "Transformers", "오버워치": "Overwatch",
        "봇치더락": "Bocchi the Rock", "봇치더록": "Bocchi the Rock",
        "프로세카": "Project Sekai", "제로의사역마": "Zero no Tsukaima",
        "캄피오네": "Campione", "헌터X헌터": "Hunter x Hunter",
        "방패용사": "Shield Hero", "페르소나": "Persona",
        "벤10": "Ben 10", "정글쥬스": "Jungle Juice",
        "스파이더맨": "Spider-Man", "울트라맨": "Ultraman",
        "블러드본": "Bloodborne", "일곱개의대죄": "Seven Deadly Sins",
        "승리의여신": "Goddess of Victory", "승리의여신니케": "NIKKE",
        "카구야님": "Kaguya-sama", "심포기어": "Symphogear",
        "장송의프리렌": "Frieren", "프리렌": "Frieren",
        "무직전생": "Mushoku Tensei", "이세돌": "Isegye Idol",
        "스키비디토일렛": "Skibidi Toilet", "스키비디": "Skibidi",
        "또봇": "Tobot", "메탈카드봇": "Metal Cardbot",
        "고질라": "Godzilla", "해즈빈호텔": "Hazbin Hotel",
        "리버스1999": "Reverse: 1999", "쿠키런": "Cookie Run",
        "아머드코어": "Armored Core", "아이돌마스터": "Idolmaster",
        "낙제기사": "Chivalry of a Failed Knight",
        "내청춘": "My Youth", "내청코": "OreGairu",
        "스타워즈": "Star Wars", "프리큐어": "PreCure",
        "인피니트스트라토스": "Infinite Stratos", "IS": "Infinite Stratos",
        "슈퍼전대": "Super Sentai", "파워레인저": "Power Rangers",
        "키보토스": "Kivotos", "가디언테일즈": "Guardian Tales",
        "스카이림": "Skyrim", "로블록스": "Roblox",
        "로스트아크": "Lost Ark", "사이버펑크2077": "Cyberpunk 2077",
        "엣지러너": "Edgerunners", "림월드": "RimWorld",
        "나노하": "Nanoha", "이리야": "Illya",
        // === Content/Rating Tags ===
        "NTL": "NTL", "19금": "R-19", "SM": "SM/BDSM", "BDSM": "BDSM",
        "떡타지": "Smut Fantasy", "써줘용": "Write for Me",
        "GL": "Girls' Love", "BL물": "BL Story",
        "타락": "Corruption", "노예": "Slave", "성노예": "Sex Slave",
        "펨돔": "Femdom", "암타": "Dark Corruption", "암컷타락": "Female Corruption",
        "유부녀": "Married Woman", "후타나리": "Futanari", "후타": "Futa",
        "상식개변": "Common Sense Alteration", "역강간": "Reverse Assault",
        "오네쇼타": "Oneshota", "몬무스": "Monster Girl",
        "약백합": "Soft Yuri", "유사근친": "Pseudo-Incest",
        "정조역전": "Chastity Reversal", "밀프": "MILF",
        "야설": "Erotic Fiction", "쇼타": "Shota",
        "TS히로인": "TS Heroine", "오토코노코": "Otokonoko",
        "노출": "Exposure", "임신": "Pregnancy", "출산": "Childbirth",
        "불륜": "Affair", "동거": "Cohabitation",
        "섹스": "Sex", "애널": "Anal", "수간": "Bestiality",
        "이상성욕": "Abnormal Desire", "이종간": "Interspecies",
        "레즈": "Lesbian", "거유": "Big Breasts",
        "낭자애": "Crossdressing Love", "난교": "Orgy",
        "갱뱅": "Gangbang", "모유": "Breast Milk",
        "나폴리탄": "Neapolitan", "역NTR": "Reverse NTR",
        "NTR없음": "No NTR", "약NTR": "Soft NTR",
        "남존여비": "Male Supremacy", "여공남수": "Female Top/Male Bottom",
        "여존남비": "Female Supremacy",
        "성비불균형": "Gender Ratio Imbalance",
        "신체개조": "Body Modification", "인체개조": "Body Modification",
        "세뇌": "Brainwashing", "최면": "Hypnosis", "역최면": "Reverse Hypnosis",
        "조련": "Taming/Training", "수치": "Humiliation", "감금": "Confinement",
        "변태": "Pervert", "천박": "Vulgar",
        "페티쉬": "Fetish", "페티시": "Fetish",
        "스캇": "Scat", "료나": "Ryona", "고뢰": "Torture",
        "사도": "Sadism", "마조": "Masochism", "마조히스트": "Masochist",
        "가학": "Sadism", "굴욕": "Humiliation",
        "야외노출": "Public Exposure", "노출증": "Exhibitionism",
        "시간정지": "Time Stop", "촉수": "Tentacle",
        "서큐버스": "Succubus", "후타나리히로인": "Futanari Heroine",
        "후타백합": "Futa Yuri", "백합하렘": "Yuri Harem",
        "메스가키": "Mesugaki", "스와핑": "Swapping",
        "정조대": "Chastity Belt", "하드펨돔": "Hard Femdom",
        "소프트펨돔": "Soft Femdom", "약펨돔": "Soft Femdom",
        "스팽킹": "Spanking", "본디지": "Bondage",
        "구속": "Restraint", "윤간": "Gang Assault",
        "매춘": "Prostitution", "창녀": "Prostitute",
        "자위": "Self-pleasure", "육변기": "Human Toilet",
        "근친상간": "Incest", "모녀덮밥": "Mother-Daughter",
        "볼버스팅": "Ballbusting", "페깅": "Pegging",
        // === Genre/Style Tags ===
        "성장형": "Growth-type", "이능력": "Supernatural Power",
        "범죄": "Crime", "디스토피아": "Dystopia",
        "전이": "Transfer/Isekai", "소꿉친구": "Childhood Friend",
        "이종족": "Different Species", "게이트": "Gate",
        "남주인공": "Male Protagonist", "게임빙의": "Game Possession",
        "게임판타지": "Game Fantasy", "고어": "Gore",
        "하드": "Hardcore", "유희생활": "Pleasure Life",
        "학교": "School", "학원물": "School Story",
        "가상현실": "Virtual Reality", "여행": "Travel",
        "독참소": "Reader Participation Novel", "독자참여": "Reader Participation",
        "옴니버스": "Omnibus", "악마": "Devil/Demon",
        "근대": "Modern Era", "병맛": "Absurd Comedy",
        "연예계": "Entertainment Industry", "미궁": "Labyrinth",
        "능력자": "Ability User", "선협": "Xianxia",
        "전투": "Battle/Combat", "차원이동": "Dimension Travel",
        "신": "God/Deity", "방송": "Broadcasting",
        "치유": "Healing", "스팀펑크": "Steampunk",
        "신화": "Mythology", "여동생": "Little Sister",
        "코믹": "Comic/Comedic", "배틀": "Battle",
        "꽁냥꽁냥": "Lovey-Dovey", "가족": "Family",
        "퇴마": "Exorcism", "마녀": "Witch",
        "하드코어": "Hardcore", "호러": "Horror",
        "AI": "AI", "육성": "Raising/Nurturing",
        "청춘": "Youth", "루프": "Loop/Time Loop",
        "병약": "Sickly/Frail", "막장": "Over-the-top Drama",
        "괴물": "Monster", "연애": "Romance/Dating",
        "몬스터": "Monster", "처녀": "Virgin",
        "해결사": "Fixer/Problem Solver", "MC": "MC",
        "말딸": "Horse Daughter", "오해": "Misunderstanding",
        "어플": "App", "군대": "Military",
        "엄마": "Mom/Mother", "유열": "Bloodshed",
        "시한부": "Terminal Illness", "타입문": "Type-Moon",
        "귀신": "Ghost", "능력": "Ability/Power",
        "공모전": "Contest Entry", "로봇": "Robot",
        "사랑": "Love", "꿈": "Dream",
        "메카닉": "Mechanic/Mecha", "대학교": "University",
        "키잡": "Catching/Collecting", "가스라이팅": "Gaslighting",
        "추방": "Banishment/Exile", "종교": "Religion",
        "일기": "Diary", "보추": "Bodyguard",
        "각성자": "Awakened", "악역": "Villain Role",
        "가챠": "Gacha", "누나": "Older Sister",
        "기갑": "Armored/Mech", "낭만": "Romance/Romantic",
        "정령": "Spirit/Elemental", "리메이크": "Remake",
        "교수": "Professor", "챌린지": "Challenge",
        "멘헤라": "Menhera", "오피스": "Office",
        "모험가": "Adventurer", "남학생": "Male Student",
        "번역": "Translation", "미소녀": "Beautiful Girl",
        "멸망": "Destruction/Ruin", "고문": "Torture",
        "철학": "Philosophy", "외신": "Foreign News/Outer God",
        "광기": "Madness", "약먼치킨": "Soft OP/Munchkin",
        "부자": "Rich/Father-Son", "밴드": "Band",
        "무한회귀": "Infinite Regression", "창작물속으로": "Into Fiction",
        "흔직세": "Common Job World", "네학소": "Four School Novel",
        "약후회": "Soft Regret", "약집착": "Soft Obsession",
        "힘숨찐": "Hidden Power", "블랙코미디": "Black Comedy",
        "먹방": "Mukbang/Food Show", "배신": "Betrayal",
        "유쾌": "Pleasant/Fun", "용": "Dragon",
        "연예인": "Celebrity", "인공지능": "Artificial Intelligence",
        "살인": "Murder", "치트": "Cheat/Cheats",
        "시": "Poetry", "이세계아이돌": "Isekai Idol",
        "TRPG": "TRPG", "격투": "Fighting",
        "동화": "Fairy Tale", "고블린": "Goblin",
        "구미호": "Nine-tailed Fox", "대장장이": "Blacksmith",
        "오리지널": "Original", "선생님": "Teacher",
        "빙의물": "Possession Story", "납치": "Kidnapping",
        "다크": "Dark", "능력자배틀": "Ability Battle",
        "초반약피폐": "Weak Start/Ruined Early",
        "학생": "Student", "연희": "Romance Drama",
        "안드로이드": "Android", "탑등반물": "Tower Climbing",
        "퍼리": "Furry", "남매": "Siblings",
        "검사": "Swordsman/Prosecutor", "RPG": "RPG",
        "비정기": "Irregular", "독식": "Monopolize",
        "퀘스트": "Quest", "아내": "Wife",
        "유혈": "Bloody/Gore", "서스펜스": "Suspense",
        "외지주": "Outer God Master",
        // === More Genre Tags ===
        "심리": "Psychology", "평행세계": "Parallel World",
        "이중인격": "Split Personality", "혁명": "Revolution",
        "원작붕괴": "Canon Divergence", "고양이": "Cat",
        "빌드": "Build", "약물": "Drugs",
        "군인": "Soldier", "이능": "Supernatural",
        "부활": "Revival", "작가물": "Author Story",
        "악녀": "Villainess", "PTSD": "PTSD",
        "소환사": "Summoner", "죽음": "Death",
        "선생": "Teacher", "근현대": "Modern History",
        "초보작가": "Beginner Author", "협박": "Blackmail",
        "외계인": "Alien", "농사": "Farming",
        "투자": "Investment", "과학": "Science",
        "대학생": "College Student", "성기사": "Holy Knight",
        "잔혹": "Cruelty", "오리캐": "Original Character",
        "무공": "Martial Arts", "총": "Gun",
        "시리어스": "Serious", "힐러": "Healer",
        "검": "Sword", "여선생": "Female Teacher",
        "책빙의": "Book Possession", "삼각관계": "Love Triangle",
        "소드마스터": "Sword Master", "수위": "Intensity Level",
        "반전": "Plot Twist", "멀티버스": "Multiverse",
        "첩보": "Espionage", "동양풍": "Eastern-style",
        "퓨전펑크": "Fusion Punk", "매니저": "Manager",
        "주술": "Sorcery/Jujutsu", "사이비": "Cult",
        "단편집": "Short Story Collection", "실눈": "Squinty Eyes",
        "참교육": "True Education", "초반피폐": "Rough Start",
        "로우파워": "Low Power", "더블주인공": "Dual Protagonist",
        "배틀로얄": "Battle Royale", "코인": "Coin/Crypto",
        "도시": "City", "첫사랑": "First Love",
        "미연시": "Visual Novel/Dating Sim", "능력물": "Ability Story",
        "주식": "Stocks", "사망회귀": "Death Regression",
        "문학": "Literature", "문제아": "Problem Child",
        "복수극": "Revenge Drama", "NPC": "NPC",
        "마족": "Demon Race", "타임루프": "Time Loop",
        "해군": "Navy", "금태양": "Golden Sun",
        "불사": "Immortal", "해피엔딩": "Happy Ending",
        "하렘순애": "Harem Pure Love", "카드": "Card",
        "정신병": "Mental Illness", "대학": "College",
        "근세": "Early Modern", "경찰": "Police",
        "회사": "Company", "검술": "Swordsmanship",
        "제자": "Disciple", "무림": "Murim/Martial World",
        "인간찬가": "Ode to Humanity", "고대": "Ancient",
        "외전": "Side Story/Spin-off", "공룡": "Dinosaur",
        "세계대전": "World War", "복종": "Submission",
        "계약": "Contract", "저주": "Curse",
        "스토리": "Story", "의존": "Dependence",
        "일본": "Japan", "현실": "Reality",
        "가상현실게임": "VR Game", "하드보일드": "Hard-boiled",
        "탈출": "Escape", "고등학생": "High Schooler",
        "만화": "Manga/Comic", "운빨": "Luck-based",
        "다차원": "Multi-dimensional", "암살자": "Assassin",
        "길드": "Guild", "연기": "Acting",
        "기업": "Corporation", "도박": "Gambling",
        "이야기": "Story/Tale", "자매": "Sisters",
        "과거": "Past", "괴인": "Villain/Freak",
        "히로인": "Heroine", "언데드": "Undead",
        "절망": "Despair", "비극": "Tragedy",
        "인생": "Life", "시간여행": "Time Travel",
        "스킬": "Skill", "체벌": "Corporal Punishment",
        "수필": "Essay", "콜라보": "Collaboration",
        "집사": "Butler", "자살": "Suicide",
        "슬라임": "Slime", "재난": "Disaster",
        "망상": "Delusion", "창작물": "Creative Work",
        "비밀": "Secret", "관음": "Voyeurism",
        "애증": "Love-Hate", "복수물": "Revenge Story",
        "오크": "Orc", "차원": "Dimension",
        "음식": "Food", "환상체": "Fantasy Entity",
        "진화": "Evolution", "흑화": "Darkening",
        "차원유랑": "Dimension Wandering", "쌍둥이": "Twins",
        "사이코패스": "Psychopath", "그리스로마신화": "Greco-Roman Myth",
        "주인공": "Protagonist", "설정붕괴": "Setting Collapse",
        "닌자": "Ninja", "지옥": "Hell",
        "최강": "Strongest", "지배": "Domination",
        "약하렘": "Soft Harem", "다중차원": "Multi-Dimension",
        "무당": "Shaman", "일진": "Bully/Delinquent",
        "성장형주인공": "Growth-type MC", "질투": "Jealousy",
        "스파이": "Spy", "사신": "Death God/Reaper",
        "친구": "Friend", "도시전설": "Urban Legend",
        "달달": "Sweet/Fluffy", "다중인격": "Multiple Personality",
        "운명": "Destiny/Fate", "FPS": "FPS",
        "귀여움": "Cute", "여기사": "Female Knight",
        "경제": "Economy", "찐따": "Loser/Nerd",
        "걸그룹": "Girl Group", "상인": "Merchant",
        "습작": "Practice Work", "노래": "Song/Singing",
        "사제": "Priest/Master-Disciple", "파티": "Party",
        "공무원": "Civil Servant", "소환수": "Summon",
        "마교": "Demonic Sect", "흑마법사": "Dark Mage",
        "수녀": "Nun", "이세계전이": "Isekai Transfer",
        "다중세계": "Multi-World", "자유연재": "Free Serial",
        "개조": "Modification", "가상역사": "Alt History",
        "사극": "Historical Drama", "동물": "Animal",
        "킬러": "Killer", "짝사랑": "Unrequited Love",
        "귀멸": "Demon Slayer", "좀비아포칼립스": "Zombie Apocalypse",
        "2차세계대전": "WWII", "무인도": "Deserted Island",
        "소프트얀데레": "Soft Yandere", "이누야샤": "InuYasha",
        "학원도시": "Academy City", "이능력배틀": "Ability Battle",
        "정통": "Orthodox/Classic", "백룸": "Backrooms",
        "초능력자": "Esper", "서부극": "Western",
        "하이스쿨": "High School", "미국": "America",
        "데스티니": "Destiny", "연습작": "Practice Work",
        "재능": "Talent", "감성": "Emotional/Sentimental",
        "메카물": "Mecha Story", "갱생": "Rehabilitation",
        "연금술사": "Alchemist", "BJ": "Broadcaster/BJ",
        "제국": "Empire", "레벨업": "Level Up",
        "등반": "Climbing", "희생": "Sacrifice",
        "댓글소설": "Comment Novel", "소년": "Boy/Shounen",
        "조선시대": "Joseon Dynasty", "단편소설": "Short Story",
        "도깨비": "Dokkaebi/Goblin", "기계": "Machine",
        "플레이어": "Player", "국뽕": "Nationalism",
        "가벼움": "Light/Casual", "웹툰": "Webtoon",
        "귀환자": "Returnee", "백수": "Unemployed/NEET",
        "달달함": "Sweetness", "게임개발": "Game Development",
        "퓨전무협": "Fusion Martial Arts", "크리처": "Creature",
        "회사원": "Office Worker", "마물": "Magical Beast",
        "문명": "Civilization", "생존기": "Survival Story",
        "세계관": "World Building", "VR": "VR",
        "교사": "Teacher", "능력배틀": "Power Battle",
        "실화": "True Story", "명탐정코난": "Detective Conan",
        "고등학교": "High School", "바다": "Sea/Ocean",
        "트라우마": "Trauma", "육아물": "Parenting Story",
        "영애": "Noble Lady", "디펜스": "Defense",
        "애니": "Anime", "여고생": "Schoolgirl",
        "편의점": "Convenience Store", "첫작품": "First Work",
        "로그라이크": "Roguelike", "자유": "Freedom",
        "순한맛": "Mild", "다중세계관": "Multi-World Setting",
        "히키코모리": "Hikikomori", "수사": "Investigation",
        "사후세계": "Afterlife", "도사": "Taoist",
        "정통무협": "Classic Martial Arts", "이혼": "Divorce",
        "결혼": "Marriage", "가수": "Singer",
        "희망": "Hope", "설정": "Setting/Worldbuilding",
        "음모": "Conspiracy", "소울라이크": "Souls-like",
        "AOS": "AOS/MOBA", "황녀": "Imperial Princess",
        "인생역전": "Life Reversal", "저승": "Afterlife",
        "장편": "Long Story", "정신붕괴": "Mental Breakdown",
        "노력": "Effort/Hard Work", "엔터테인먼트": "Entertainment",
        "탐험": "Exploration", "마계": "Demon World",
        "안티히어로": "Anti-Hero", "피페": "Ruined/Devastated",
        "전쟁영웅": "War Hero", "격투기": "Martial Arts/MMA",
        "성직자": "Cleric/Clergy", "주종관계": "Master-Servant",
        "항해": "Voyage/Sailing", "군주": "Monarch",
        "책사": "Strategist", "직장인": "Office Worker",
        "보스": "Boss", "냉전": "Cold War",
        "중2병": "Chuunibyou", "동아리": "Club",
        "농구": "Basketball", "버츄얼": "Virtual",
        "영혼": "Soul", "디젤펑크": "Dieselpunk",
        "카드게임": "Card Game", "영화": "Movie",
        "인형": "Doll/Puppet", "역전세계": "Reversed World",
        "바이러스": "Virus", "한국": "Korea",
        "늑대": "Wolf", "대항해시대": "Age of Sail",
        "무쌍": "Unmatched/Musou", "인어": "Mermaid",
        "유령": "Ghost/Spirit", "의인화": "Personification",
        "실험": "Experiment", "전차": "Tank",
        "웹소설": "Web Novel", "초보": "Beginner",
        "연상연하": "Age Gap", "전설": "Legend",
        "동료": "Companion", "저승사자": "Grim Reaper",
        "대한민국": "South Korea", "어드벤처": "Adventure",
        "DOOM": "DOOM", "에로": "Ero",
        "욕망": "Desire", "메타버스": "Metaverse",
        "정복": "Conquest", "로맨스코미디": "Rom-Com",
        "심리전": "Psychological Warfare", "도서관": "Library",
        "부부": "Married Couple", "체스": "Chess",
        "인간": "Human", "그리스": "Greece",
        "소련": "Soviet Union", "검성": "Sword Saint",
        "싸움": "Fight", "여대생": "College Girl",
        "개발자": "Developer", "조폭": "Gangster/Yakuza",
        "근육녀": "Muscular Girl", "스트리밍": "Streaming",
        "시대극": "Period Drama", "유튜버": "YouTuber",
        "회귀자": "Regressor", "거인": "Giant",
        "극복": "Overcoming", "바이오펑크": "Biopunk",
        "잔잔": "Calm/Peaceful", "튜토리얼": "Tutorial",
        "기록": "Record/Log", "특촬물": "Tokusatsu",
        "소녀": "Girl/Shoujo", "학교물": "School Story",
        "후회남": "Regretful Man", "시골": "Countryside",
        "미친놈": "Madman", "극악연재": "Extreme Serial",
        "여장남자": "Crossdressing Man", "로코": "Romantic Comedy",
        "특수부대": "Special Forces", "빠른전개": "Fast-paced",
        "초월자": "Transcendent", "성공": "Success",
        "처녀작": "Debut Work", "은퇴": "Retirement",
        "교주": "Cult Leader", "총잡이": "Gunslinger",
        "기억": "Memory", "도플갱어": "Doppelganger",
        "여정": "Journey", "러시아": "Russia",
        "초인": "Superhuman", "특촬": "Tokusatsu",
        "딸": "Daughter", "학대": "Abuse",
        "사이보그": "Cyborg", "로우판타지": "Low Fantasy",
        "하이판타지": "High Fantasy", "풍자": "Satire",
        // === Remaining Novelpia Tags (10+ novels) ===
        "던만추": "DanMachi", "나히아": "Nahia",
        "지름작": "Impulse Buy Work", "용사파티": "Hero Party",
        "일부TS": "Partial TS", "아케인펑크": "Arcanepunk",
        "게임시스템": "Game System", "이군깽": "Military Thug",
        "기츠": "Geats", "지오": "Zi-O",
        "컨셉질": "Concept Play", "IF": "IF/What-If",
        "토리코": "Toriko", "떡협지": "Smut Collab",
        "아리우스": "Arius", "캐붕": "Character Collapse",
        "MTR": "MTR", "산나비": "Sannabi",
        "자캐": "Original Character", "메가데레": "Mega Dere",
        "컨셉": "Concept", "듀얼": "Duel",
        "착정": "Corruption/Staining", "약고어": "Soft Gore",
        "네토": "Netori", "연습": "Practice",
        "흔해빠진": "Common/Generic", "스승": "Master/Mentor",
        "세이버": "Saber", "사지절단": "Dismemberment",
        "호시노": "Hoshino", "게헨나": "Gehenna",
        "모녀": "Mother-Daughter", "스텔라이브": "Stellive",
        "코스믹호러": "Cosmic Horror", "마약": "Drugs",
        "기생": "Parasitism", "어마금": "Mother's Money",
        "나혼뉴": "Solo Newbie", "용사가돌아왔다": "Hero Has Returned",
        "암살교실": "Assassination Classroom", "실지주": "Real World Master",
        "고구마": "Sweet Potato/Frustrating", "갓챠드": "Gotchard",
        "트립": "Trip", "식인": "Cannibalism",
        "아리스": "Aris", "부분19": "Partial R-19",
        "다가온": "Arrived/Come", "크싸레": "Cringe Love",
        "돈지랄": "Money Spending Spree", "독일": "Germany",
        "마법학교": "Magic Academy", "E스포츠": "Esports",
        "샌즈": "Sans", "멜섭": "Melsub",
        "가히리": "Gahiri", "명조": "Wuthering Waves",
        "인격배설": "Personality Excretion", "겜판": "Game World",
        "보이스로이드": "Voiceroid", "매운맛": "Spicy/Intense",
        "혐관": "Disgusting", "상태창없음": "No Status Window",
        "맹인": "Blind", "TS아님": "Not TS",
        "칠대죄": "Seven Deadly Sins", "아크파이브": "Arc-V",
        "코난": "Conan", "그림": "Illustration/Drawing",
        "친구엄마": "Friend's Mom", "꽁냥": "Lovey-Dovey",
        "아싸": "Outsider/Loner", "아비도스": "Abydos",
        "뱅드림": "BanG Dream!", "2차대전": "WWII",
        "BSS": "BSS", "신비아파트": "Sinbi Apartment",
        "고쥬저": "King-Ohger", "페스나": "Fate/Stay Night",
        "스타킹": "Stockings", "고라니": "Deer/Random",
        "깽판": "Rampage", "캐빨": "Character Luck",
        "엘소드": "Elsword", "약코미디": "Soft Comedy",
        "이상현상": "Anomaly", "배우물": "Actor Story",
        "아마도": "Maybe/Probably", "보빔": "Treasure/Beam",
        "가브": "Gabu", "악질": "Scum/Wicked",
        "트리니티": "Trinity", "마나": "Mana",
        "집착남": "Obsessive Male", "뇌절": "Cringe/Cringy",
        "리본": "Reborn!", "로리": "Loli",
        "야겜": "Eroge", "라이브러리오브루이나": "Library of Ruina",
        "뽑기": "Gacha/Draw", "유니콘": "Unicorn",
        "실험체": "Test Subject", "멜돔": "Maledom",
        "데스티니가디언즈": "Destiny: Guardians", "레제": "Reze",
        "소재": "Material/Theme", "여우": "Fox",
        "약피페": "Soft Devastation", "능력자배틀물": "Ability Battle Story",
        "소설아님": "Not a Novel", "수성의마녀": "Witch from Mercury",
        "디케이드": "Decade", "레이싱": "Racing",
        "갑질": "Power Abuse", "3P": "Threesome",
        "육체개조": "Body Modification", "어두움": "Darkness",
        "대물": "Large/Big", "어과초": "Fisheries HS",
        "펨섭": "Femsub", "개판": "Chaos/Mess",
        "기타등등": "Etc/Miscellaneous", "이상": "Abnormal/Ideal",
        "감독": "Director", "우주세기": "Universal Century",
        "무지성": "Mindless/Brainless", "TS있음": "Contains TS",
        "NTK야설대회": "NTK Contest", "실눈캐": "Squinty-eyed Char",
        "걸레": "Promiscuous", "마마마": "Madoka Magica",
        "독자중혁TS": "Reader-chosen TS", "비일상": "Non-daily Life",
        "로마": "Rome", "역전": "Reversal",
        "진지": "Serious", "월희": "Tsukihime",
        "에그제이드": "Ex-Aid", "욕설": "Profanity",
        "욕설주의": "Profanity Warning", "가축": "Livestock",
        "아줌마": "Middle-aged Woman", "이별": "Breakup",
        "방귀": "Flatulence", "역내청": "Reverse OreGairu",
        "몸으로하는스포츠": "Physical Sports",
        "삽화": "Illustration", "엘피스전기": "Elpis Chronicle",
        "약수위": "Mild Intensity", "상담": "Counseling",
        "치녀": "Virgin Woman", "색마": "Lust Demon",
        "오리주": "Original MC", "조직": "Organization",
        "다크라이더": "Dark Rider", "사신소년": "Reaper Boy",
        "오즈": "OOO/Oz", "술": "Alcohol",
        "잔인함": "Brutality", "프리코네": "Princess Connect",
        "블랙서바이벌": "Black Survival", "모자": "Mother-Son/Hat",
        "쓰리썸": "Threesome", "눈물": "Tears",
        "격리픽션": "Isolation Fiction", "바바리안": "Barbarian",
        "회빙환": "Regression/Ice/Return", "북부대공": "Northern Duke",
        "일진녀": "Delinquent Girl", "쌀먹": "Rice-eating/Satisfying",
        "로드무비": "Road Movie", "뱀": "Snake",
        "B급": "B-grade", "나혼자": "Alone/Solo",
        "마크": "Mark/Minecraft", "곤충": "Insect",
        "마사지": "Massage", "배덕": "Immoral",
        "단편모음": "Short Story Collection", "좀비고": "Zombie High",
        "공의경계": "Kara no Kyoukai", "밀레니엄": "Millennium",
        "로보토미코퍼레이션": "Lobotomy Corporation",
        "오나홀": "Onahole", "썰": "Story/Tale",
        "단편선": "Short Anthology", "대마인": "Taimanin",
        "류우키": "Ryuki", "GOH": "God of Highschool",
        "벨": "Bell", "능력남": "Ability Man",
        "헤일로": "Halo", "미시": "Mature Woman",
        "응애": "Baby/Whimper", "개발": "Development",
        "정신조종": "Mind Control", "상점": "Shop/Store",
        "폭유": "Huge Breasts", "관리국": "Management Bureau",
        "비처녀없음": "No Non-virgins", "해골": "Skeleton",
        "둠": "Doom", "전투기": "Fighter Jet",
        "일제강점기": "Japanese Colonial Era", "면간": "Face-sitting",
        "시리즈": "Series", "감정": "Emotion/Appraisal",
        "도망": "Escape/Flee", "후피집": "Post-devastation",
        "메모라이즈": "Memorize", "음뇨": "Urolagnia",
        "스쿠나": "Sukuna", "세계": "World",
        "상상": "Imagination", "오리카": "Original Card",
        "유희왕GX": "Yu-Gi-Oh! GX", "나와호랑이님": "Me and the Tiger",
        "악성향": "Dark Tendencies", "페이트그랜드오더": "Fate/Grand Order",
        "약SM": "Soft SM", "마도정병": "Magic Soldier",
        "미카": "Mika", "수치플": "Humiliation Play",
        "교관": "Instructor", "이능력배틀물": "Ability Battle Story",
        "굴복": "Surrender", "가끔진지": "Sometimes Serious",
        "카페": "Cafe", "히틀러": "Hitler",
        "잔혹동화": "Cruel Fairy Tale", "상황극": "Situation Play",
        "캐붕주의": "Char Collapse Warning", "성자": "Saint",
        "고구려": "Goguryeo", "알파메일": "Alpha Male",
        "사업": "Business", "유녀전기": "Saga of Tanya",
        "성배전쟁": "Holy Grail War", "마망": "Demon Mother",
        "회빙환X": "Regression X", "나치": "Nazi",
        "조종": "Control/Manipulation", "금기": "Taboo",
        "설화": "Folklore", "별의커비": "Kirby",
        "동생": "Younger Sibling", "해병": "Marine",
        "슬로우라이프": "Slow Life", "서양판타지": "Western Fantasy",
        "총기": "Firearms", "고죠사토루": "Gojo Satoru",
        "도라에몽": "Doraemon", "시간": "Time",
        "최종보스": "Final Boss", "오마지오": "Homage",
        "발로란트": "Valorant", "부분19금": "Partial R-19",
        "전사": "Warrior", "핵전쟁": "Nuclear War",
        "드워프": "Dwarf", "코스프레": "Cosplay",
        "슈퍼로봇대전": "Super Robot Wars", "세키로": "Sekiro",
        "왁타버스": "Waktaverse", "형사": "Detective",
        "상남자": "Manly Man", "해병문학": "Marine Literature",
        "현대로맨스": "Modern Romance", "겜빙의": "Game Possession",
        "마검": "Magic Sword", "대한제국": "Korean Empire",
        "주종역전": "Master-Servant Reversal", "기독교": "Christianity",
        "공상과학": "Sci-Fi/Speculative", "순정": "Pure/Innocent",
        "프리즈마": "Prisma", "GX": "GX",
        "소설빙의": "Novel Possession", "괴도": "Phantom Thief",
        "리바이스": "Revice", "슬픔": "Sadness",
        "게이": "Gay", "현대무기": "Modern Weapons",
        "콜오브듀티": "Call of Duty", "에세이": "Essay",
        "제로원": "Zero-One", "테러": "Terrorism",
        "만담": "Comedy Talk", "다정남": "Kind Man",
        "뽕빨": "Power Trip", "대마법사": "Archmage",
        "던전운영": "Dungeon Management", "식물": "Plant",
        "힘순찐": "Hidden True Power", "늑대인간": "Werewolf",
        "능력녀": "Ability Girl", "외교": "Diplomacy",
        "열혈": "Hot-blooded", "빙하기": "Ice Age",
        "역조교": "Reverse Training", "차원여행": "Dimension Travel",
        "미녀": "Beautiful Woman", "3차창작": "Tertiary Creation",
        "소유욕": "Possessiveness", "작곡": "Composition",
        "쌍방구원": "Mutual Salvation", "세계수": "World Tree",
        "전개느림": "Slow Development", "검은머리": "Black Hair",
        "제국주의": "Imperialism", "새엄마": "Stepmother",
        "분탕": "Trolling/Chaos", "거대로봇": "Giant Robot",
        "회상": "Flashback", "커비": "Kirby",
        "서사": "Narrative/Epic", "바이오하자드": "Resident Evil",
        "폴아웃": "Fallout", "초차원게임넵튠": "Neptunia",
        "패티쉬": "Fetish", "차원유랑물": "Dimension Wandering",
        "도시괴담": "Urban Horror Tales", "퇴폐": "Decadence",
        "데스노트": "Death Note", "은혼": "Gintama",
        "지능캐": "Smart Character", "여사친": "Female Friend",
        "바텐더": "Bartender", "옵니버스": "Omnibus",
        "마비노기": "Mabinogi", "아저씨": "Uncle/Middle-aged Man",
        "느린연재": "Slow Updates", "사냥": "Hunting",
        "마인드컨트롤": "Mind Control", "냄새": "Smell",
        "전간기": "Interwar Period", "메다카박스": "Medaka Box",
        "일기장": "Diary/Journal", "연중": "Hiatus",
        "군사": "Military", "바키": "Baki",
        "몰루": "Dunno/IDK", "권선징악": "Good vs Evil",
        "홀로라이브EN": "Hololive EN", "에피소드": "Episode",
        "파이즈": "Faiz", "고지라": "Godzilla",
        "디비전": "Division", "여러가지": "Various",
        "낙제기사의영웅담": "Chivalry of a Failed Knight",
        "아이마스": "Idolmaster", "정상화": "Normalization",
        "택티컬": "Tactical", "약공포": "Soft Horror",
        "교체": "Swap/Change", "약TS": "Soft TS",
        "느린암타": "Slow Corruption", "퇴마사": "Exorcist",
        "복싱": "Boxing", "규칙괴담": "Rules Horror",
        "카드배틀": "Card Battle", "AV": "AV",
        "배드엔딩": "Bad Ending", "MMORPG": "MMORPG",
        "무녀": "Shrine Maiden", "문스독": "Moon's Dog",
        "이것저것": "This and That", "타이쿤": "Tycoon",
        "돈": "Money", "모드": "Mod",
        "행복": "Happiness", "전함": "Warship/Battleship",
        "헬싱": "Hellsing", "분신": "Clone/Alter Ego",
        "서부": "Western", "쓰레기": "Trash/Garbage",
        "해커": "Hacker", "클리셰": "Cliché",
        "카제나": "Kazena", "기괴": "Bizarre",
        "몽마": "Dream Demon", "신체결손": "Body Deficiency",
        "스토커": "Stalker", "테이머": "Tamer",
        "요원": "Agent", "프세카": "Project Sekai",
        "등등": "Etc", "영주": "Lord/Feudal Lord",
        "요정": "Fairy", "퀘지주": "Quest World Master",
        "어실주": "Real World Master", "아카데미아": "Academia",
        "환생자": "Reincarnator", "지휘관": "Commander",
        "레즈배틀": "Lesbian Battle", "강제": "Forced",
        "설정집": "Setting Collection", "여캠": "Female Cam",
        "미션": "Mission", "연습장": "Practice Space",
        "가벼운": "Light/Casual", "아마도하렘": "Maybe Harem",
        "환상": "Fantasy/Illusion", "전대물": "Super Sentai Story",
        "누아르": "Noir", "혈귀": "Blood Demon",
        "젤다의전설": "Legend of Zelda", "킹오저": "King-Ohger",
        "BLEACH": "Bleach", "유튜브": "YouTube",
        "NONTR": "No NTR", "오룡즈": "Ohrangers",
        "함대전": "Fleet Battle", "의식의흐름": "Stream of Consciousness",
        "픽션": "Fiction", "메리수": "Mary Sue",
        "더블오": "00/Double-O", "강화": "Enhancement",
        "시스템창": "System Window", "HL": "HL",
        "하이파워": "High Power", "학교생활": "School Life",
        "권력": "Power/Authority", "애니메이션": "Animation",
        "창관": "Window View", "악당주인공": "Villain Protagonist",
        "무기": "Weapon", "오리지널캐릭터": "Original Character",
        "싸이코패스": "Psychopath", "아이템": "Item",
        "소설가": "Novelist", "전이물": "Transfer Story",
        "구원서사": "Salvation Narrative", "직장": "Workplace",
        "템빨": "Item Dependent", "기연": "Fortuitous Encounter",
        "더블TS": "Double TS", "베놈": "Venom",
        "약다중": "Soft Multi", "소시오패스": "Sociopath",
        "최면어플": "Hypnosis App", "AU": "AU/Alternate Universe",
        "세계정복": "World Domination", "또라이": "Crazy Person",
        "일러스트": "Illustration", "그리스신화": "Greek Mythology",
        "마탑": "Mage Tower", "연구": "Research",
        "노인": "Elderly", "신라": "Silla Dynasty",
        "전여친": "Ex-girlfriend", "VRAINS": "VRAINS",
        "가상": "Virtual", "2차장작": "Secondary Creation",
        "방랑": "Wandering", "말포이TS": "Malfoy TS",
        "붕괴3": "Honkai 3", "타임리프": "Time Leap",
        "용돌": "Dragon Idol", "고블린슬레이어": "Goblin Slayer",
        "더블": "Double", "저격수": "Sniper",
        "히나": "Hina", "약약피폐": "Very Soft Devastation",
        "사신수": "Death Beast", "전래동화": "Folk Tale",
        "아카메가벤다": "Akame ga Kill", "리얼돌": "Real Doll",
        "로또": "Lottery", "사기": "Fraud/OP",
        "수사물": "Crime Investigation", "롤랑": "Roland",
        "풋잡": "Footjob", "암흑가": "Underworld",
        "음유시인": "Bard", "여왕": "Queen",
        "인큐버스": "Incubus", "감옥": "Prison",
        "메타픽션": "Metafiction", "TS물": "TS Story",
        "몬스터버스": "Monsterverse", "아이디어": "Idea",
        "정신조작": "Mind Manipulation", "터미네이터": "Terminator",
        "엑스맨": "X-Men", "방패용사성공담": "Shield Hero Success",
        "음모론": "Conspiracy Theory", "2회차": "2nd Playthrough",
        "유희": "Game/Play", "괴담동아리": "Horror Story Club",
        "SEED": "Gundam SEED", "치한": "Groper",
        "라이덴": "Raiden", "가이무": "Gaim",
        "추억": "Memories", "테라리아": "Terraria",
        "초고수위": "Ultra High Intensity", "소울워커": "Soul Worker",
        "레즈비언": "Lesbian", "남장": "Male Disguise",
        "당하는주인공": "Suffering MC", "종합게임": "Mixed Games",
        "케로로": "Keroro", "상처": "Wound/Scar",
        "AI일러스트": "AI Illustration", "재벌남": "Chaebol Man",
        "연희아님": "Not Romance", "고품격성인소설": "Premium Adult Novel",
        "엽기": "Bizarre/Grotesque", "구원물": "Salvation Story",
        "착유": "Milking", "능력여주": "Ability Heroine",
        "약근친": "Soft Pseudo-Incest", "TS백합": "TS Yuri",
        "의학": "Medicine", "던전경영": "Dungeon Management",
        "MMA": "MMA", "고죠": "Gojo",
        "폴리아모리": "Polyamory", "빌드업": "Build-up",
        "무술": "Martial Arts", "예언": "Prophecy",
        "마노사바": "Demon Survival", "마법소녀의마녀재판": "Magical Girl Witch Trial",
        "전투원화": "Combat Animation", "재단": "Foundation/SCP",
        "소닉": "Sonic", "풀맨스": "Full Romance",
        "반란": "Rebellion", "선택지": "Choices",
        "폐급": "Trash-tier",
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
        "爱情类": "Romance-type", "同人类": "Fan Fiction-type", "魔幻类": "Magic Fantasy-type",
        "2018萌神": "2018 Moe God", "2019萌神": "2019 Moe God", "2020萌神": "2020 Moe God",
        "2021萌神": "2021 Moe God", "2022萌神": "2022 Moe God", "2023萌神": "2023 Moe God",
        "2024萌神": "2024 Moe God", "2025萌神": "2025 Moe God",
        // Top-100 tags
        "1차세계대전": "World War I", "TS5화미만": "TS Under 5 Chapters",
        "각종태그": "Various Tags", "개이득": "Huge Profit",
        "게으른": "Lazy", "계약결혼": "Contract Marriage",
        "공학": "Engineering", "괴담우주": "Horror Universe",
        "괴담향": "Horror-flavored", "국가경영": "Nation Management",
        "김괴라리": "Kim Goe-ra-ri", "나혼자능력자": "Solo Ability User",
        "날먹": "Freeloader", "뉴비": "Newbie",
        "대체역사향첨가": "Alt. History Flavored", "로우먼치킨": "Low-key OP",
        "롬멜": "Rommel", "마법소년": "Magic Boy",
        "메디컬": "Medical", "명장": "Great General",
        "세종파딱": "Sejong Twist", "아기호랑이": "Baby Tiger",
        "아카데미파견공무원": "Academy Dispatch Officer", "야짤": "Lewds",
        "어노말리": "Anomaly", "우주괴담": "Space Horror",
        "유년기": "Childhood", "임진왜란": "Imjin War",
        "젤리": "Jelly", "족장": "Chieftain",
        "종신파딱": "Life Sentence Twist", "캔따개하렘": "Easy Harem",
        "파딱": "Plot Twist", "표절": "Plagiarism",
        "허균": "Heo Gyun",
    };

    function tl(tag) { return TAG_MAP[tag] || tag; }

    // === State ===
    let allNovels = [];
    let titleTranslations = {};  // id -> english title
    let filtered = [];
    let andTags = new Set();
    let orTags = new Set();
    let excludeTags = new Set();
    let tagMode = "AND";
    let displayCount = 32;
    let allTagCounts = {};   // full tag -> count for all loaded novels
    let top80Tags = new Set(); // tags shown in the default top-80 cloud
    let BATCH = 32;
    let currentPage = 1;
    let pendingImageTimers = [];

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
    const tagSearchInput = $("#tagSearch");
    const excludeTagSearchInput = $("#excludeTagSearch");
    const batchSelect = $("#batchSelect");
    const statusSelect = $("#statusSelect");
    const audienceSelect = $("#audienceSelect");

    // Track which specific card+tag was clicked for highlight
    let focusedCard = null; // { id, source, tag }
    let preTagScrollY = null; // scroll position before tag filter was applied
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
        allTagCounts = counts;
        const sorted = Object.entries(counts)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 80);
        top80Tags = new Set(sorted.map(([t]) => t));

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
        // If tag is already in either set, remove it
        if (andTags.has(tag)) {
            andTags.delete(tag);
            chip.classList.remove("active");
        } else if (orTags.has(tag)) {
            orTags.delete(tag);
            chip.classList.remove("active-or");
        } else {
            // Add to the set matching current mode
            if (tagMode === "AND") {
                andTags.add(tag);
                chip.classList.add("active");
            } else {
                orTags.add(tag);
                chip.classList.add("active-or");
            }
        }
        updateActiveTagsSummary();
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
        updateActiveTagsSummary();
        applyFilters();
    }

    function updateActiveTagsSummary() {
        const includeSummary = document.getElementById("activeTagsSummary");
        const excludeSummary = document.getElementById("activeExcludeTagsSummary");

        // Include tags (AND + OR)
        includeSummary.innerHTML = "";
        for (const tag of andTags) {
            const chip = document.createElement("span");
            chip.className = "summary-chip include";
            chip.textContent = `${tl(tag)} ✕`;
            chip.title = `Remove AND tag: ${tag}`;
            chip.addEventListener("click", () => {
                andTags.delete(tag);
                tagContainer.querySelectorAll(".tag-chip").forEach((c) => {
                    if (c.dataset.tag === tag) c.classList.remove("active");
                });
                updateActiveTagsSummary();
                applyFilters();
            });
            includeSummary.appendChild(chip);
        }
        for (const tag of orTags) {
            const chip = document.createElement("span");
            chip.className = "summary-chip include-or";
            chip.textContent = `${tl(tag)} ✕`;
            chip.title = `Remove OR tag: ${tag}`;
            chip.addEventListener("click", () => {
                orTags.delete(tag);
                tagContainer.querySelectorAll(".tag-chip").forEach((c) => {
                    if (c.dataset.tag === tag) c.classList.remove("active-or");
                });
                updateActiveTagsSummary();
                applyFilters();
            });
            includeSummary.appendChild(chip);
        }

        // Exclude tags
        excludeSummary.innerHTML = "";
        for (const tag of excludeTags) {
            const chip = document.createElement("span");
            chip.className = "summary-chip exclude";
            chip.textContent = `${tl(tag)} ✕`;
            chip.title = `Remove ${tag}`;
            chip.addEventListener("click", () => {
                excludeTags.delete(tag);
                excludeTagContainer.querySelectorAll(".tag-chip").forEach((c) => {
                    if (c.dataset.tag === tag) c.classList.remove("excluded");
                });
                updateActiveTagsSummary();
                applyFilters();
            });
            excludeSummary.appendChild(chip);
        }
    }

    // Add a tag to include filter (from card click)
    function addIncludeTag(tag) {
        // Clear previous tags first
        andTags.clear();
        orTags.clear();
        tagContainer.querySelectorAll(".tag-chip.active, .tag-chip.active-or").forEach((c) => {
            c.classList.remove("active");
            c.classList.remove("active-or");
        });
        andTags.add(tag);
        // Highlight the chip in the tag grid
        tagContainer.querySelectorAll(".tag-chip").forEach((c) => {
            if (c.dataset.tag === tag) c.classList.add("active");
        });
        updateActiveTagsSummary();
        applyFilters();
    }

    // Handle browser back/forward — restore from URL hash
    window.addEventListener("hashchange", () => {
        restoreFromHash();
    });

    // === Rank helper: picks the right rank field based on audience ===
    // all     → all/plus ranks
    // general → teen/plus ranks (Novelpia "teen" = non-adult = our "General")
    // adult   → adult/plus ranks
    // r15     → all ranks (no dedicated R15 ranking on Novelpia)
    function getRank(novel, type) {
        const aud = audienceSelect.value;
        if (aud === "adult") {
            if (type === "weekly") return novel.weeklyRankAdult;
            if (type === "monthly") return novel.monthlyRankAdult;
            if (type === "daily") return novel.dailyRankAdult;
        } else if (aud === "general") {
            if (type === "weekly") return novel.weeklyRankTeen;
            if (type === "monthly") return novel.monthlyRankTeen;
            if (type === "daily") return novel.dailyRankTeen;
        }
        // all / r15 → use 'all' ranks
        if (type === "weekly") return novel.weeklyRank;
        if (type === "monthly") return novel.monthlyRank;
        if (type === "daily") return novel.dailyRank;
        return 0;
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
            if (audience === "adult" && !((n.source === "novelpia" || n.source === "kakao") && n.age === 19)) return false;
            if (audience === "r15" && !((n.source === "sfacg" && n.age === 19) || (n.source === "novelpia" && n.age === 15))) return false;
            if (audience === "general" && n.age === 19) return false;

            // Tag filter (include): AND tags + OR tags
            const hasAndTags = andTags.size > 0;
            const hasOrTags = orTags.size > 0;
            if (hasAndTags || hasOrTags) {
                const novelTags = new Set(n.tags);
                // All AND tags must match
                if (hasAndTags) {
                    for (const t of andTags) {
                        if (!novelTags.has(t)) return false;
                    }
                }
                // At least one OR tag must match (if any OR tags selected)
                if (hasOrTags) {
                    let match = false;
                    for (const t of orTags) {
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
                case "daily":
                case "weekly":
                case "monthly": {
                    const ra = getRank(a, sortBy) || 9999;
                    const rb = getRank(b, sortBy) || 9999;
                    if (ra !== rb) return order === "asc" ? rb - ra : ra - rb;
                    return b.views - a.views;
                }
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
        render(true);
    }

    // === Render Cards ===

    function renderCard(n) {
        const card = document.createElement("div");
        card.className = "novel-card";
        const novelSource = n.source || currentSource;
        const cfg = SOURCES[novelSource] || SOURCES.novelpia;
        const cardLink = `${cfg.linkPrefix}${n.id}`;

        // Novelpia fallback covers
        const NPIA_COVER_R19 = "https://images.novelpia.com/img/novel/adult_cover_img.jpg";
        const NPIA_COVER_DEFAULT = "https://images.novelpia.com/img/layout/readycover4.png";

        let coverSrc = n.cover;
        if ((!coverSrc || coverSrc === "") && novelSource === "novelpia") {
            coverSrc = (n.age === 19) ? NPIA_COVER_R19 : NPIA_COVER_DEFAULT;
        }
        const fallbackSrc = (novelSource === "novelpia")
            ? ((n.age === 19) ? NPIA_COVER_R19 : NPIA_COVER_DEFAULT)
            : "";

        const coverHTML = coverSrc
            ? `<img class="card-cover" data-src="${escHtml(coverSrc)}" alt="" decoding="async" referrerpolicy="no-referrer" onload="this.classList.add('loaded')" onerror="${fallbackSrc ? `this.onerror=null;this.src='${fallbackSrc}'` : `this.outerHTML='<div class=\\'card-cover no-img\\'>📖</div>'`}">`
            : `<div class="card-cover no-img">📖</div>`;

        const sortBy = sortSelect.value;
        const displayRank = (sortBy === "daily" || sortBy === "weekly" || sortBy === "monthly")
            ? getRank(n, sortBy)
            : (getRank(n, "daily") || getRank(n, "weekly") || getRank(n, "monthly"));
        const rankBadge = displayRank ? `<span class="card-badge badge-rank">#${displayRank}</span>` : "";
        const completeBadge = n.complete ? `<span class="card-badge badge-complete">Complete</span>` : "";
        const badgeHTML = completeBadge + rankBadge;

        const isFocused = focusedCard && focusedCard.id === n.id && focusedCard.source === (n.source || currentSource);
        const sortedTags = [...n.tags].sort((a, b) => ((andTags.has(b) || orTags.has(b)) ? 1 : 0) - ((andTags.has(a) || orTags.has(a)) ? 1 : 0));
        const tagsHTML = sortedTags
            .map((t) => `<span class="card-tag${isFocused && focusedCard.tag === t ? ' active' : ''}" title="${escHtml(t)}" data-tag="${escHtml(t)}">${escHtml(tl(t))}</span>`)
            .join("");

        const isR15 = (novelSource === "sfacg" && n.age === 19) || (novelSource === "novelpia" && n.age === 15);
        const isR19 = (novelSource !== "sfacg" && n.age === 19);
        const ageBadge = isR15 ? `<span class="badge-r15">15</span>`
            : isR19 ? `<span class="badge-r19">19</span>`
            : "";

        const synopsisHTML = n.synopsis ? `
                <div class="card-synopsis"><span class="synopsis-label">Synopsis:</span> ${escHtml(n.synopsis).replace(/\n+/g, '<br>')}</div>` : "";

        card.innerHTML = `
            <a class="card-cover-wrap" href="${escHtml(cardLink)}" target="_blank" rel="noopener">
                ${coverHTML}
                ${ageBadge}
                ${badgeHTML}
            </a>
            <div class="card-body">
                <div class="card-title">${n.titleEn ? escHtml(n.titleEn) : escHtml(n.title)}</div>
                ${n.titleEn ? `<div class="card-title-kr">${escHtml(n.title)}</div>` : ""}
                <div class="card-author" data-author="${escHtml(n.author)}">Author: ${escHtml(n.author)}</div>
                <div class="card-tags">${tagsHTML}</div>
                <div class="card-stats">
                    <span class="stat">👁 ${fmt(n.views)}</span>
                    <span class="stat">❤ ${fmt(n.likes)}</span>
                    <span class="stat">📄 ${fmt(n.chapters)}</span>
                </div>${synopsisHTML}
            </div>
        `;

        // Helper: filter, jump to card's page, scroll to it
        function filterAndFocusCard(novelId, novelSource) {
            const idx = filtered.findIndex((f) => f.id === novelId && (f.source || currentSource) === novelSource);
            if (idx >= 0) {
                currentPage = Math.floor(idx / BATCH) + 1;
                render();
                setTimeout(() => {
                    const cards = resultsEl.querySelectorAll(".novel-card");
                    const cardIdx = idx - (currentPage - 1) * BATCH;
                    if (cards[cardIdx]) {
                        cards[cardIdx].scrollIntoView({ behavior: "smooth", block: "center" });
                    }
                }, 50);
            }
        }

        // Make card tags clickable (add to include filter)

        card.querySelectorAll(".card-tag").forEach((tagEl) => {
            tagEl.addEventListener("click", (e) => {
                e.preventDefault();
                e.stopPropagation();
                const clickedTag = tagEl.dataset.tag;
                const novelId = n.id;
                const novelSource = n.source || currentSource;
                // Toggle: if this tag is the only active tag, clear it and restore scroll
                const totalActive = andTags.size + orTags.size;
                if (totalActive === 1 && (andTags.has(clickedTag) || orTags.has(clickedTag))) {
                    const savedScroll = preTagScrollY;
                    andTags.clear();
                    orTags.clear();
                    focusedCard = null;
                    preTagScrollY = null;
                    tagContainer.querySelectorAll(".tag-chip.active, .tag-chip.active-or").forEach((c) => {
                        c.classList.remove("active");
                        c.classList.remove("active-or");
                    });
                    applyFilters();
                    if (savedScroll != null) {
                        requestAnimationFrame(() => window.scrollTo({ top: savedScroll, behavior: "instant" }));
                    }
                } else {
                    // Save scroll position before applying filter
                    if (totalActive === 0) {
                        preTagScrollY = window.scrollY;
                    }
                    focusedCard = { id: novelId, source: novelSource, tag: clickedTag };
                    addIncludeTag(clickedTag);
                    filterAndFocusCard(novelId, novelSource);
                }
            });
        });

        // Make author clickable (search by author) — toggle off on 2nd click
        const authorEl = card.querySelector(".card-author");
        if (authorEl) {
            authorEl.addEventListener("click", (e) => {
                e.preventDefault();
                e.stopPropagation();
                const novelId = n.id;
                const novelSource = n.source || currentSource;
                // Toggle: if search already matches this author, clear it
                if (searchInput.value === authorEl.dataset.author) {
                    searchInput.value = "";
                    applyFilters();
                } else {
                    searchInput.value = authorEl.dataset.author;
                    applyFilters();
                    filterAndFocusCard(novelId, novelSource);
                }
            });
        }

        return card;
    }

    function render(fade = false) {
        // Cancel pending staggered image loads from previous page
        for (const id of pendingImageTimers) clearTimeout(id);
        pendingImageTimers = [];
        // Abort in-flight image downloads by clearing src
        resultsEl.querySelectorAll("img.card-cover").forEach(img => { img.src = ""; });

        function doRender() {
            const totalPages = Math.max(1, Math.ceil(filtered.length / BATCH));
            if (currentPage > totalPages) currentPage = totalPages;

            const start = (currentPage - 1) * BATCH;
            const end = Math.min(start + BATCH, filtered.length);

            resultCount.textContent = `${filtered.length.toLocaleString()} novel(s) found — page ${currentPage} of ${totalPages}`;

            resultsEl.innerHTML = "";
            for (let i = start; i < end; i++) {
                resultsEl.appendChild(renderCard(filtered[i]));
            }

            // Stagger image loading: load 4 at a time with 100ms gaps
            const imgs = resultsEl.querySelectorAll("img.card-cover[data-src]");
            imgs.forEach((img, idx) => {
                const tid = setTimeout(() => { img.src = img.dataset.src; }, Math.floor(idx / 4) * 100);
                pendingImageTimers.push(tid);
            });

            // Update both pagination bars
            const show = totalPages > 1;
            const pages = buildPageRange(currentPage, totalPages);
            for (const bar of paginationBars) {
                bar.style.display = show ? "" : "none";
                bar.querySelector(".prev-page").disabled = currentPage === 1;
                bar.querySelector(".next-page").disabled = currentPage === totalPages;
                const pi = bar.querySelector(".page-input");
                if (pi) pi.max = totalPages;

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

            if (fade) {
                // Fade back in after DOM rebuild
                requestAnimationFrame(() => resultsEl.classList.remove("fading"));
            }
        }

        if (fade && resultsEl.children.length > 0) {
            resultsEl.classList.add("fading");
            // Wait for fade-out transition, then rebuild and fade in
            setTimeout(doRender, 150);
        } else {
            doRender();
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
    function setupToggle(headerId, containerId, searchInputEl) {
        const header = document.getElementById(headerId);
        const container = document.getElementById(containerId);
        header.addEventListener("click", (e) => {
            // Don't toggle when clicking buttons or inputs inside the header
            if (e.target.closest("button") || e.target.closest("input")) return;
            const collapsed = container.classList.toggle("collapsed");
            // Also toggle the search input visibility
            if (searchInputEl) {
                if (collapsed) {
                    searchInputEl.classList.add("collapsed");
                } else {
                    searchInputEl.classList.remove("collapsed");
                }
            }
            const label = header.querySelector("span");
            label.textContent = label.textContent.replace(/^[▶▼]/, collapsed ? "▶" : "▼");
        });
    }
    setupToggle("tagToggle", "tagContainer", tagSearchInput);
    setupToggle("excludeToggle", "excludeTagContainer", excludeTagSearchInput);

    // === Tag search filtering ===
    function setupTagSearch(searchInput, container, isExclude) {
        const toggleFn = isExclude ? toggleExcludeTag : toggleTag;
        searchInput.addEventListener("input", () => {
            const query = searchInput.value.toLowerCase().trim();
            // Remove previously injected dynamic chips
            container.querySelectorAll(".tag-chip.dynamic").forEach((c) => c.remove());
            // Show/hide the static top-80 chips
            container.querySelectorAll(".tag-chip").forEach((chip) => {
                if (!query) {
                    chip.style.display = "";
                    return;
                }
                const translated = chip.textContent.toLowerCase();
                const original = (chip.dataset.tag || "").toLowerCase();
                chip.style.display = (translated.includes(query) || original.includes(query)) ? "" : "none";
            });
            // If query is long enough, inject dynamic chips for non-top-80 matches
            if (query.length >= 2) {
                let added = 0;
                const entries = Object.entries(allTagCounts).sort((a, b) => b[1] - a[1]);
                for (const [tag, count] of entries) {
                    if (top80Tags.has(tag)) continue; // already a static chip
                    const translated = tl(tag).toLowerCase();
                    const original = tag.toLowerCase();
                    if (!translated.includes(query) && !original.includes(query)) continue;
                    const chip = document.createElement("span");
                    chip.className = "tag-chip dynamic";
                    chip.textContent = `${tl(tag)} (${fmt(count)})`;
                    chip.title = tag;
                    chip.dataset.tag = tag;
                    // Restore active state if already selected
                    if (!isExclude && andTags.has(tag)) chip.classList.add("active");
                    if (!isExclude && orTags.has(tag)) chip.classList.add("active-or");
                    if (isExclude && excludeTags.has(tag)) chip.classList.add("excluded");
                    chip.addEventListener("click", () => toggleFn(tag, chip));
                    container.appendChild(chip);
                    if (++added >= 50) break;
                }
            }
        });
    }
    setupTagSearch(tagSearchInput, tagContainer, false);
    setupTagSearch(excludeTagSearchInput, excludeTagContainer, true);

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

        const goBtn = bar.querySelector(".go-page");
        const pageInput = bar.querySelector(".page-input");
        function jumpToPage() {
            const totalPages = Math.max(1, Math.ceil(filtered.length / BATCH));
            const val = parseInt(pageInput.value);
            if (val >= 1 && val <= totalPages) {
                currentPage = val;
                render();
                window.scrollTo({ top: resultsEl.offsetTop - 80, behavior: "smooth" });
            }
            pageInput.value = "";
        }
        goBtn.addEventListener("click", jumpToPage);
        pageInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") jumpToPage();
        });
    }

    tagModeAnd.addEventListener("click", () => {
        tagMode = "AND";
        tagModeAnd.classList.add("active");
        tagModeOr.classList.remove("active");
    });

    tagModeOr.addEventListener("click", () => {
        tagMode = "OR";
        tagModeOr.classList.add("active");
        tagModeAnd.classList.remove("active");
    });

    clearTagsBtn.addEventListener("click", () => {
        andTags.clear();
        orTags.clear();
        focusedCard = null;
        tagSearchInput.value = "";
        tagContainer.querySelectorAll(".tag-chip.dynamic").forEach((c) => c.remove());
        tagContainer.querySelectorAll(".tag-chip").forEach((c) => {
            c.style.display = "";
            c.classList.remove("active");
            c.classList.remove("active-or");
        });
        updateActiveTagsSummary();
        applyFilters();
    });

    clearExcludeBtn.addEventListener("click", () => {
        excludeTags.clear();
        excludeTagSearchInput.value = "";
        excludeTagContainer.querySelectorAll(".tag-chip.dynamic").forEach((c) => c.remove());
        excludeTagContainer.querySelectorAll(".tag-chip").forEach((c) => {
            c.style.display = "";
            c.classList.remove("excluded");
        });
        updateActiveTagsSummary();
        applyFilters();
    });

    // === DOM ref ===
    const sourceSelect = $("#sourceSelect");

    // === Source configs ===
    const SOURCES = {
        novelpia: {
            dataUrl: "data/novels.json",
            translationsUrl: "data/titles_en.txt",
            descriptionsUrl: "data/descriptions.txt",
            format: "array",
            coverPrefix: "https://novelpia.com",
            linkPrefix: "https://novelpia.com/novel/",
            chunked: true,
            chunkCount: 5,
            chunkPrefix: "data/novelpia_chunk_",
            topUrl: "data/novelpia_top.json.gz",
        },
        kakao: {
            dataUrl: "data/kakao_novels.json",
            translationsUrl: "data/kakao_titles_en.txt",
            format: "array",
            coverPrefix: "",
            linkPrefix: "https://page.kakao.com/content/",
            chunked: true,
            chunkCount: 3,
            chunkPrefix: "data/kakao_chunk_",
        },
        sfacg: {
            dataUrl: "data/sfacg_novels.json",
            translationsUrl: "data/sfacg_titles_en.txt",
            format: "array",
            coverPrefix: "https://rss.sfacg.com/web/novel/images/NovelCover/Big/",
            linkPrefix: "https://book.sfacg.com/Novel/",
            noWeeklyRank: true,
            chunked: true,
            chunkCount: 10,
            chunkPrefix: "data/sfacg_chunk_",
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
            const text = await blob.text();
            resultsEl.innerHTML = `<div class="loading-spinner">Parsing ${(received / 1024 / 1024).toFixed(1)} MB...</div>`;
            return await parseJsonAsync(text);
        } else {
            return await resp.json();
        }
    }

    /** Fetch a .json.gz file, decompress client-side, parse JSON */
    async function fetchGzChunk(url) {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`HTTP ${resp.status} for ${url}`);
        // Use DecompressionStream to decompress gzip on the fly
        const ds = new DecompressionStream("gzip");
        const decompressed = resp.body.pipeThrough(ds);
        const reader = decompressed.getReader();
        const chunks = [];
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            chunks.push(value);
        }
        const blob = new Blob(chunks);
        const text = await blob.text();
        return JSON.parse(text);
    }

    /**
     * Load a chunked+gzipped source progressively.
     * Fires onChunkLoaded after each chunk so the UI can update.
     */
    async function fetchChunkedSource(cfg, sourceName, onChunkLoaded) {
        const { chunkCount, chunkPrefix } = cfg;
        let loaded = 0;
        const chunkPromises = [];

        for (let i = 0; i < chunkCount; i++) {
            const url = `${chunkPrefix}${i}.json.gz`;
            chunkPromises.push(
                fetchGzChunk(url).then((raw) => {
                    loaded++;
                    const novels = parseNovels(raw, sourceName, cfg);
                    onChunkLoaded(novels, loaded, chunkCount);
                    return novels;
                })
            );
        }

        const results = await Promise.all(chunkPromises);
        return results.flat();
    }

    function parseJsonAsync(text) {
        return new Promise((resolve, reject) => {
            try {
                const blob = new Blob([
                    `self.onmessage = function(e) { self.postMessage(JSON.parse(e.data)); };`
                ], { type: "application/javascript" });
                const url = URL.createObjectURL(blob);
                const worker = new Worker(url);
                worker.onmessage = (e) => { URL.revokeObjectURL(url); worker.terminate(); resolve(e.data); };
                worker.onerror = (e) => { URL.revokeObjectURL(url); worker.terminate(); resolve(JSON.parse(text)); };
                worker.postMessage(text);
            } catch (e) {
                resolve(JSON.parse(text));
            }
        });
    }


    function parseNovels(raw, sourceName, cfg) {
        // SFACG format (11 fields): [id, title, author, cover, tags, views, likes, chapters, complete, updated, age]
        // Novelpia/Kakao format (13 fields): [id, title, author, cover, tags, views, likes, chapters, complete, updated, weeklyRank, age, monthlyRank]
        const noRank = cfg.noWeeklyRank;
        return raw.map((r) => {
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
                weeklyRank: noRank ? 0 : (r[10] || 0),
                age: noRank ? (r[10] || 0) : (r[11] || 0),
                monthlyRank: noRank ? 0 : (r[12] || 0),
                dailyRank: noRank ? 0 : (r[13] || 0),
                weeklyRankAdult: noRank ? 0 : (r[14] || 0),
                monthlyRankAdult: noRank ? 0 : (r[15] || 0),
                dailyRankAdult: noRank ? 0 : (r[16] || 0),
                weeklyRankTeen: noRank ? 0 : (r[17] || 0),
                monthlyRankTeen: noRank ? 0 : (r[18] || 0),
                dailyRankTeen: noRank ? 0 : (r[19] || 0),
                synopsis: "",
                titleEn: "",
                source: sourceName,
            };
        });
    }

    async function loadTranslations(novels, cfg, sourceName) {
        try {
            const tResp = await fetch(cfg.translationsUrl);
            if (tResp.ok) {
                const text = await tResp.text();
                const map = {};
                let count = 0;
                for (const line of text.split("\n")) {
                    const parts = line.split("|||");
                    if (parts.length >= 3 && parts[2].trim()) {
                        map[parts[0]] = parts[2].trim();
                        count++;
                    }
                }
                if (count > 0) {
                    for (const n of novels) {
                        const en = map[String(n.id)];
                        if (en) n.titleEn = en;
                    }
                    console.log(`Loaded ${count} title translations for ${sourceName}`);
                }
            }
        } catch (e) {
            // translations not found, skip
        }
    }

    async function loadDescriptions(novels, cfg, sourceName) {
        if (!cfg.descriptionsUrl) return;
        try {
            const resp = await fetch(cfg.descriptionsUrl);
            if (resp.ok) {
                const text = await resp.text();
                const map = {};
                let count = 0;
                for (const line of text.split("\n")) {
                    const parts = line.split("|||");
                    if (parts.length >= 2 && parts[0].trim()) {
                        const id = parts[0].trim();
                        // Format: id|||korean|||english — prefer english if present
                        const raw = (parts.length >= 3 && parts[2].trim())
                            ? parts[2].trim()
                            : parts[1].trim();
                        if (raw) {
                            map[id] = raw.replace(/\\r\\n|\\n/g, "\n");
                            count++;
                        }
                    }
                }
                if (count > 0) {
                    for (const n of novels) {
                        const d = map[String(n.id)];
                        if (d) n.synopsis = d;
                    }
                    console.log(`Loaded ${count} descriptions for ${sourceName}`);
                }
            }
        } catch (e) {
            // descriptions not found, skip
        }
    }

    async function loadSource(source, keepState = false) {
        currentSource = source;
        // Only show loading spinner for sources that have a slow multi-phase load.
        // SFACG and Kakao load fast enough that a spinner just causes a distracting flash.
        if (source === "all" || source === "novelpia") {
            resultsEl.innerHTML = `<div class="loading-spinner">Loading ${source === "all" ? "all sources" : source} database...</div>`;
            for (const bar of paginationBars) bar.style.display = "none";
        }

        // Clear filters (unless restoring state)
        if (!keepState) {
            andTags.clear();
            orTags.clear();
            excludeTags.clear();
            searchInput.value = "";
            currentPage = 1;
        }

        try {
            // Helper: load top rankings instantly, return parsed novels with translations applied
            async function loadTopRankings() {
                const cfg = SOURCES.novelpia;
                const data = await fetchGzChunk(cfg.topUrl);
                const novels = parseNovels(data.novels, "novelpia", cfg);
                // Apply embedded translations
                if (data.translations) {
                    for (const n of novels) {
                        if (data.translations[n.id]) n.titleEn = data.translations[n.id];
                    }
                }
                // Apply embedded descriptions
                if (data.descriptions) {
                    for (const n of novels) {
                        if (data.descriptions[n.id]) {
                            n.synopsis = data.descriptions[n.id].replace(/\\r\\n|\\n/g, "\n");
                        }
                    }
                }
                return novels;
            }

            if (source === "all") {
                // === Instant load: show top rankings immediately ===
                let topNovels = null;
                try {
                    topNovels = await loadTopRankings();
                } catch (e) { /* fall through to normal load */ }

                if (topNovels && topNovels.length > 0) {
                    allNovels = [...topNovels];
                    titleTranslations = {};
                    buildTags(allNovels);
                    applyFilters();
                }

                // === Background: load ALL sources in parallel ===
                const topIds = new Set(topNovels ? topNovels.map((n) => n.id) : []);
                let firstRendered = topNovels && topNovels.length > 0;

                const loadSingle = async (s) => {
                    const cfg = SOURCES[s];
                    let novels;
                    if (cfg.chunked) {
                        novels = await fetchChunkedSource(cfg, s, (chunk, loaded, total) => {
                            if (!firstRendered) {
                                resultsEl.innerHTML = `<div class="loading-spinner">Loading ${s}... chunk ${loaded}/${total}</div>`;
                            } else {
                                resultCount.textContent = `${allNovels.length.toLocaleString()} novel(s) — loading ${s}... ${loaded}/${total}`;
                            }
                        });
                    } else {
                        const raw = await fetchWithProgress(cfg.dataUrl);
                        novels = parseNovels(raw, s, cfg);
                    }
                    await Promise.all([
                        loadTranslations(novels, cfg, s),
                        loadDescriptions(novels, cfg, s),
                    ]);
                    return novels;
                };

                // Start ALL sources loading in parallel immediately
                const novelpiaPromise = loadSingle("novelpia");
                const kakaoPromise = loadSingle("kakao");
                const sfacgPromise = loadSingle("sfacg");

                // Merge each source as it finishes
                const novelpiaNovels = await novelpiaPromise;
                const deduped = novelpiaNovels.filter((n) => !topIds.has(n.id));
                allNovels = [...(topNovels || []), ...deduped];
                // Re-apply translations & descriptions to top novels
                // (browser caches the fetch, so this is effectively free)
                const npiaCfg = SOURCES.novelpia;
                await Promise.all([
                    loadTranslations(allNovels, npiaCfg, "novelpia"),
                    loadDescriptions(allNovels, npiaCfg, "novelpia"),
                ]);
                titleTranslations = {};
                buildTags(allNovels);
                applyFilters();
                firstRendered = true;

                // Await remaining sources (already loading in background)
                const [kakaoNovels, sfacgNovels] = await Promise.all([kakaoPromise, sfacgPromise]);
                allNovels = [...allNovels, ...kakaoNovels, ...sfacgNovels];
                titleTranslations = {};
                buildTags(allNovels);
                applyFilters();
            } else if (source === "novelpia") {
                // === Single source: Novelpia with instant top ===
                let topNovels = null;
                try {
                    topNovels = await loadTopRankings();
                } catch (e) { /* fall through */ }

                if (topNovels && topNovels.length > 0) {
                    allNovels = [...topNovels];
                    titleTranslations = {};
                    buildTags(allNovels);
                    applyFilters();
                }

                const topIds = new Set(topNovels ? topNovels.map((n) => n.id) : []);
                const cfg = SOURCES.novelpia;
                let firstRender = !(topNovels && topNovels.length > 0);
                const allChunkNovels = await fetchChunkedSource(cfg, source, (chunk, loaded, total) => {
                    if (firstRender) {
                        allNovels.push(...chunk);
                        resultsEl.innerHTML = `<div class="loading-spinner">Loading chunk ${loaded}/${total}...</div>`;
                        buildTags(allNovels);
                        applyFilters();
                        firstRender = false;
                    } else {
                        resultCount.textContent = `${allNovels.length.toLocaleString()} novel(s) — loading chunk ${loaded}/${total}`;
                    }
                });
                // Deduplicate and merge
                const deduped = allChunkNovels.filter((n) => !topIds.has(n.id));
                allNovels = [...(topNovels || []), ...deduped];
                await Promise.all([
                    loadTranslations(allNovels, cfg, source),
                    loadDescriptions(allNovels, cfg, source),
                ]);
            } else {
                const cfg = SOURCES[source];
                if (cfg.chunked) {
                    // Progressive chunked loading — show results as chunks arrive
                    allNovels = [];
                    titleTranslations = {};
                    let firstRender = true;
                    const allChunkNovels = await fetchChunkedSource(cfg, source, (chunk, loaded, total) => {
                        allNovels.push(...chunk);
                        resultCount.textContent = `Loading ${source}... chunk ${loaded}/${total}`;
                        // Render after first chunk so user sees results immediately
                        if (firstRender) {
                            firstRender = false;
                            buildTags(allNovels);
                            applyFilters();
                        }
                    });
                    allNovels = allChunkNovels;
                    await Promise.all([
                        loadTranslations(allNovels, cfg, source),
                        loadDescriptions(allNovels, cfg, source),
                    ]);
                } else {
                    const raw = await fetchWithProgress(cfg.dataUrl);
                    allNovels = parseNovels(raw, source, cfg);
                    titleTranslations = {};
                    await Promise.all([
                        loadTranslations(allNovels, cfg, source),
                        loadDescriptions(allNovels, cfg, source),
                    ]);
                }
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
    // === State persistence via URL hash ===
    let _lastHash = window.location.hash;
    function saveState() {
        const state = {};
        if (searchInput.value) state.q = searchInput.value;
        if (sortSelect.value !== "daily") state.sort = sortSelect.value;
        if (orderSelect.value !== "desc") state.order = orderSelect.value;
        if (statusSelect.value !== "all") state.status = statusSelect.value;
        if (audienceSelect.value !== "all") state.audience = audienceSelect.value;
        if (batchSelect.value !== "50") state.batch = batchSelect.value;
        if (currentPage > 1) state.page = currentPage;
        if (sourceSelect.value !== "all") state.src = sourceSelect.value;
        if (andTags.size > 0) state.tags = [...andTags].join(",");
        if (orTags.size > 0) state.ortags = [...orTags].join(",");
        if (excludeTags.size > 0) state.xtags = [...excludeTags].join(",");

        const hash = Object.keys(state).length > 0
            ? "#" + Object.entries(state).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&")
            : "";
        const newHash = hash || window.location.pathname + window.location.search;
        if (_lastHash !== hash) {
            history.pushState(null, "", newHash);
            _lastHash = hash;
        }
    }

    function parseHash(hash) {
        if (!hash) return {};
        const params = {};
        for (const pair of hash.replace(/^#/, "").split("&")) {
            const eq = pair.indexOf("=");
            if (eq > 0) params[pair.slice(0, eq)] = decodeURIComponent(pair.slice(eq + 1));
        }
        return params;
    }

    function restoreFromHash() {
        const params = parseHash(window.location.hash);
        searchInput.value = params.q || "";
        sortSelect.value = params.sort || "daily";
        orderSelect.value = params.order || "desc";
        statusSelect.value = params.status || "all";
        audienceSelect.value = params.audience || "all";
        if (params.batch) { batchSelect.value = params.batch; BATCH = parseInt(params.batch); }
        currentPage = params.page ? parseInt(params.page) : 1;
        if (params.tmode) tagMode = params.tmode;

        // Restore AND tags
        andTags.clear();
        orTags.clear();
        tagContainer.querySelectorAll(".tag-chip.active, .tag-chip.active-or").forEach((c) => {
            c.classList.remove("active");
            c.classList.remove("active-or");
        });
        if (params.tags) {
            for (const t of params.tags.split(",")) {
                andTags.add(t);
                tagContainer.querySelectorAll(".tag-chip").forEach((c) => {
                    if (c.dataset.tag === t) c.classList.add("active");
                });
            }
        }
        // Restore OR tags
        if (params.ortags) {
            for (const t of params.ortags.split(",")) {
                orTags.add(t);
                tagContainer.querySelectorAll(".tag-chip").forEach((c) => {
                    if (c.dataset.tag === t) c.classList.add("active-or");
                });
            }
        }
        excludeTags.clear();
        excludeTagContainer.querySelectorAll(".tag-chip.excluded").forEach((c) => c.classList.remove("excluded"));
        if (params.xtags) {
            for (const t of params.xtags.split(",")) {
                excludeTags.add(t);
                excludeTagContainer.querySelectorAll(".tag-chip").forEach((c) => {
                    if (c.dataset.tag === t) c.classList.add("excluded");
                });
            }
        }

        _lastHash = window.location.hash;
        updateActiveTagsSummary();
        _origApplyFilters();
    }

    function restoreState() {
        const params = parseHash(window.location.hash);
        if (Object.keys(params).length === 0) return false;

        if (params.q) searchInput.value = params.q;
        if (params.sort) sortSelect.value = params.sort;
        if (params.order) orderSelect.value = params.order;
        if (params.status) statusSelect.value = params.status;
        if (params.audience) audienceSelect.value = params.audience;
        if (params.batch) { batchSelect.value = params.batch; BATCH = parseInt(params.batch); }
        if (params.page) currentPage = parseInt(params.page);
        if (params.src) sourceSelect.value = params.src;

        // Tags are restored after data loads (in loadSource callback)
        return params;
    }

    const savedParams = restoreState();
    const initialSource = sourceSelect.value;
    const hasRestoredState = savedParams && Object.keys(savedParams).length > 0;

    // Wrap original applyFilters to auto-save state
    const _origApplyFilters = applyFilters;
    applyFilters = function() {
        _origApplyFilters();
        saveState();
    };

    // After data loads, restore tags then re-apply
    const _origLoadSource = loadSource;
    if (hasRestoredState) {
        loadSource = async function(source, keepState) {
            await _origLoadSource(source, true);
            if (savedParams.tags) {
                for (const t of savedParams.tags.split(",")) {
                    andTags.add(t);
                    tagContainer.querySelectorAll(".tag-chip").forEach((c) => {
                        if (c.dataset.tag === t) c.classList.add("active");
                    });
                }
            }
            if (savedParams.ortags) {
                for (const t of savedParams.ortags.split(",")) {
                    orTags.add(t);
                    tagContainer.querySelectorAll(".tag-chip").forEach((c) => {
                        if (c.dataset.tag === t) c.classList.add("active-or");
                    });
                }
            }
            if (savedParams.xtags) {
                for (const t of savedParams.xtags.split(",")) {
                    excludeTags.add(t);
                    excludeTagContainer.querySelectorAll(".tag-chip").forEach((c) => {
                        if (c.dataset.tag === t) c.classList.add("excluded");
                    });
                }
            }
            // Re-apply with restored state
            updateActiveTagsSummary();
            _origApplyFilters();
            saveState();
            // Only restore once
            loadSource = _origLoadSource;
        };
    }

    // Source change listener
    sourceSelect.addEventListener("change", () => {
        loadSource(sourceSelect.value);
        saveState();
    });

    loadSource(initialSource, hasRestoredState);
})();
