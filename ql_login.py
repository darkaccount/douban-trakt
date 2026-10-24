# -*- coding: utf-8 -*-
# new Env('豆瓣-Trakt 1.账号登录与凭证验证')
# cron: 0 0 1 1 *
"""
cron: 0 0 1 1 *
new Env('豆瓣-Trakt 1.账号登录与凭证验证')
"""
import sys
import os

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
from core.notify import send_notification


def main():
    print("=" * 60)
    print("【青龙任务 1/2】豆瓣与Trakt同步脚本 - 账号登录与凭证健康检查")
    print("=" * 60)

    db = LocalDatabase()
    Config.load_cached_credentials(db)

    douban_ok = False
    trakt_ok = False

    # -------------------------------------------------------------
    # 1. 豆瓣账号检查
    # -------------------------------------------------------------
    print("\n[1/3] 正在检查【豆瓣】账号连通性...")
    douban = None

    if Config.DOUBAN_COOKIE:
        try:
            douban = DoubanClient(Config.DOUBAN_USER_ID, Config.DOUBAN_COOKIE)
            status = douban.test_connection()
            if status.get("status") == "ok":
                print(f"✔ 豆瓣 Cookie 连通成功！")
                print(f"  用户 ID: {douban.user_id}")
                print(f"  网页标识: {status.get('page_title')}")
                print(f"  CSRF ck 状态: {'✔ 已识别' if status.get('has_ck') else '✘ 缺失'}")
                douban_ok = True
            else:
                print(f"⚠ 豆瓣警告: {status.get('message')}")
        except Exception as e:
            print(f"⚠ 现有 Cookie 连通失败: {e}")

    # If cookie failed but username/password provided, try password login
    if not douban_ok and Config.DOUBAN_USERNAME and Config.DOUBAN_PASSWORD:
        print(f"正在尝试使用账号密码自动登录豆瓣: {Config.DOUBAN_USERNAME[:3]}****...")
        try:
            douban = DoubanClient.login_by_password(Config.DOUBAN_USERNAME, Config.DOUBAN_PASSWORD, db=db)
            status = douban.test_connection()
            if status.get("status") == "ok":
                print(f"✔ 豆瓣账号密码登录成功！已自动提取 Cookie 并保存至本地缓存。")
                douban_ok = True
        except Exception as e:
            print(f"✘ 豆瓣账号密码登录失败: {e}")

    if not douban_ok:
        print("\n" + "-" * 50)
        print("【豆瓣配置指引】")
        print("青龙环境变量只需配置 1 条：DOUBAN_AUTH")
        print("推荐直接填入 Cookie（电脑打开 movie.douban.com 登录后，F12 网络请求复制完整 Cookie）")
        print("脚本会自动从 Cookie 中提取用户ID和CSRF码，无需额外设置 DOUBAN_USER_ID！")
        print("-" * 50)

    # -------------------------------------------------------------
    # 2. Trakt 账号检查
    # -------------------------------------------------------------
    print("\n[2/3] 正在检查【Trakt】账号连通性...")
    trakt = None

    if Config.TRAKT_CLIENT_ID and Config.TRAKT_ACCESS_TOKEN:
        try:
            trakt = TraktClient(Config.TRAKT_CLIENT_ID, Config.TRAKT_ACCESS_TOKEN)
            user_info = trakt.test_connection()
            print(f"✔ Trakt 连通成功！")
            print(f"  用户名: {user_info.get('username')}")
            print(f"  VIP 会员: {user_info.get('vip')}")
            trakt_ok = True
        except Exception as e:
            print(f"⚠ 现有 Token 验证未通过: {e}")

    # If token expired but refresh token available, try auto-refresh
    if not trakt_ok and Config.TRAKT_REFRESH_TOKEN and Config.TRAKT_CLIENT_ID and Config.TRAKT_CLIENT_SECRET:
        print("检测到 Trakt Refresh Token，正在尝试全自动无感续期...")
        try:
            trakt = TraktClient.refresh_access_token(
                Config.TRAKT_CLIENT_ID,
                Config.TRAKT_CLIENT_SECRET,
                Config.TRAKT_REFRESH_TOKEN,
                db=db,
            )
            user_info = trakt.test_connection()
            print(f"✔ Trakt Token 自动续期成功！用户名: {user_info.get('username')}")
            trakt_ok = True
        except Exception as e:
            print(f"✘ Token 续期失败: {e}")

    if not trakt_ok:
        print("\n" + "-" * 50)
        print("【Trakt 配置指引】")
        print("青龙环境变量只需配置 1 条：TRAKT_AUTH")
        print("格式可以为：Client_ID#Access_Token")
        print("或者直接粘贴在 trakt.tv 控制台提取的整段字符串：")
        print("TRAKT_CLIENT_ID=xxxx TRAKT_ACCESS_TOKEN=yyyy")
        print("-" * 50)

    # -------------------------------------------------------------
    # 3. 通知测试
    # -------------------------------------------------------------
    print("\n[3/3] 正在测试青龙推送通知...")
    if douban_ok and trakt_ok:
        msg = "🎉 恭喜！豆瓣与 Trakt 双端账号登录与连通性验证全部通过！日常定时同步任务已可以正常执行。"
        print("✔ " + msg)
        send_notification("豆瓣-Trakt 同步服务测试", msg)
    else:
        fail_reasons = []
        if not douban_ok:
            fail_reasons.append("豆瓣未登录或Cookie失效")
        if not trakt_ok:
            fail_reasons.append("Trakt凭证未通过")
        msg = f"⚠ 账号连通性检查未通过: {', '.join(fail_reasons)}。请检查青龙环境变量配置。"
        print(msg)
        send_notification("豆瓣-Trakt 登录异常提醒", msg)

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
