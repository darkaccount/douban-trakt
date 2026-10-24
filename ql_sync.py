# -*- coding: utf-8 -*-
# new Env('豆瓣-Trakt 2.日常定时增量同步')
# cron: 0 4 * * *
"""
cron: 0 4 * * *
new Env('豆瓣-Trakt 2.日常定时增量同步')
"""
import sys
import os
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.deps import ensure_dependencies

if not ensure_dependencies():
    sys.exit(1)

from core.config import Config
from core.database import LocalDatabase
from core.trakt_client import TraktClient
from core.douban_client import DoubanClient
from core.matcher import Matcher
from core.syncer import Syncer
from core.notify import send_notification


def main():
    print("=" * 60)
    print("【青龙任务 2/2】豆瓣与Trakt影视双向同步 - 定时日常同步任务")
    print("=" * 60)

    db = LocalDatabase()
    Config.load_cached_credentials(db)

    # 1. Initialize & Verify Clients
    douban = None
    trakt = None

    # Douban Init
    if Config.DOUBAN_COOKIE:
        try:
            douban = DoubanClient(Config.DOUBAN_USER_ID, Config.DOUBAN_COOKIE)
            status = douban.test_connection()
            if status.get("status") != "ok":
                douban = None
        except Exception as e:
            print(f"⚠ 豆瓣 Cookie 连通失败: {e}")
            douban = None

    if not douban and Config.DOUBAN_USERNAME and Config.DOUBAN_PASSWORD:
        print("正在尝试使用账号密码自动登录豆瓣...")
        try:
            douban = DoubanClient.login_by_password(Config.DOUBAN_USERNAME, Config.DOUBAN_PASSWORD, db=db)
        except Exception as e:
            print(f"✘ 豆瓣密码登录失败: {e}")

    if not douban:
        err_msg = "豆瓣凭证已失效且未能自动登录，请在青龙环境变量中更新 DOUBAN_AUTH 或 DOUBAN_COOKIE。"
        print(f"✘ {err_msg}")
        send_notification("豆瓣-Trakt 同步失败：豆瓣凭证失效", err_msg)
        sys.exit(1)

    # Trakt Init
    if Config.TRAKT_CLIENT_ID and Config.TRAKT_ACCESS_TOKEN:
        try:
            trakt = TraktClient(Config.TRAKT_CLIENT_ID, Config.TRAKT_ACCESS_TOKEN)
            trakt.test_connection()
        except Exception as e:
            print(f"⚠ Trakt Access Token 验证未通过: {e}")
            trakt = None

    if not trakt and Config.TRAKT_REFRESH_TOKEN and Config.TRAKT_CLIENT_ID and Config.TRAKT_CLIENT_SECRET:
        print("正在使用 Refresh Token 自动续期 Trakt Access Token...")
        try:
            trakt = TraktClient.refresh_access_token(
                Config.TRAKT_CLIENT_ID,
                Config.TRAKT_CLIENT_SECRET,
                Config.TRAKT_REFRESH_TOKEN,
                db=db,
            )
        except Exception as e:
            print(f"✘ Trakt 续期失败: {e}")

    if not trakt:
        err_msg = "Trakt Access Token 已失效，请在青龙环境变量中更新 TRAKT_AUTH 或 TRAKT_ACCESS_TOKEN。"
        print(f"✘ {err_msg}")
        send_notification("豆瓣-Trakt 同步失败：Trakt凭证失效", err_msg)
        sys.exit(1)

    # 2. Fetch and Diff
    print("\n[执行比对] 正在比对两端影视看片记录（智能增量模式）...")
    trakt_items = trakt.get_all_watched(db=db, force_refresh=False)
    douban_items = douban.get_watched_movies(db, force_full=False)
    diff = Matcher.compute_diff(trakt_items, douban_items, douban, db, trakt_client=trakt)

    print(f"\n比对结果:")
    print(f"- 两端共有（已对齐）: {len(diff.both)} 部")
    print(f"- 豆瓣独有（待同步至 Trakt）: {len(diff.douban_only)} 部")
    print(f"- Trakt独有（待同步至 豆瓣）: {len(diff.trakt_only)} 部")
    print(f"- 已忽略/非整季/短片: {len(diff.trakt_ignored)} 部")

    synced_to_trakt_count = 0
    synced_to_douban_count = 0

    # 3. Sync Douban -> Trakt
    if diff.douban_only:
        print(f"\n[同步] 正在将 {len(diff.douban_only)} 部豆瓣独有影视同步至 Trakt...")
        res_t = Syncer.sync_to_trakt(diff.douban_only, trakt, db)
        synced_to_trakt_count = res_t.get("added", 0)

    # 4. Sync Trakt -> Douban
    if diff.trakt_only:
        print(f"\n[同步] 正在将 {len(diff.trakt_only)} 部 Trakt 独有影视同步至豆瓣...")
        res_d = Syncer.sync_to_douban(
            diff.trakt_only,
            douban,
            db,
            delay_min=Config.SYNC_DELAY_MIN,
            delay_max=Config.SYNC_DELAY_MAX,
            trakt_client=trakt,
        )
        synced_to_douban_count = res_d.get("success", 0)

    # 5. Send Notification
    total_new = synced_to_trakt_count + synced_to_douban_count
    summary_lines = [
        f"📅 同步时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"🎬 共有已对齐: {len(diff.both) + total_new} 部",
    ]

    if synced_to_trakt_count > 0:
        summary_lines.append(f"\n⬆ 豆瓣 -> Trakt 新增 ({synced_to_trakt_count} 部):")
        for m in diff.douban_only[:5]:
            summary_lines.append(f"  • {m.title} ({m.year or '未知年份'})")
        if len(diff.douban_only) > 5:
            summary_lines.append(f"  • ... 等共 {len(diff.douban_only)} 部")

    if synced_to_douban_count > 0:
        summary_lines.append(f"\n⬇ Trakt -> 豆瓣 新增 ({synced_to_douban_count} 部):")
        for m in diff.trakt_only[:5]:
            summary_lines.append(f"  • {m.title} ({m.year or '未知年份'})")
        if len(diff.trakt_only) > 5:
            summary_lines.append(f"  • ... 等共 {len(diff.trakt_only)} 部")

    summary_text = "\n".join(summary_lines)

    if total_new > 0:
        print(f"\n✔ 本次定时任务发现并成功同步 {total_new} 部影视！")
        send_notification("🎬 豆瓣-Trakt 影视同步成功简报", summary_text)
    else:
        print("\n✔ 两端数据完全一致，本次无需任何新增写入。")
        notify_always = os.getenv("NOTIFY_ON_NO_CHANGE", "false").lower() in ("true", "1")
        if notify_always:
            send_notification("豆瓣-Trakt 同步日常巡检", "两端数据完全一致，无新增看片记录。\n" + summary_text)

    print("=" * 60)


if __name__ == "__main__":
    main()
