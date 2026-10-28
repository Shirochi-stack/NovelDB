"""npia tag search: the tag list, query labels and saved ID lists (from the old main window).

The searching itself is `DownloaderCore.fetch_novels_by_tags` / `fetch_all_novels` /
`fetch_top100_rankings`; `TagSearch` runs one of them with a stop switch and a log.
"""

from __future__ import annotations

import os
from typing import Callable, Iterable, Optional

from npia_search_terms import DEFAULT_SEARCH_QUERY_COUNT

# Tags shown as chips: (tag as the site spells it, English label). The site's own tag is stored as
# Unicode escapes (AGENTS.md: wire values keep working under the codename policy).
COMMON_TAGS = [
    # -- Pinned --
    ("TS", "Genderbend"), ("약피폐", "Moderate Suffering"), ("먼치킨", "Munchkin"),
    ("GL", "Girls Love"), ("BL", "Boys Love"),
    ("백합", "Yuri"), ("추리", "Mystery"), ("로맨스", "Romance"),
    ("하렘", "Harem"), ("역하렘", "Reverse Harem"),
    # -- Recommended --
    ("\uB178\uBCA8\uD53C\uC544", "Npia"), ("판타지", "Fantasy"), ("현대", "Hyundai/Modern"),
    ("오리지널", "Original"), ("순애", "Pure Love"), ("일상", "Slice of Life"), ("용사", "Hero"),
    # -- Popular --
    ("학교", "School"), ("19금", "R-19"), ("수녀", "Nun"),
    ("학원", "Academy"), ("썰만화", "Ssulmanhwa"), ("방송", "Broadcast"),
    # -- Genre / Trope --
    ("SF", "Sci-Fi"), ("비극", "Tragedy"), ("악피폐", "Dark Suffering"), ("회귀", "Regression"), ("빙의", "Possession"),
    ("환생", "Reincarnation"), ("헌터", "Hunter"),
    ("현대판타지", "Modern Fantasy"), ("로맨스판타지", "Romance Fantasy"), ("무협", "Martial Arts"),
    ("게임판타지", "Game Fantasy"), ("호러", "Horror"), ("스포츠", "Sports"),
    ("라이트노벨", "Light Novel"), ("팬픽", "Fanfic"),
    ("성장", "Growth"), ("후회물", "Regret"), ("집착물", "Obsession"), ("피카레스크", "Picaresque"),
    ("착각", "Misunderstanding"), ("나데나데", "Nade Nade"),
]

TAG_LABELS = dict(COMMON_TAGS)
SITE_TAG = next(tag for tag, label in COMMON_TAGS if label == "Npia")
ADULT_TAG = "19금"
AGE_FILTERS = {"": "All", "15": "Non-adult only", "19": "Adult only"}


def display_name(tag: str) -> str:
    """Chip text: "판타지 (Fantasy)"; the site's own tag shows its codename."""
    if tag == SITE_TAG:
        return "npia"
    label = TAG_LABELS.get(tag)
    return f"{tag} ({label})" if label and label != tag else tag


def tag_label(groups: Optional[Iterable[Iterable[str]]]) -> str:
    """File-name label for a query: English labels for Korean tags, "_OR_" within, "+" between groups."""
    if groups is None:
        return "all_novels"
    parts = []
    for group in groups:
        names = [t if t.isascii() else TAG_LABELS.get(t, t).replace(" ", "_") for t in group]
        parts.append("_OR_".join(names))
    label = "+".join(parts)
    return "".join(c if c not in '/\\:*?"<>|' else "_" for c in label) or "tags"


def groups_for(query_groups, selected: Iterable[str], custom: str) -> list:
    """The groups to search: the query builder's groups, else every selected/custom tag as its own group."""
    groups = [list(g) for g in query_groups if g]
    if groups:
        return groups
    tags = list(selected) + [t.strip() for t in (custom or "").split(",") if t.strip()]
    return [[t] for t in dict.fromkeys(tags)]


def top100_summary(rankings: dict) -> tuple[set, str]:
    """(unique ids, "Top 100: N unique novels" plus one line per audience)."""
    unique, by_audience = set(), {}
    for label, ids in rankings.items():
        unique.update(ids)
        parts = label.split()
        audience = parts[-1] if len(parts) > 1 else "all"
        by_audience.setdefault(audience, []).append(f"{parts[0] if parts else label}: {len(ids)}")
    lines = [f"Top 100: {len(unique)} unique novels"] + [f"  {a}: {', '.join(p)}" for a, p in by_audience.items()]
    return unique, "\n".join(lines)


def write_ids(path: str, ids: Iterable) -> int:
    ids = list(ids)
    with open(path, "w", encoding="utf-8") as handle:
        handle.writelines(f"{novel_id}\n" for novel_id in ids)
    return len(ids)


def write_top100(rankings: dict, path: str = "", folder: str = "") -> list:
    """One file with a section per ranking (`path`), or one file per ranking in `folder`."""
    written = []
    if path:
        with open(path, "w", encoding="utf-8") as handle:
            for label, ids in rankings.items():
                handle.write(f"# {label.title()} Top 100\n")
                handle.writelines(f"{novel_id}\n" for novel_id in ids)
                handle.write("\n")
        written.append(path)
    elif folder:
        for label, ids in rankings.items():
            if ids:
                target = os.path.join(folder, label.replace(" ", "_") + "_top100.txt")
                write_ids(target, ids)
                written.append(target)
    return written


class TagSearch:
    """One search at a time on its own `DownloaderCore` (so Stop never touches a download)."""

    def __init__(self, auth, log: Callable[[str], None], core_factory=None) -> None:
        if core_factory is None:
            from downloader_core import DownloaderCore as core_factory
        self.core = core_factory(auth, log)
        self.log = log

    @property
    def stopped(self) -> bool:
        return bool(self.core.stop_signal)

    def stop(self) -> None:
        self.core.stop_signal = True

    def search(self, groups, age_filter: str = "", delay: float = 2.0, threads: int = 1) -> list:
        self.core.stop_signal = False
        return list(self.core.fetch_novels_by_tags(groups, delay=max(0.0, delay), age_filter=age_filter,
                                                    mode="GROUPS", threads=max(1, threads)))

    def scrape_all(self, age_filter: str = "", delay: float = 2.0, threads: int = 1,
                   max_queries: int = DEFAULT_SEARCH_QUERY_COUNT) -> list:
        self.core.stop_signal = False
        queries = max(1, min(int(max_queries), DEFAULT_SEARCH_QUERY_COUNT))
        return list(self.core.fetch_all_novels(delay=max(0.0, delay), age_filter=age_filter,
                                               max_queries=queries, threads=max(1, threads)))

    def top100(self, age_filter: str = "") -> dict:
        self.core.stop_signal = False
        self.log("--- Fetching Top 100 Rankings ---")
        return dict(self.core.fetch_top100_rankings(age_filter=age_filter))
