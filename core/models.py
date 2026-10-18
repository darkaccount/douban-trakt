from dataclasses import dataclass
from typing import Optional


@dataclass
class MovieRecord:
    title: str
    year: Optional[int] = None
    imdb_id: Optional[str] = None
    douban_id: Optional[str] = None
    trakt_id: Optional[int] = None
    tmdb_id: Optional[int] = None
    watched_at: Optional[str] = None  # YYYY-MM-DD or ISO 8601
    rating: Optional[int] = None
    media_type: str = "movie"  # 'movie' or 'show'
    source: str = "unknown"  # 'douban' or 'trakt'

    def __hash__(self):
        if self.imdb_id:
            return hash(self.imdb_id)
        if self.douban_id:
            return hash(f"douban_{self.douban_id}")
        return hash((self.title, self.year))

    def __eq__(self, other):
        if not isinstance(other, MovieRecord):
            return False
        if self.imdb_id and other.imdb_id:
            return self.imdb_id == other.imdb_id
        if self.douban_id and other.douban_id:
            return self.douban_id == other.douban_id
        return self.title == other.title and self.year == other.year
