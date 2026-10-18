import os
import json
import re
from dataclasses import dataclass, field
from typing import List, Set, Dict, Optional, Tuple, Any
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, *args, **kwargs):
        return iterable

try:
    from .models import MovieRecord
    from .douban_client import DoubanClient
    from .database import LocalDatabase
except ImportError:
    from models import MovieRecord
    from douban_client import DoubanClient
    from database import LocalDatabase


@dataclass
class DiffResult:
    douban_only: List[MovieRecord] = field(default_factory=list)
    trakt_only: List[MovieRecord] = field(default_factory=list)
    trakt_ignored: List[MovieRecord] = field(default_factory=list)
    both: List[MovieRecord] = field(default_factory=list)
    douban_missing_imdb: List[MovieRecord] = field(default_factory=list)


def normalize_title(s: str) -> str:
    """Normalize a title by stripping season indicators, special editions, punctuation, and extra whitespace."""
    if not s:
        return ""
    # Strip Season / 第X季 suffixes
    s = re.sub(r"\s*(?:Season\s*\d+|第\s*[\d一二三四五六七八九十百]+\s*季).*", "", s, flags=re.IGNORECASE)
    # Strip special edition suffixes (SP, 特别篇, 续篇, 番外篇)
    s = re.sub(r"\s*(?:SP|特别篇|番外篇|新春特别篇|春季特別篇|秋之特别篇|夏之特别篇|続編|续篇).*", "", s, flags=re.IGNORECASE)
    # Strip trailing digits preceded by space
    s = re.sub(r"\s+\d+$", "", s)
    # Remove punctuation and lowercase
    s = re.sub(r"[^\w\s\u4e00-\u9fa5]", "", s).strip().lower()
    return re.sub(r"\s+", " ", s)


class Matcher:
    """Fast Multi-Tier Matcher for comparing watched records between Trakt and Douban."""

    @staticmethod
    def compute_diff(
        trakt_items: List[MovieRecord],
        douban_items: List[MovieRecord],
        douban_client: Optional[DoubanClient],
        db: LocalDatabase,
        resolve_remaining_imdb: bool = False,
        trakt_client: Optional[Any] = None,
        account_id: Optional[str] = None,
    ) -> DiffResult:
        result = DiffResult()

        # Step 1: Pre-load local mappings and optional custom overrides
        imdb_mappings = db.get_all_imdb_to_douban()
        custom_mappings: Dict[str, str] = {}
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        mapping_file = os.path.join(base_dir, "custom_mappings.json")
        if not os.path.exists(mapping_file):
            mapping_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom_mappings.json")
        if os.path.exists(mapping_file):
            try:
                with open(mapping_file, "r", encoding="utf-8") as f:
                    custom_mappings = json.load(f)
            except Exception:
                pass

        # Step 2: Index Trakt items by IMDb ID, Douban ID, and normalized Titles (English + Chinese)
        trakt_by_imdb: Dict[str, MovieRecord] = {}
        trakt_by_douban_id: Dict[str, List[MovieRecord]] = {}
        trakt_by_norm_title: Dict[str, List[MovieRecord]] = {}

        for tm in trakt_items:
            if tm.imdb_id:
                trakt_by_imdb[tm.imdb_id.lower()] = tm

                # Check custom mapping
                custom_target = custom_mappings.get(tm.imdb_id)
                if custom_target and custom_target != "ignore":
                    trakt_by_douban_id.setdefault(str(custom_target), []).append(tm)

                # Check cached Douban ID and Chinese title
                cached_sub = imdb_mappings.get(tm.imdb_id.lower())
                if cached_sub:
                    if cached_sub.get("douban_id"):
                        trakt_by_douban_id.setdefault(str(cached_sub["douban_id"]), []).append(tm)
                    if cached_sub.get("title"):
                        norm_zh = normalize_title(cached_sub["title"])
                        if norm_zh:
                            trakt_by_norm_title.setdefault(norm_zh, []).append(tm)

            if tm.trakt_id:
                custom_target = custom_mappings.get(str(tm.trakt_id))
                if custom_target and custom_target != "ignore":
                    trakt_by_douban_id.setdefault(str(custom_target), []).append(tm)

            if tm.title:
                custom_target = custom_mappings.get(tm.title)
                if custom_target and custom_target != "ignore":
                    trakt_by_douban_id.setdefault(str(custom_target), []).append(tm)

            if tm.douban_id:
                trakt_by_douban_id.setdefault(str(tm.douban_id), []).append(tm)

            norm_t = normalize_title(tm.title)
            if norm_t:
                trakt_by_norm_title.setdefault(norm_t, []).append(tm)

        # Step 3: Match Douban items against Trakt (Tier 1: Douban ID -> Tier 2: IMDb ID -> Tier 3: Title & Year)
        trakt_matched_set: Set[MovieRecord] = set()
        unmatched_douban: List[MovieRecord] = []

        print("正在执行本地多级智能比对（优先匹配已知 IMDb ID、豆瓣 ID 与片名别名，无需网络请求）...")

        for dm in douban_items:
            matched_tms: List[MovieRecord] = []

            # Tier 1: Match by Douban ID if resolved (supports N-to-1 mapping)
            if dm.douban_id and str(dm.douban_id) in trakt_by_douban_id:
                matched_tms.extend(trakt_by_douban_id[str(dm.douban_id)])

            # Tier 2: Match by IMDb ID if known
            if not matched_tms and dm.imdb_id and dm.imdb_id.lower() in trakt_by_imdb:
                matched_tms.append(trakt_by_imdb[dm.imdb_id.lower()])

            # Tier 3: Match by Title & Year (split Douban title by '/')
            if not matched_tms and dm.title:
                aliases = [normalize_title(part) for part in dm.title.split("/")]
                for alias in aliases:
                    if alias and alias in trakt_by_norm_title:
                        candidates = trakt_by_norm_title[alias]
                        for cand in candidates:
                            # For TV shows: a multi-season show (cand.year) can match later seasons (dm.year)
                            if not dm.year or not cand.year:
                                matched_tms.append(cand)
                                break
                            elif cand.media_type == "show" and cand.year <= (dm.year or 9999):
                                matched_tms.append(cand)
                                break
                            elif abs(dm.year - cand.year) <= 1:
                                matched_tms.append(cand)
                                break
                        if matched_tms:
                            break

            if matched_tms:
                result.both.append(dm)
                for tm in matched_tms:
                    trakt_matched_set.add(tm)
                # Backfill IMDb ID if missing
                primary_tm = matched_tms[0]
                if not dm.imdb_id and primary_tm.imdb_id and douban_client:
                    dm.imdb_id = primary_tm.imdb_id
                    db.update_user_douban_movie_imdb(douban_client.user_id, dm.douban_id, dm.imdb_id)
            else:
                unmatched_douban.append(dm)

        print(f"本地极速比对完成：共有条目已匹配 {len(result.both)} 部，剩余待核实条目 {len(unmatched_douban)} 部。")

        # Step 4: Handle remaining unmatched Douban items
        for dm in unmatched_douban:
            if not dm.imdb_id:
                cached_subject = db.get_subject_by_douban_id(dm.douban_id)
                if cached_subject and cached_subject.get("imdb_id"):
                    dm.imdb_id = cached_subject["imdb_id"]

        if resolve_remaining_imdb and douban_client:
            to_resolve = [dm for dm in unmatched_douban if not dm.imdb_id]
            if to_resolve:
                print(f"正在对剩余 {len(to_resolve)} 部疑似独有条目解析 IMDb 编号...")
                for dm in tqdm(to_resolve, desc="解析 IMDb ID", unit="部"):
                    douban_client.resolve_imdb_id(dm, db)

        for dm in unmatched_douban:
            # Check again if newly resolved IMDb matches Trakt
            if dm.imdb_id and dm.imdb_id.lower() in trakt_by_imdb:
                matched_tm = trakt_by_imdb[dm.imdb_id.lower()]
                result.both.append(dm)
                trakt_matched_set.add(matched_tm)
            elif dm.imdb_id:
                result.douban_only.append(dm)
            else:
                result.douban_missing_imdb.append(dm)

        # Step 5: Classify Trakt-only items and filter Ignored / Censored items
        if not account_id:
            if trakt_client and hasattr(trakt_client, "get_username") and trakt_client.get_username():
                account_id = trakt_client.get_username()
            elif douban_client and hasattr(douban_client, "user_id") and douban_client.user_id:
                account_id = str(douban_client.user_id)
            else:
                account_id = "default"
        for tm in trakt_items:
            if tm not in trakt_matched_set:
                identifier = tm.imdb_id or str(tm.trakt_id or tm.title)
                # Check if marked as ignored
                is_ignored = (
                    custom_mappings.get(identifier) == "ignore"
                    or (tm.imdb_id and custom_mappings.get(tm.imdb_id) == "ignore")
                    or (tm.trakt_id and custom_mappings.get(str(tm.trakt_id)) == "ignore")
                    or custom_mappings.get(tm.title) == "ignore"
                    or db.is_item_ignored(account_id, identifier)
                    or (tm.imdb_id and db.is_item_ignored(account_id, tm.imdb_id))
                    or (tm.trakt_id and db.is_item_ignored(account_id, str(tm.trakt_id)))
                    or db.is_item_ignored(account_id, tm.title)
                )

                # Check if it's a TV show without any completed season (user constraint: 非整季标记看过的不要同步到豆瓣)
                if not is_ignored and tm.media_type == "show" and trakt_client:
                    t_ident = tm.imdb_id or str(tm.trakt_id or tm.title)
                    if not trakt_client.has_any_completed_season(t_ident):
                        is_ignored = True
                        db.ignore_item(
                            account_id=account_id,
                            identifier=t_ident,
                            title=tm.title,
                            year=tm.year,
                            reason="trakt_partial_watched",
                        )

                if is_ignored:
                    result.trakt_ignored.append(tm)
                else:
                    result.trakt_only.append(tm)

        return result

