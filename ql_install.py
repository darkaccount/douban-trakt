# -*- coding: utf-8 -*-
# new Env('豆瓣-Trakt 1.依赖安装与更新')
# cron: 0 0 1 1 *
"""
cron: 0 0 1 1 *
new Env('豆瓣-Trakt 1.依赖安装与更新')
"""
import sys
import os
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def run_cmd(cmd, desc):
    print(f"\n[执行中] {desc}...")
    res = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    if res.returncode == 0:
        print(f"✔ 成功: {desc}")
        if res.stdout.strip():
            print(res.stdout.strip())
        return True
    else:
        print(f"✘ 失败: {desc} (代码 {res.returncode})")
        if res.stderr.strip():
            print(res.stderr.strip())
        return False


def main():
    print("=" * 60)
    print("【青龙任务 1/3】豆瓣与Trakt同步脚本 - 依赖安装与更新")
    print("=" * 60)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    print(f"工作目录: {script_dir}")
    print(f"Python 版本: {sys.version.split()[0]}")

    # 1. Check Git update
    if os.path.exists(os.path.join(script_dir, ".git")):
        run_cmd("git pull", "拉取 GitHub / Gitee 最新代码")

    # 2. Install / Upgrade dependencies
    req_file = os.path.join(script_dir, "requirements.txt")
    if os.path.exists(req_file):
        cmd = f'"{sys.executable}" -m pip install -r "{req_file}" --upgrade'
        run_cmd(cmd, "安装/更新 Python 依赖包 (requirements.txt)")

    # 3. Initialize SQLite cache database
    try:
        from core.database import LocalDatabase
        db = LocalDatabase()
        print("✔ 本地 SQLite 缓存数据库 (cache.db) 初始化/结构检查完成")
    except Exception as e:
        print(f"✘ 数据库初始化异常: {e}")

    print("\n" + "=" * 60)
    print("✔ 安装与更新步骤已全部就绪！")
    print("接下来请配置青龙环境变量，并运行【任务 2】ql_login.py 验证账号。")
    print("=" * 60)


if __name__ == "__main__":
    main()
