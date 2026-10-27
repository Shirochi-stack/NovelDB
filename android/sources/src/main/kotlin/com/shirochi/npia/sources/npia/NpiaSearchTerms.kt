package com.shirochi.npia.sources.npia

/**
 * Search coverage for the tag search and "scrape all novels", ported from `npia_search_terms.py`
 * (the desktop sweep, the authenticated rescrape and the no-auth rescrape share these lists).
 */
object NpiaSearchTerms {
    val BASE_SEARCH_TAGS: List<String> = listOf(
        "판타지", "현대", "패러디", "하렘", "라이트노벨", "일상", "로맨스", "현대판타지",
        "TS", "먼치킨", "중세", "전생", "집착", "아카데미", "고수위", "드라마",
        "SF", "순애", "빙의", "피폐", "성장", "착각", "무협", "블루아카이브",
        "후회", "코미디", "이세계", "기타", "백합", "회귀", "약피폐", "아포칼립스",
        "얀데레", "게임", "환생", "남성향", "헌터", "조교", "복수", "인터넷방송",
        "남녀역전", "대체역사", "모험", "원신", "상태창", "공포", "생존", "전쟁",
        "가면라이더", "액션", "디스토피아",
    )

    val EXTRA_SEARCH_TAGS: List<String> = listOf(
        "다크판타지", "FATE", "NTL", "힐링", "히로아카", "2차창작", "19금", "여주인공",
        "다중", "떡타지", "스포츠", "능욕", "근친", "NTR", "FGO", "구원",
        "마법", "히어로", "인외", "용사", "원피스", "써줘용", "러브코미디", "프문",
        "림버스", "라오루", "데어라", "주술회전", "팬픽", "최면", "마법소녀", "좀비",
        "퓨전", "로보토미", "노맨스", "빌런", "로맨스판타지", "성장형먼치킨", "단편", "블아",
        "갤러리", "던전", "미스터리", "천재", "SM", "사이버펑크", "시스템", "전독시",
        "스릴러", "포켓몬",
    )

    val SEARCH_TAGS: List<String> = (BASE_SEARCH_TAGS + EXTRA_SEARCH_TAGS).distinct()

    /** Hangul syllables and consonants, Latin capitals and digits: one-character queries that reach what the tags miss. */
    val SWEEP_CHARS: List<String> = (
        "가나다라마바사아자차카타파하" +
            "ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎ" +
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ" +
            "0123456789"
        ).map { it.toString() }.distinct()

    /** Tags first, then the sweep characters, without repeats. */
    val ALL_SEARCH_TERMS: List<String> = (SEARCH_TAGS + SWEEP_CHARS).distinct()

    /** Every term: "Search queries" defaults to this and cannot go above it. */
    val DEFAULT_SEARCH_QUERY_COUNT: Int = ALL_SEARCH_TERMS.size

    val RETRYABLE_STATUS_CODES: Set<Int> = setOf(408, 429, 500, 502, 503, 504)
}

/** One entry of the tag picker: the tag as the site spells it and its English name. */
data class CommonTag(
    val tag: String,
    val english: String,
    /** What the picker shows in place of `tag`; defaults to the tag itself. */
    val label: String = tag,
) {
    /** "<tag> (<english>)", the checkbox text of the desktop dialog. */
    val display: String get() = "$label ($english)"
}

/** The desktop dialog's `COMMON_TAGS`, pinned and recommended tags first. */
object NpiaCommonTags {
    val ALL: List<CommonTag> = listOf(
        CommonTag("TS", "Genderbend"),
        CommonTag("약피폐", "Moderate Suffering"),
        CommonTag("먼치킨", "Munchkin"),
        CommonTag("GL", "Girls Love"),
        CommonTag("BL", "Boys Love"),
        CommonTag("백합", "Yuri"),
        CommonTag("추리", "Mystery"),
        CommonTag("로맨스", "Romance"),
        CommonTag("하렘", "Harem"),
        CommonTag("역하렘", "Reverse Harem"),
        CommonTag("\uB178\uBCA8\uD53C\uC544", "Npia", label = "npia"),
        CommonTag("판타지", "Fantasy"),
        CommonTag("현대", "Hyundai/Modern"),
        CommonTag("오리지널", "Original"),
        CommonTag("순애", "Pure Love"),
        CommonTag("일상", "Slice of Life"),
        CommonTag("용사", "Hero"),
        CommonTag("학교", "School"),
        CommonTag("19금", "R-19"),
        CommonTag("수녀", "Nun"),
        CommonTag("학원", "Academy"),
        CommonTag("썰만화", "Ssulmanhwa"),
        CommonTag("방송", "Broadcast"),
        CommonTag("SF", "Sci-Fi"),
        CommonTag("비극", "Tragedy"),
        CommonTag("악피폐", "Dark Suffering"),
        CommonTag("회귀", "Regression"),
        CommonTag("빙의", "Possession"),
        CommonTag("환생", "Reincarnation"),
        CommonTag("헌터", "Hunter"),
        CommonTag("현대판타지", "Modern Fantasy"),
        CommonTag("로맨스판타지", "Romance Fantasy"),
        CommonTag("무협", "Martial Arts"),
        CommonTag("게임판타지", "Game Fantasy"),
        CommonTag("호러", "Horror"),
        CommonTag("스포츠", "Sports"),
        CommonTag("라이트노벨", "Light Novel"),
        CommonTag("팬픽", "Fanfic"),
        CommonTag("성장", "Growth"),
        CommonTag("후회물", "Regret"),
        CommonTag("집착물", "Obsession"),
        CommonTag("피카레스크", "Picaresque"),
        CommonTag("착각", "Misunderstanding"),
        CommonTag("나데나데", "Nade Nade"),
    )

    private val english: Map<String, String> = ALL.associate { it.tag to it.english }

    /** English name of a known tag, or null. */
    fun englishName(tag: String): String? = english[tag]

    /** The tag that only adults can see; disabled when the age filter is not "All". */
    const val ADULT_TAG = "19금"
}
