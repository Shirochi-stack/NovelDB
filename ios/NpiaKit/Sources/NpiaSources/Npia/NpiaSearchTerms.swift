import Foundation

/// Port of `npia_search_terms.py`: the tag and sweep terms the "scrape all novels" search
/// walks through, so desktop and mobile cover the catalog the same way.
public enum NpiaSearchTerms {
    public static let baseTags: [String] = [
        "판타지", "현대", "패러디", "하렘", "라이트노벨", "일상",
        "로맨스", "현대판타지", "TS", "먼치킨", "중세", "전생",
        "집착", "아카데미", "고수위", "드라마", "SF", "순애",
        "빙의", "피폐", "성장", "착각", "무협", "블루아카이브",
        "후회", "코미디", "이세계", "기타", "백합", "회귀",
        "약피폐", "아포칼립스", "얀데레", "게임", "환생", "남성향",
        "헌터", "조교", "복수", "인터넷방송", "남녀역전", "대체역사",
        "모험", "원신", "상태창", "공포", "생존", "전쟁",
        "가면라이더", "액션", "디스토피아",
    ]

    public static let extraTags: [String] = [
        "다크판타지", "FATE", "NTL", "힐링", "히로아카", "2차창작",
        "19금", "여주인공", "다중", "떡타지", "스포츠", "능욕",
        "근친", "NTR", "FGO", "구원", "마법", "히어로",
        "인외", "용사", "원피스", "써줘용", "러브코미디", "프문",
        "림버스", "라오루", "데어라", "주술회전", "팬픽", "최면",
        "마법소녀", "좀비", "퓨전", "로보토미", "노맨스", "빌런",
        "로맨스판타지", "성장형먼치킨", "단편", "블아", "갤러리", "던전",
        "미스터리", "천재", "SM", "사이버펑크", "시스템", "전독시",
        "스릴러", "포켓몬",
    ]

    /// `SEARCH_TAGS`: base then extra tags, first occurrence wins.
    public static let searchTags: [String] = unique(baseTags + extraTags)

    /// `SWEEP_CHARS`: Hangul syllables, Hangul consonants, A-Z, then 0-9.
    public static let sweepChars: [String] = {
        let groups: [String] = [
            "가나다라마바사아자차카타파하",
            "ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎ",
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            "0123456789",
        ]
        var characters: [String] = []
        for group in groups {
            for character in group {
                characters.append(String(character))
            }
        }
        return unique(characters)
    }()

    /// `ALL_SEARCH_TERMS`: tags first, then the sweep characters.
    public static let allTerms: [String] = unique(searchTags + sweepChars)

    /// `DEFAULT_SEARCH_QUERY_COUNT`: one query per term.
    public static let defaultQueryCount = allTerms.count

    /// `RETRYABLE_STATUS_CODES`: statuses the all-novels sweep retries with backoff.
    public static let retryableStatusCodes: Set<Int> = [408, 429, 500, 502, 503, 504]

    /// Order-preserving de-duplication (`dict.fromkeys`).
    static func unique(_ values: [String]) -> [String] {
        var seen = Set<String>()
        return values.filter { seen.insert($0).inserted }
    }

    /// Clamps a requested query count to 1...`defaultQueryCount`.
    public static func clampQueryCount(_ value: Int) -> Int {
        max(1, min(value, defaultQueryCount))
    }

    /// `COMMON_TAGS` from the desktop tag dialog, in the same order.
    public static let commonTags: [NpiaCommonTag] = [
        NpiaCommonTag(value: "TS", english: "Genderbend"),
        NpiaCommonTag(value: "약피폐", english: "Moderate Suffering"),
        NpiaCommonTag(value: "먼치킨", english: "Munchkin"),
        NpiaCommonTag(value: "GL", english: "Girls Love"),
        NpiaCommonTag(value: "BL", english: "Boys Love"),
        NpiaCommonTag(value: "백합", english: "Yuri"),
        NpiaCommonTag(value: "추리", english: "Mystery"),
        NpiaCommonTag(value: "로맨스", english: "Romance"),
        NpiaCommonTag(value: "하렘", english: "Harem"),
        NpiaCommonTag(value: "역하렘", english: "Reverse Harem"),
        NpiaCommonTag(value: "\u{b178}\u{bca8}\u{d53c}\u{c544}", label: "npia", english: "Npia"),
        NpiaCommonTag(value: "판타지", english: "Fantasy"),
        NpiaCommonTag(value: "현대", english: "Hyundai/Modern"),
        NpiaCommonTag(value: "오리지널", english: "Original"),
        NpiaCommonTag(value: "순애", english: "Pure Love"),
        NpiaCommonTag(value: "일상", english: "Slice of Life"),
        NpiaCommonTag(value: "용사", english: "Hero"),
        NpiaCommonTag(value: "학교", english: "School"),
        NpiaCommonTag(value: "19금", english: "R-19"),
        NpiaCommonTag(value: "수녀", english: "Nun"),
        NpiaCommonTag(value: "학원", english: "Academy"),
        NpiaCommonTag(value: "썰만화", english: "Ssulmanhwa"),
        NpiaCommonTag(value: "방송", english: "Broadcast"),
        NpiaCommonTag(value: "SF", english: "Sci-Fi"),
        NpiaCommonTag(value: "비극", english: "Tragedy"),
        NpiaCommonTag(value: "악피폐", english: "Dark Suffering"),
        NpiaCommonTag(value: "회귀", english: "Regression"),
        NpiaCommonTag(value: "빙의", english: "Possession"),
        NpiaCommonTag(value: "환생", english: "Reincarnation"),
        NpiaCommonTag(value: "헌터", english: "Hunter"),
        NpiaCommonTag(value: "현대판타지", english: "Modern Fantasy"),
        NpiaCommonTag(value: "로맨스판타지", english: "Romance Fantasy"),
        NpiaCommonTag(value: "무협", english: "Martial Arts"),
        NpiaCommonTag(value: "게임판타지", english: "Game Fantasy"),
        NpiaCommonTag(value: "호러", english: "Horror"),
        NpiaCommonTag(value: "스포츠", english: "Sports"),
        NpiaCommonTag(value: "라이트노벨", english: "Light Novel"),
        NpiaCommonTag(value: "팬픽", english: "Fanfic"),
        NpiaCommonTag(value: "성장", english: "Growth"),
        NpiaCommonTag(value: "후회물", english: "Regret"),
        NpiaCommonTag(value: "집착물", english: "Obsession"),
        NpiaCommonTag(value: "피카레스크", english: "Picaresque"),
        NpiaCommonTag(value: "착각", english: "Misunderstanding"),
        NpiaCommonTag(value: "나데나데", english: "Nade Nade"),
    ]

    /// The grid entry for a stored search value.
    static func commonTag(for value: String) -> NpiaCommonTag? {
        commonTags.first { $0.value == value }
    }

    /// What the app shows for a stored tag: the grid label, or the tag itself for custom tags.
    public static func displayName(forTag value: String) -> String {
        commonTag(for: value)?.label ?? value
    }
}

/// One entry of the tag grid.
public struct NpiaCommonTag: Sendable, Hashable, Identifiable {
    /// The text sent to the site as the tag search value.
    public let value: String
    /// What the grid shows for the tag; the search value itself unless it is overridden.
    public let label: String
    public let english: String

    public init(value: String, label: String? = nil, english: String) {
        self.value = value
        self.label = label ?? value
        self.english = english
    }

    public var id: String { value }

    /// "<tag> (<English name>)", the way the desktop grid labels it.
    public var title: String { "\(label) (\(english))" }
}
