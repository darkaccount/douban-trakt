from datetime import datetime
from typing import List, Dict, Any, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from .models import MovieRecord
except ImportError:
    from models import MovieRecord


class TraktClient:
    """Trakt API Client with Scheme 1 smart caching (last_activities) and full pagination."""

    BASE_URL = "https://api.trakt.tv"

    def __init__(self, client_id: str, access_token: str):
        self.client_id = client_id.strip()
        self.access_token = access_token.strip()
        self._username: Optional[str] = None
        self._progress_cache: Dict[str, Any] = {}
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
        })

    def test_connection(self) -> Dict[str, Any]:
        """Test API connection and return authenticated user info."""
        url = f"{self.BASE_URL}/users/me"
        response = self.session.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            self._username = data.get("username")
            return data
        elif response.status_code in (401, 403):
            raise ValueError(f"Trakt 认证失败 (HTTP {response.status_code})。请检查 TRAKT_ACCESS_TOKEN 或 TRAKT_CLIENT_ID 是否过期或正确。")
        else:
            raise RuntimeError(f"Trakt API 请求失败 (HTTP {response.status_code}): {response.text}")

    def get_username(self) -> str:
        """Get authenticated username."""
        if self._username:
            return self._username
        data = self.test_connection()
        return data.get("username", "unknown")

    def get_last_activities(self) -> Dict[str, Any]:
        """Fetch Trakt last activities timestamps (ultra lightweight ~1KB)."""
        url = f"{self.BASE_URL}/sync/last_activities"
        resp = self.session.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return {}

    def get_translations_zh(self, id_or_imdb: str, media_type: str = "movie") -> List[str]:
        """Fetch Chinese translation titles from Trakt."""
        endpoint = "movies" if media_type == "movie" else "shows"
        url = f"{self.BASE_URL}/{endpoint}/{id_or_imdb}/translations/zh"
        try:
            resp = self.session.get(url, timeout=12)
            if resp.status_code == 200:
                titles = []
                for item in resp.json():
                    t = item.get("title", "").strip()
                    if t and t not in titles:
                        titles.append(t)
                return titles
        except Exception:
            pass
        return []

    def _fetch_all_paginated(self, endpoint: str, limit: int = 250) -> List[Dict]:
        """Helper to fetch all items across all pages using Trakt pagination headers."""
        items: List[Dict] = []
        page = 1
        while True:
            url = f"{self.BASE_URL}/{endpoint}"
            params = {"extended": "full", "page": page, "limit": limit}
            resp = self.session.get(url, params=params, timeout=25)
            if resp.status_code != 200:
                print(f"获取 Trakt {endpoint} 第 {page} 页失败 (HTTP {resp.status_code}): {resp.text}")
                break

            data = resp.json()
            if not data:
                break
            items.extend(data)

            # Check total pages from header
            page_count_header = resp.headers.get("x-pagination-page-count")
            if page_count_header:
                total_pages = int(page_count_header)
                if page >= total_pages:
                    break
            else:
                if len(data) < limit:
                    break
            page += 1

        return items

    def get_watched_movies(self) -> List[MovieRecord]:
        """Fetch all watched movies from Trakt account (all pages)."""
        data = self._fetch_all_paginated("sync/watched/movies")
        movies: List[MovieRecord] = []
        for item in data:
            m = item.get("movie", {})
            ids = m.get("ids", {})
            watched_at = item.get("last_watched_at")

            record = MovieRecord(
                title=m.get("title", ""),
                year=m.get("year"),
                imdb_id=ids.get("imdb"),
                trakt_id=ids.get("trakt"),
                tmdb_id=ids.get("tmdb"),
                watched_at=watched_at,
                media_type="movie",
                source="trakt",
            )
            movies.append(record)

        return movies

    def get_watched_shows(self) -> List[MovieRecord]:
        """Fetch all watched TV shows from Trakt account (all pages)."""
        data = self._fetch_all_paginated("sync/watched/shows")
        shows: List[MovieRecord] = []
        for item in data:
            s = item.get("show", {})
            ids = s.get("ids", {})
            watched_at = item.get("last_watched_at")

            record = MovieRecord(
                title=s.get("title", ""),
                year=s.get("year"),
                imdb_id=ids.get("imdb"),
                trakt_id=ids.get("trakt"),
                tmdb_id=ids.get("tmdb"),
                watched_at=watched_at,
                media_type="show",
                source="trakt",
            )
            shows.append(record)

        return shows

    def get_all_watched(self, db: Optional[Any] = None, force_refresh: bool = False) -> List[MovieRecord]:
        """
        Fetch both watched movies and watched TV shows.
        Uses Trakt Scheme 1: Probes /sync/last_activities to determine if remote
        data changed before making full paginated requests, saving 99% of network calls.
        """
        username = self.get_username()

        # Scheme 1: Check last_activities timestamp
        if db and not force_refresh:
            activities = self.get_last_activities()
            movies_act = activities.get("movies", {}).get("watched_at")
            episodes_act = activities.get("episodes", {}).get("watched_at")

            cached_meta = db.get_trakt_sync_meta(username)
            cached_items = db.get_user_trakt_items(username)

            if cached_meta and cached_items:
                if (
                    cached_meta.get("last_movies_watched_at") == movies_act
                    and cached_meta.get("last_episodes_watched_at") == episodes_act
                ):
                    print(f"Trakt 账号 [{username}] 未检测到新观影变动（命中 last_activities 探测），直接加载本地缓存 {len(cached_items)} 条。")
                    return cached_items

        print(f"Trakt 账号 [{username}] 正在全量拉取远程数据（电影 + 电视剧）...")
        movies = self.get_watched_movies()
        shows = self.get_watched_shows()
        all_items = movies + shows
        print(f"Trakt 数据拉取完成：电影 {len(movies)} 部，剧集 {len(shows)} 部，总计 {len(all_items)} 条。")

        # Save to local cache
        if db:
            db.save_user_trakt_items(username, all_items)
            activities = self.get_last_activities()
            db.save_trakt_sync_meta(
                username,
                activities.get("movies", {}).get("watched_at"),
                activities.get("episodes", {}).get("watched_at"),
            )

        return all_items

    @staticmethod
    def _format_watched_at(date_str: Optional[str]) -> Optional[str]:
        """Normalize date string to ISO 8601 UTC timestamp."""
        if not date_str:
            return None
        date_str = date_str.strip()
        # If it's YYYY-MM-DD
        if len(date_str) == 10 and date_str.count("-") == 2:
            return f"{date_str}T12:00:00.000Z"
        # If already ISO
        if "T" in date_str:
            return date_str
        return None

    def add_watched_items(self, items: List[MovieRecord], batch_size: int = 50) -> Dict[str, Any]:
        """
        Add movies and shows to Trakt history.
        Only syncs items that are not already watched, with their original watch date.
        """
        url = f"{self.BASE_URL}/sync/history"
        total_added_movies = 0
        total_added_shows = 0
        total_existing = 0

        for i in range(0, len(items), batch_size):
            batch = items[i : i + batch_size]
            payload_movies = []
            payload_shows = []

            for item in batch:
                ids: Dict[str, Any] = {}
                if item.imdb_id:
                    ids["imdb"] = item.imdb_id
                if item.tmdb_id:
                    ids["tmdb"] = item.tmdb_id
                if item.trakt_id:
                    ids["trakt"] = item.trakt_id

                if not ids:
                    continue

                obj: Dict[str, Any] = {
                    "title": item.title,
                    "ids": ids,
                }
                if item.year:
                    obj["year"] = item.year

                iso_date = self._format_watched_at(item.watched_at)
                if iso_date:
                    obj["watched_at"] = iso_date

                if item.media_type == "show":
                    payload_shows.append(obj)
                else:
                    payload_movies.append(obj)

            payload: Dict[str, Any] = {}
            if payload_movies:
                payload["movies"] = payload_movies
            if payload_shows:
                payload["shows"] = payload_shows

            if not payload:
                continue

            response = self.session.post(url, json=payload, timeout=20)
            if response.status_code not in (200, 201):
                raise RuntimeError(f"同步到 Trakt 失败 (HTTP {response.status_code}): {response.text}")

            result = response.json()
            total_added_movies += result.get("added", {}).get("movies", 0)
            total_added_shows += result.get("added", {}).get("shows", 0)
            total_existing += result.get("existing", {}).get("movies", 0) + result.get("existing", {}).get("shows", 0)

        return {
            "added_movies": total_added_movies,
            "added_shows": total_added_shows,
            "added": total_added_movies + total_added_shows,
            "existing": total_existing,
        }

    def get_show_progress(self, id_or_imdb: str) -> Optional[Dict[str, Any]]:
        """Fetch watched progress for a show (including aired vs completed for each season)."""
        if not id_or_imdb:
            return None
        cache_key = str(id_or_imdb).strip().lower()
        if cache_key in self._progress_cache:
            return self._progress_cache[cache_key]

        url = f"{self.BASE_URL}/shows/{id_or_imdb}/progress/watched"
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                self._progress_cache[cache_key] = data
                return data
        except Exception:
            pass
        return None

    def get_show_completed_seasons(self, id_or_imdb: str) -> List[int]:
        """Return list of season numbers that are 100% fully completed (completed == aired > 0)."""
        prog = self.get_show_progress(id_or_imdb)
        if not prog:
            return []
        seasons = prog.get("seasons", [])
        return [
            s.get("number")
            for s in seasons
            if s.get("aired", 0) > 0 and s.get("completed", 0) == s.get("aired", 0)
        ]

    def has_any_completed_season(self, id_or_imdb: str) -> bool:
        """Check if at least one season of this show is 100% completed."""
        return len(self.get_show_completed_seasons(id_or_imdb)) > 0

    def is_season_completed(self, id_or_imdb: str, season_number: int = 1) -> bool:
        """Check if a specific season of this show is 100% completed."""
        return season_number in self.get_show_completed_seasons(id_or_imdb)

    @classmethod
    def refresh_access_token(
        cls,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        db: Optional[Any] = None,
    ) -> "TraktClient":
        """Refresh expired access token using refresh_token."""
        url = "https://api.trakt.tv/oauth/token"
        payload = {
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            "grant_type": "refresh_token",
        }
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            new_access = data.get("access_token")
            new_refresh = data.get("refresh_token")
            if db:
                db.save_platform_credential("trakt", {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "access_token": new_access,
                    "refresh_token": new_refresh,
                })
            return cls(client_id, new_access)
        else:
            raise RuntimeError(f"Trakt 刷新 Token 失败 (HTTP {resp.status_code}): {resp.text}")

    @classmethod
    def start_device_code_flow(cls, client_id: str) -> Dict[str, Any]:
        """Start Trakt Device Code login flow (trakt.tv/activate)."""
        url = "https://api.trakt.tv/oauth/device/code"
        resp = requests.post(url, json={"client_id": client_id}, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        raise RuntimeError(f"获取 Trakt 设备授权码失败 (HTTP {resp.status_code}): {resp.text}")

    @classmethod
    def poll_device_code_token(
        cls,
        client_id: str,
        client_secret: str,
        device_code: str,
        interval: int = 5,
        timeout: int = 300,
        db: Optional[Any] = None,
    ) -> "TraktClient":
        """Poll Trakt device code until authorized by user."""
        import time
        url = "https://api.trakt.tv/oauth/device/token"
        payload = {
            "code": device_code,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        start_time = time.time()
        while time.time() - start_time < timeout:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                new_access = data.get("access_token")
                new_refresh = data.get("refresh_token")
                if db:
                    db.save_platform_credential("trakt", {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "access_token": new_access,
                        "refresh_token": new_refresh,
                    })
                return cls(client_id, new_access)
            elif resp.status_code == 400:
                # Pending authorization
                time.sleep(interval)
            elif resp.status_code in (404, 409, 410):
                raise RuntimeError(f"Trakt 设备授权码已过期或无效 (HTTP {resp.status_code})")
            elif resp.status_code == 429:
                time.sleep(interval + 2)
            else:
                time.sleep(interval)
        raise TimeoutError("Trakt 设备授权等待超时，请重新运行登录任务。")


