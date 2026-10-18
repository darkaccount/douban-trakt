import re
import time
import random
from typing import List, Optional, Dict, Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    from .models import MovieRecord
    from .database import LocalDatabase
except ImportError:
    from models import MovieRecord
    from database import LocalDatabase


class DoubanClient:
    """Douban Web Client for reading user collections and marking movies as watched."""

    BASE_URL = "https://movie.douban.com"

    def __init__(self, user_id: str = "", cookie_str: str = ""):
        self.user_id = (user_id or "").strip()
        self.cookie_str = (cookie_str or "").strip()
        self.session = requests.Session()
        retries = Retry(total=2, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://movie.douban.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })

        self.cookies_dict = self._parse_cookie_str(self.cookie_str)
        self.session.cookies.update(self.cookies_dict)
        self.ck = self.cookies_dict.get("ck", "")

        # Auto-extract user_id from dbcl2 if omitted
        if not self.user_id and self.cookie_str:
            m = re.search(r'dbcl2=[\"\']?(\d+):', self.cookie_str)
            if m:
                self.user_id = m.group(1)

    @classmethod
    def login_by_password(cls, username: str, password: str, db: Optional[LocalDatabase] = None) -> "DoubanClient":
        """
        Attempt to login to Douban using username and password.
        Saves obtained cookie to database cache if successful.
        """
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
                "MicroMessenger/8.0.38(0x1800262c) NetType/WIFI Language/zh_CN"
            ),
            "Referer": "https://accounts.douban.com/passport/login_popup?login_source=anony",
        })

        login_url = "https://accounts.douban.com/j/mobile/login/basic"
        payload = {
            "name": username,
            "password": password,
            "remember": "true",
            "ticket": "",
        }

        resp = session.post(login_url, data=payload, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                cookies_list = [f"{k}={v}" for k, v in session.cookies.items()]
                cookie_str = "; ".join(cookies_list)
                user_id = str(data.get("payload", {}).get("account_info", {}).get("id", ""))
                if not user_id:
                    m = re.search(r'dbcl2=[\"\']?(\d+):', cookie_str)
                    if m:
                        user_id = m.group(1)

                if db:
                    db.save_platform_credential("douban", {
                        "cookie": cookie_str,
                        "user_id": user_id,
                        "username": username,
                    })

                return cls(user_id=user_id, cookie_str=cookie_str)
            else:
                desc = data.get("description", "") or data.get("message", "")
                raise PermissionError(f"豆瓣账号密码登录未通过: {desc}（触发风控或需要验证码，请配置 DOUBAN_COOKIE / DOUBAN_AUTH）")
        else:
            raise RuntimeError(f"豆瓣登录请求失败 (HTTP {resp.status_code}): {resp.text}")

    @staticmethod
    def _parse_cookie_str(cookie_str: str) -> Dict[str, str]:
        cookies = {}
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                cookies[k.strip()] = v.strip().strip('"')
        return cookies

    def test_connection(self) -> Dict[str, Any]:
        """Test Douban connection and verify user profile access."""
        url = f"{self.BASE_URL}/people/{self.user_id}/collect"
        response = self.session.get(url, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            # Check user nickname or title
            title_tag = soup.find("title")
            title_text = title_tag.get_text(strip=True) if title_tag else ""
            if "登录" in title_text and not self.cookies_dict.get("dbcl2"):
                # If redirected to login
                return {
                    "status": "warning",
                    "user_id": self.user_id,
                    "message": "可以访问公开页面，但未检测到登录 Cookie (dbcl2)。只能读取公开列表，无法写入标记。",
                }
            return {
                "status": "ok",
                "user_id": self.user_id,
                "page_title": title_text,
                "has_ck": bool(self.ck),
            }
        elif response.status_code == 403:
            raise PermissionError("访问豆瓣主页返回 403 Forbidden，可能触发了豆瓣反爬或 Cookie 已失效。")
        else:
            raise RuntimeError(f"豆瓣连接测试失败 (HTTP {response.status_code})")

    def get_watched_movies(
        self,
        db: LocalDatabase,
        page_delay: float = 2.0,
        max_consecutive_hits: int = 15,
        force_full: bool = False,
    ) -> List[MovieRecord]:
        """
        Fetch all movies marked as 'collect' (看过) by the user.
        Supports time-sorted incremental caching: stops crawling when hitting
        consecutive existing cached records, greatly reducing web scraping requests.
        """
        # Load locally cached movies for this user
        cached_list = db.get_user_douban_movies(self.user_id)
        cached_dict = {r["douban_id"]: r for r in cached_list}
        if cached_dict:
            print(f"本地缓存已存在 {len(cached_dict)} 条豆瓣已看记录。启用增量抓取模式...")

        newly_crawled_movies: List[MovieRecord] = []
        consecutive_hits = 0
        start = 0
        page_size = 15

        print(f"正在从豆瓣抓取用户 [{self.user_id}] 的最新看片记录（按时间倒序）...")

        while True:
            url = f"{self.BASE_URL}/people/{self.user_id}/collect?start={start}&sort=time&rating=all&filter=all&mode=grid"
            response = self.session.get(url, timeout=15)
            if response.status_code != 200:
                print(f"抓取豆瓣第 {start // page_size + 1} 页出错 (HTTP {response.status_code})")
                break

            soup = BeautifulSoup(response.text, "html.parser")
            items = soup.select(".grid-view .item")
            if not items:
                break

            page_new_count = 0
            stop_crawling = False

            for item in items:
                title_elem = item.select_one(".info .title a")
                if not title_elem:
                    continue

                href = title_elem.get("href", "")
                subject_match = re.search(r"/subject/(\d+)", href)
                if not subject_match:
                    continue
                douban_id = subject_match.group(1)

                # Incremental check: check if already in local cache
                if not force_full and douban_id in cached_dict:
                    consecutive_hits += 1
                    if consecutive_hits >= max_consecutive_hits:
                        print(f"已连续匹配 {consecutive_hits} 部本地缓存记录，判定更早历史已完整同步，提前停止网页抓取。")
                        stop_crawling = True
                        break
                    continue
                else:
                    consecutive_hits = 0

                # Clean title
                raw_title = title_elem.get_text(separator=" ", strip=True)
                title = re.sub(r"\s+", " ", raw_title).strip()

                # Watch date
                date_elem = item.select_one(".info .date")
                watched_date = None
                if date_elem:
                    date_text = date_elem.get_text(strip=True)
                    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", date_text)
                    if date_match:
                        watched_date = date_match.group(1)

                # Rating
                rating = None
                rating_elem = item.select_one('[class*="rating"]')
                if rating_elem:
                    classes = rating_elem.get("class", [])
                    for cls in classes:
                        rate_match = re.search(r"rating(\d)-t", cls)
                        if rate_match:
                            rating = int(rate_match.group(1))
                            break

                # Parse year if available in intro
                year = None
                intro_elem = item.select_one(".info .intro")
                if intro_elem:
                    intro_text = intro_elem.get_text(strip=True)
                    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", intro_text)
                    if year_match:
                        year = int(year_match.group(1))

                rec = MovieRecord(
                    title=title,
                    year=year,
                    douban_id=douban_id,
                    watched_at=watched_date,
                    rating=rating,
                    source="douban",
                )
                newly_crawled_movies.append(rec)
                page_new_count += 1

                # Save new movie to database immediately
                db.save_user_douban_movie(
                    user_id=self.user_id,
                    douban_id=douban_id,
                    title=title,
                    year=year,
                    imdb_id=None,
                    watched_at=watched_date,
                    rating=rating,
                )

            if stop_crawling:
                break

            # Check next page link
            next_link = soup.select_one("span.next a")
            if not next_link:
                break

            start += page_size
            time.sleep(page_delay + random.uniform(0.3, 0.8))

        # Re-fetch the full merged list from database
        all_cached = db.get_user_douban_movies(self.user_id)
        merged_movies: List[MovieRecord] = []
        for r in all_cached:
            merged_movies.append(
                MovieRecord(
                    title=r["title"],
                    year=r["year"],
                    douban_id=r["douban_id"],
                    imdb_id=r["imdb_id"],
                    watched_at=r["watched_at"],
                    rating=r["rating"],
                    source="douban",
                )
            )

        print(f"豆瓣记录整理完毕：本次新抓取 {len(newly_crawled_movies)} 部，本地累计有效记录 {len(merged_movies)} 部。")
        return merged_movies

    def resolve_imdb_id(self, movie: MovieRecord, db: LocalDatabase) -> Optional[str]:
        """
        Resolve IMDb ID for a Douban movie.
        First checks SQLite cache, then queries Douban subject page if necessary.
        """
        if not movie.douban_id:
            return None

        # Check local cache first
        cached = db.get_subject_by_douban_id(movie.douban_id)
        if cached and cached.get("imdb_id"):
            movie.imdb_id = cached["imdb_id"]
            return movie.imdb_id

        # Fetch from Douban subject page
        url = f"{self.BASE_URL}/subject/{movie.douban_id}/"
        try:
            response = self.session.get(url, timeout=12)
            if response.status_code == 200:
                imdb_match = re.search(r"IMDb:?</span>\s*(tt\d+)", response.text, re.IGNORECASE)
                if imdb_match:
                    movie.imdb_id = imdb_match.group(1).strip()

                # Extract exact title / year if missing
                if not movie.year:
                    year_match = re.search(r'<span class="year">\((\d{4})\)</span>', response.text)
                    if year_match:
                        movie.year = int(year_match.group(1))

                db.save_douban_subject(movie.douban_id, movie.title, movie.year, movie.imdb_id)
                if movie.imdb_id:
                    db.update_user_douban_movie_imdb(self.user_id, movie.douban_id, movie.imdb_id)
                time.sleep(1.2 + random.uniform(0.2, 0.6))
                return movie.imdb_id
            elif response.status_code == 403:
                print(f"访问豆瓣条目 {movie.douban_id} 遭遇 403，暂停中...")
                time.sleep(5)
        except Exception as e:
            print(f"解析豆瓣条目 {movie.douban_id} 异常: {e}")

        return None

    def search_douban_by_imdb(
        self,
        imdb_id: Optional[str],
        title: str,
        year: Optional[int],
        db: LocalDatabase,
        trakt_client: Optional[Any] = None,
        media_type: str = "movie",
    ) -> Optional[str]:
        """
        Search for a Douban subject ID using IMDb ID, Trakt Chinese translations, or title+year.
        Checks SQLite cache first.
        """
        if imdb_id:
            cached_id = db.get_douban_id_by_imdb(imdb_id)
            if cached_id:
                return cached_id

        # 1. Gather search candidate titles (Trakt Chinese translations first)
        candidates: List[str] = []
        if trakt_client and imdb_id:
            try:
                zh_titles = trakt_client.get_translations_zh(imdb_id, media_type)
                for zt in zh_titles:
                    if zt and zt not in candidates:
                        candidates.append(zt)
            except Exception:
                pass

        # 2. Add original title and stripped title
        if title and title not in candidates:
            candidates.append(title)
        clean_title = re.sub(r"[^\w\s\u4e00-\u9fa5]", " ", title).strip()
        if clean_title and clean_title not in candidates:
            candidates.append(clean_title)

        # 3. Query Douban subject_suggest with rate-limit protection
        for cand in candidates:
            if not cand:
                continue
            try:
                resp = self.session.get(
                    f"{self.BASE_URL}/j/subject_suggest",
                    params={"q": cand},
                    timeout=10,
                )
                if resp.status_code == 200:
                    results = resp.json()
                    # Soft rate limit check: if empty for common query, canary check
                    if not results and len(cand) >= 2:
                        pass

                    for item in results:
                        res_id = str(item.get("id"))
                        res_year = item.get("year")
                        res_title = item.get("title", title)

                        # Match year if available
                        if year and res_year:
                            try:
                                if abs(int(res_year) - int(year)) <= 1:
                                    db.save_douban_subject(res_id, res_title, year, imdb_id)
                                    return res_id
                            except ValueError:
                                pass
                        elif not year:
                            db.save_douban_subject(res_id, res_title, year, imdb_id)
                            return res_id

                time.sleep(1.0)
            except Exception:
                pass

        return None

    def mark_as_watched(self, douban_id: str, watched_date: Optional[str] = None, rating: Optional[int] = None) -> bool:
        """
        Mark a movie as watched on Douban with the specified watch date.
        """
        if not self.ck:
            raise ValueError("缺少豆瓣 CSRF 校验码 'ck'，请确保 DOUBAN_COOKIE 中包含 ck=xxxx。")

        url = f"{self.BASE_URL}/j/subject/{douban_id}/interest"
        payload = {
            "ck": self.ck,
            "interest": "collect",
            "fold": "viewer",
            "share-date": watched_date if watched_date else "",
        }
        if rating:
            payload["rating"] = str(rating)

        headers = {
            "Referer": f"{self.BASE_URL}/subject/{douban_id}/",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self.BASE_URL,
        }

        response = self.session.post(url, data=payload, headers=headers, timeout=15)
        if response.status_code == 200:
            return True
        elif response.status_code == 403:
            raise PermissionError(f"标记豆瓣条目 {douban_id} 失败：403 Forbidden，请暂停同步并检查 Cookie。")
        else:
            print(f"标记豆瓣条目 {douban_id} 失败 (HTTP {response.status_code}): {response.text}")
            return False

    def unmark_as_watched(self, douban_id: str) -> bool:
        """
        Remove/unmark a movie/show from user's Douban 'collect' (看过) list.
        """
        if not self.ck:
            raise ValueError("缺少豆瓣 CSRF 校验码 'ck'，请确保 DOUBAN_COOKIE 中包含 ck=xxxx。")

        url = f"{self.BASE_URL}/subject/{douban_id}/remove"
        payload = {
            "ck": self.ck,
        }
        headers = {
            "Referer": f"{self.BASE_URL}/subject/{douban_id}/",
            "Origin": self.BASE_URL,
        }

        response = self.session.post(url, data=payload, headers=headers, timeout=15, allow_redirects=False)
        if response.status_code in (200, 302):
            return True
        elif response.status_code == 403:
            raise PermissionError(f"取消标记豆瓣条目 {douban_id} 失败：403 Forbidden，请暂停同步并检查 Cookie。")
        else:
            print(f"取消标记豆瓣条目 {douban_id} 失败 (HTTP {response.status_code}): {response.text}")
            return False

