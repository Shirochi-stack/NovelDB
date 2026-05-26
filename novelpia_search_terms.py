"""Shared Novelpia search coverage terms.

Used by the authenticated rescrape script, no-auth rescrape script, and the
desktop "scrape all novels" path so their catalog sweep coverage stays aligned.
"""

BASE_SEARCH_TAGS = [
    "판타지", "현대", "패러디", "하렘", "라이트노벨", "일상", "로맨스",
    "현대판타지", "TS", "먼치킨", "중세", "전생", "집착", "아카데미",
    "고수위", "드라마", "SF", "순애", "빙의", "피폐", "성장", "착각",
    "무협", "블루아카이브", "후회", "코미디", "이세계", "기타", "백합",
    "회귀", "약피폐", "아포칼립스", "얀데레", "게임", "환생", "남성향",
    "헌터", "조교", "복수", "인터넷방송", "남녀역전", "대체역사", "모험",
    "원신", "상태창", "공포", "생존", "전쟁", "가면라이더", "액션",
    "디스토피아",
]

EXTRA_SEARCH_TAGS = [
    "다크판타지", "FATE", "NTL", "힐링", "히로아카", "2차창작", "19금",
    "여주인공", "다중", "떡타지", "스포츠", "능욕", "근친", "NTR",
    "FGO", "구원", "마법", "히어로", "인외", "용사", "원피스",
    "써줘용", "러브코미디", "프문", "림버스", "라오루", "데어라",
    "주술회전", "팬픽", "최면", "마법소녀", "좀비", "퓨전",
    "로보토미", "노맨스", "빌런", "로맨스판타지", "성장형먼치킨",
    "단편", "블아", "갤러리", "던전", "미스터리", "천재", "SM",
    "사이버펑크", "시스템", "전독시", "스릴러", "포켓몬",
]

SEARCH_TAGS = list(dict.fromkeys(BASE_SEARCH_TAGS + EXTRA_SEARCH_TAGS))

SWEEP_CHARS = list(dict.fromkeys(
    list("가나다라마바사아자차카타파하")
    + list("ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎ")
    + list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    + list("0123456789")
))

ALL_SEARCH_TERMS = list(dict.fromkeys(SEARCH_TAGS + SWEEP_CHARS))
DEFAULT_SEARCH_QUERY_COUNT = len(ALL_SEARCH_TERMS)
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
