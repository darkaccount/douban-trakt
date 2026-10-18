import time
import random
import re
from typing import List, Dict, Any, Optional
from tqdm import tqdm

try:
    from .models import MovieRecord
    from .trakt_client import TraktClient
    from .douban_client import DoubanClient
    from .database import LocalDatabase
except ImportError:
    from models import MovieRecord
    from trakt_client import TraktClient
    from douban_client import DoubanClient
    from database import LocalDatabase


class Syncer:
    """Handles synchronization execution between Trakt and Douban."""

    @staticmethod
    def sync_to_trakt(
        movies: List[MovieRecord],
        trakt_client: TraktClient,
        db: LocalDatabase,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Sync Douban-only movies into Trakt history.
        Includes original watch date (watched_at) and preserves existing watched items.
        """
        valid_movies = [m for m in movies if m.imdb_id]
        if not valid_movies:
            print("没有找到带有有效 IMDb ID 的待同步电影。")
            return {"added": 0, "existing": 0, "not_found": []}

        if dry_run:
            print(f"[Dry Run 预览] 将有 {len(valid_movies)} 部电影同步到 Trakt（不执行实际写入）。")
            return {"added": len(valid_movies), "existing": 0, "not_found": []}

        print(f"正在将 {len(valid_movies)} 部电影提交到 Trakt...")
        result = trakt_client.add_watched_items(valid_movies)

        # Log to local DB
        for m in valid_movies:
            db.log_sync(
                source="douban",
                target="trakt",
                identifier=m.imdb_id or m.title,
                title=m.title,
                watched_at=m.watched_at,
                status="success",
            )

        # Update local Trakt cache
        username = trakt_client.get_username()
        db.save_user_trakt_items(username, valid_movies)
        acts = trakt_client.get_last_activities()
        if acts:
            db.save_trakt_sync_meta(
                username,
                acts.get("movies", {}).get("watched_at"),
                acts.get("episodes", {}).get("watched_at"),
            )

        print(f"Trakt 同步完成: 新增 {result['added']} 部，已有已忽略 {result['existing']} 部。")
        return result

    @staticmethod
    def sync_to_douban(
        movies: List[MovieRecord],
        douban_client: DoubanClient,
        db: LocalDatabase,
        delay_min: float = 3.0,
        delay_max: float = 6.0,
        dry_run: bool = False,
        trakt_client: Optional[TraktClient] = None,
        account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Sync Trakt-only movies into Douban 'collect' list.
        Includes original watch date and respects anti-ban rate limiting.
        """
        if dry_run:
            print(f"[Dry Run 预览] 将有 {len(movies)} 部电影同步到豆瓣（不执行实际写入）。")
            return {"success": len(movies), "failed": 0, "skipped": 0}

        if not account_id:
            if trakt_client and hasattr(trakt_client, "get_username") and trakt_client.get_username():
                account_id = trakt_client.get_username()
            elif douban_client and hasattr(douban_client, "user_id") and douban_client.user_id:
                account_id = str(douban_client.user_id)
            else:
                account_id = "default"

        success_count = 0
        failed_count = 0
        skipped_count = 0

        print(f"开始同步 {len(movies)} 部电影到豆瓣（每次标记间隔 {delay_min}~{delay_max} 秒以保护账号安全）...")

        for m in tqdm(movies, desc="同步至豆瓣", unit="部"):
            try:
                # 0. Check for TV show season completion: Do not sync partially watched shows
                if m.media_type == "show" and trakt_client:
                    ident = m.imdb_id or str(m.trakt_id or m.title)
                    comp_seasons = trakt_client.get_show_completed_seasons(ident)
                    if not comp_seasons:
                        print(f"\n[跳过未整季标记] 剧集未完整看完整季: {m.title}")
                        skipped_count += 1
                        db.log_sync("trakt", "douban", ident, m.title, m.watched_at, "skipped_partial_season")
                        db.ignore_item(
                            account_id=account_id,
                            identifier=ident,
                            title=m.title,
                            year=m.year,
                            reason="trakt_partial_watched",
                        )
                        continue

                # 1. Resolve Douban Subject ID
                douban_id = m.douban_id
                if not douban_id and m.imdb_id:
                    douban_id = douban_client.search_douban_by_imdb(
                        m.imdb_id,
                        m.title,
                        m.year,
                        db,
                        trakt_client=trakt_client,
                        media_type=m.media_type,
                    )

                if not douban_id:
                    print(f"\n[跳过] 未能找到对应的豆瓣条目: {m.title} ({m.year or '未知年份'}, IMDb: {m.imdb_id or '无'})")
                    skipped_count += 1
                    identifier = m.imdb_id or str(m.trakt_id or m.title)
                    db.log_sync("trakt", "douban", identifier, m.title, m.watched_at, "skipped_not_found")
                    db.ignore_item(
                        account_id=account_id,
                        identifier=identifier,
                        title=m.title,
                        year=m.year,
                        reason="douban_not_found",
                    )
                    continue

                # If Douban subject specifies a season (e.g. 第一季), verify that specific season is completed
                if m.media_type == "show" and trakt_client:
                    ident = m.imdb_id or str(m.trakt_id or m.title)
                    cached_sub = db.get_subject_by_douban_id(douban_id)
                    d_title = cached_sub.get("title", "") if cached_sub else ""
                    m_cn = re.search(r"第([一二三四五六七八九十\d]+)季", d_title)
                    cn_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
                    if m_cn:
                        s_str = m_cn.group(1)
                        s_num = int(s_str) if s_str.isdigit() else cn_map.get(s_str, 1)
                        if not trakt_client.is_season_completed(ident, s_num):
                            print(f"\n[跳过未整季标记] 豆瓣条目为第{s_num}季，但Trakt未完整看完该季: {m.title} ({d_title})")
                            skipped_count += 1
                            db.log_sync("trakt", "douban", douban_id, m.title, m.watched_at, f"skipped_season_{s_num}_partial")
                            continue

                # 2. Extract watch date (YYYY-MM-DD)
                watch_date = None
                if m.watched_at:
                    watch_date = m.watched_at[:10]  # Take YYYY-MM-DD

                # 3. Mark as watched on Douban
                ok = douban_client.mark_as_watched(douban_id, watched_date=watch_date)
                if ok:
                    success_count += 1
                    db.log_sync("trakt", "douban", douban_id, m.title, watch_date, "success")
                    db.save_user_douban_movie(
                        user_id=douban_client.user_id,
                        douban_id=douban_id,
                        title=m.title,
                        year=m.year,
                        imdb_id=m.imdb_id,
                        watched_at=watch_date,
                        rating=None,
                    )
                else:
                    failed_count += 1
                    db.log_sync("trakt", "douban", douban_id, m.title, watch_date, "failed")

                # 4. Anti-ban pacing
                sleep_time = random.uniform(delay_min, delay_max)
                time.sleep(sleep_time)

            except KeyboardInterrupt:
                print("\n用户手动中止同步，当前进度已保存至本地数据库，下次运行可继续。")
                break
            except PermissionError as pe:
                print(f"\n[安全中断] {pe}")
                break
            except Exception as e:
                print(f"\n[错误] 同步 {m.title} 失败: {e}")
                failed_count += 1

        print(f"豆瓣同步完成: 成功 {success_count} 部，失败 {failed_count} 部，未找到匹配 {skipped_count} 部。")
        return {"success": success_count, "failed": failed_count, "skipped": skipped_count}
