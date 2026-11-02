"""lightnovel-crawler's data models (Novel, Volume, Chapter, SearchResult), as attribute dicts.

Same names, fields and behaviour as the originals, without pydantic and python-box.
"""

from typing import Any, Dict, List, Optional

__all__ = ["Novel", "Volume", "Chapter", "SearchResult", "CombinedSearchResult"]


class _ModelBox(dict):
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    def __delattr__(self, name: str) -> None:
        self.pop(name, None)

    def to_dict(self) -> Dict:
        return dict(self)


class SearchResult(_ModelBox):
    def __init__(self, title: str, url: str, info: str = "", **kwargs: Any) -> None:
        super().__init__(title=str(title), url=str(url), info=str(info), **kwargs)


class CombinedSearchResult(_ModelBox):
    def __init__(self, id: str, title: str, novels: Optional[List[SearchResult]] = None, **kwargs: Any) -> None:
        super().__init__(id=id, title=str(title), novels=list(novels or []), **kwargs)


class Chapter(_ModelBox):
    def __init__(self, id: int, url: str = "", title: str = "", volume: Optional[int] = None,
                 body: Optional[str] = None, images: Optional[Dict[str, str]] = None, success: bool = False,
                 crawler_version: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(id=id, url=url, title=title, volume=volume, body=body, images=dict(images or {}),
                         success=success, crawler_version=crawler_version, **kwargs)


class Volume(_ModelBox):
    def __init__(self, id: int, title: str = "", chapters: int = 0, crawler_version: Optional[str] = None,
                 **kwargs: Any) -> None:
        super().__init__(id=id, title=title, chapters=chapters, crawler_version=crawler_version, **kwargs)


class Novel(_ModelBox):
    def __init__(self, url: str, title: str = "", cover_url: str = "", volumes: Optional[List[Volume]] = None,
                 chapters: Optional[List[Chapter]] = None, author: str = "", synopsis: str = "",
                 tags: Optional[List[str]] = None, language: Optional[str] = None, is_manga: Optional[bool] = None,
                 is_mtl: Optional[bool] = None, is_rtl: Optional[bool] = None, crawler_version: Optional[str] = None,
                 **kwargs: Any) -> None:
        super().__init__(crawler_version=crawler_version, url=url, title=title, cover_url=cover_url, author=author,
                         language=language, is_manga=is_manga, is_mtl=is_mtl, is_rtl=is_rtl, tags=list(tags or []),
                         synopsis=synopsis, volumes=list(volumes or []), chapters=list(chapters or []), **kwargs)

    def add_volume(self, id: Optional[int] = None, title: str = "", **kwargs: Any) -> Volume:
        volume = Volume(id=len(self.volumes) + 1 if id is None else id, title=title,
                        crawler_version=self.crawler_version, **kwargs)
        self.volumes.append(volume)
        return volume

    def add_chapter(self, id: Optional[int] = None, url: str = "", title: str = "", volume: Optional[int] = None,
                    **kwargs: Any) -> Chapter:
        chapter = Chapter(id=len(self.chapters) + 1 if id is None else id, url=url, title=title, volume=volume,
                          crawler_version=self.crawler_version, **kwargs)
        self.chapters.append(chapter)
        return chapter
