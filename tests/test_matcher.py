# -*- coding: utf-8 -*-
"""Matcher 多级比对测试（零网络依赖）。

compute_diff 使用假 Trakt 客户端与临时 SQLite 库；
matcher 会读取项目根目录的真实 custom_mappings.json，
因此测试条目统一使用不可能与真实映射冲突的虚构 ID。
"""
import gc
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import LocalDatabase
from core.matcher import Matcher, normalize_title
from core.models import MovieRecord


class FakeTraktClient:
    def __init__(self, username="tester", completed_seasons=None):
        self.username = username
        self.completed = completed_seasons or set()

    def get_username(self):
        return self.username

    def get_show_completed_seasons(self, id_or_imdb):
        return [s for (ident, s) in self.completed if ident == id_or_imdb]

    def has_any_completed_season(self, id_or_imdb):
        return bool(self.get_show_completed_seasons(id_or_imdb))


def make_db():
    tmp = tempfile.TemporaryDirectory()
    db = LocalDatabase(db_path=os.path.join(tmp.name, "test_cache.db"))
    db._tmpdir = tmp
    return db


class NormalizeTitleTest(unittest.TestCase):
    def test_strips_season_suffix(self):
        self.assertEqual(normalize_title("权力的游戏 第三季"), "权力的游戏")
        self.assertEqual(normalize_title("Game of Thrones Season 3"), "game of thrones")

    def test_strips_punctuation_and_lowercases(self):
        self.assertEqual(normalize_title("The! Matrix."), "the matrix")
        self.assertEqual(normalize_title("Avengers: Endgame (2019)"), "avengers endgame 2019")

    def test_collapses_whitespace(self):
        self.assertEqual(normalize_title("  Inception    2010  "), "inception 2010")

    def test_empty(self):
        self.assertEqual(normalize_title(""), "")
        self.assertEqual(normalize_title(None), "")


class ComputeDiffTest(unittest.TestCase):
    def tearDown(self):
        db = getattr(self, "_db", None)
        if db is not None:
            gc.collect()
            try:
                db._tmpdir.cleanup()
            except PermissionError:
                # Windows 下 SQLite 句柄可能未及时释放，留在系统临时目录即可
                pass

    def _run(self, trakt_items, douban_items, trakt_client=None, db=None):
        self._db = db or make_db()
        return Matcher.compute_diff(
            trakt_items,
            douban_items,
            SimpleNamespace(user_id="1000001"),
            self._db,
            trakt_client=trakt_client or FakeTraktClient(),
        )

    def test_tier2_match_by_imdb(self):
        trakt = [MovieRecord(title="Inception", year=2010, imdb_id="tt1375666", media_type="movie", source="trakt")]
        douban = [MovieRecord(title="盗梦空间", year=2010, imdb_id="tt1375666", douban_id="3541415", source="douban")]
        diff = self._run(trakt, douban)
        self.assertEqual(len(diff.both), 1)
        self.assertEqual(diff.douban_only, [])
        self.assertEqual(diff.trakt_only, [])

    def test_tier3_match_by_chinese_alias(self):
        trakt = [MovieRecord(title="Inception", year=2010, imdb_id="tt1375666", media_type="movie", source="trakt")]
        douban = [MovieRecord(title="盗梦空间/Inception", year=2010, douban_id="3541415", source="douban")]
        diff = self._run(trakt, douban)
        self.assertEqual(len(diff.both), 1)

    def test_show_multi_season_alias_matches_mother_show(self):
        # 中文别名匹配依赖 imdb_to_douban 缓存中的中文标题，先种入缓存
        db = make_db()
        db.save_douban_subject("6874741", "权力的游戏 第一季", 2011, "tt0944947")
        trakt = [
            MovieRecord(title="Game of Thrones", year=2011, imdb_id="tt0944947", media_type="show", source="trakt")
        ]
        # 豆瓣第三季条目 ID 与缓存的第一季不同，模拟真实的多季独立条目
        douban = [MovieRecord(title="权力的游戏 第三季", year=2013, douban_id="6874743", source="douban")]
        diff = self._run(trakt, douban, db=db)
        self.assertEqual(len(diff.both), 1)

    def test_douban_only_with_imdb(self):
        trakt = []
        douban = [MovieRecord(title="无间道", year=2002, imdb_id="tt9999991", douban_id="1291841", source="douban")]
        diff = self._run(trakt, douban)
        self.assertEqual(len(diff.douban_only), 1)

    def test_douban_without_imdb_goes_to_missing(self):
        diff = self._run([], [MovieRecord(title="冷门短片", year=2001, douban_id="1300001", source="douban")])
        self.assertEqual(len(diff.douban_missing_imdb), 1)

    def test_partially_watched_show_is_ignored_and_persisted(self):
        db = make_db()
        trakt = [
            MovieRecord(title="部分看完的剧", year=2016, imdb_id="tt9999992", media_type="show", source="trakt")
        ]
        diff = self._run(trakt, [], trakt_client=FakeTraktClient(completed_seasons=set()), db=db)
        self.assertEqual(diff.trakt_only, [])
        self.assertEqual(len(diff.trakt_ignored), 1)
        self.assertTrue(db.is_item_ignored("tester", "tt9999992"))

    def test_completed_show_stays_syncable(self):
        trakt = [
            MovieRecord(title="看完整季的剧", year=2016, imdb_id="tt9999993", media_type="show", source="trakt")
        ]
        trakt_client = FakeTraktClient(completed_seasons={("tt9999993", 1)})
        diff = self._run(trakt, [], trakt_client=trakt_client)
        self.assertEqual(len(diff.trakt_only), 1)
        self.assertEqual(diff.trakt_ignored, [])


if __name__ == "__main__":
    unittest.main()
