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
    res = subprocess.run(cmd, shell=True)
    if res.returncode == 0:
        print(f"✔ 成功: {desc}")
        return True
    else:
        print(f"✘ 失败: {desc} (退出码 {res.returncode})")
        return False


def main():
    print("=" * 60)
    print("【青龙任务 1/3】豆瓣与Trakt同步脚本 - 依赖安装与更新")
    print("=" * 60)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    print(f"脚本目录: {script_dir}")
    print(f"Python 解释器: {sys.executable} ({sys.version.split()[0]})")

    # 1. 查找 requirements.txt（优先检查当前目录，其次检查青龙对应 repo 目录）
    candidate_paths = [
        os.path.join(script_dir, "requirements.txt"),
        os.path.join(script_dir.replace("/scripts/", "/repo/"), "requirements.txt"),
        os.path.join(script_dir.replace("\\scripts\\", "\\repo\\"), "requirements.txt"),
    ]
    parent_dir = os.path.dirname(script_dir)
    repo_base = parent_dir.replace("/scripts", "/repo").replace("\\scripts", "\\repo")
    folder_name = os.path.basename(script_dir)
    candidate_paths.append(os.path.join(repo_base, folder_name, "requirements.txt"))

    req_file = None
    for p in candidate_paths:
        if os.path.exists(p):
            req_file = p
            break

    # 2. 安装 / 更新 Python 依赖包
    packages = ["requests>=2.31.0", "beautifulsoup4>=4.12.0", "python-dotenv>=1.0.0", "tqdm>=4.66.0", "rich>=13.7.0"]
    if req_file:
        print(f"找到依赖文件: {req_file}")
        cmd = f'"{sys.executable}" -m pip install -r "{req_file}" --upgrade'
        run_cmd(cmd, f"根据 {os.path.basename(req_file)} 安装/更新依赖")
    else:
        print("未在脚本目录检测到 requirements.txt，将自动执行核心依赖包直接安装...")
        cmd = f'"{sys.executable}" -m pip install {" ".join(packages)} --upgrade'
        run_cmd(cmd, "安装核心 Python 依赖包 (requests, beautifulsoup4 等)")

    # 3. 校验关键依赖安装状态
    print("\n[依赖状态检测]")
    deps = [("requests", "requests"), ("bs4", "beautifulsoup4"), ("dotenv", "python-dotenv"), ("tqdm", "tqdm")]
    all_ok = True
    for mod_name, pkg_name in deps:
        try:
            __import__(mod_name)
            print(f"  ✔ {pkg_name} ({mod_name}): 已就绪")
        except ImportError:
            print(f"  ✘ {pkg_name} ({mod_name}): 未安装成功")
            all_ok = False

    # 4. 初始化本地 SQLite 缓存数据库
    try:
        from core.database import LocalDatabase
        db = LocalDatabase()
        print("✔ 本地 SQLite 缓存数据库 (cache.db) 初始化/结构检查完成")
    except Exception as e:
        print(f"✘ 数据库初始化异常: {e}")

    print("\n" + "=" * 60)
    if all_ok:
        print("✔ 依赖安装与准备步骤已全部就绪！")
        print("接下来请确认青龙面板环境变量设置，并运行【任务 2】ql_login.py 验证账号。")
    else:
        print("⚠ 部分依赖未能通过 pip 自动安装。")
        print("若青龙容器内网络受限，可前往青龙面板「依赖管理」->「Python3」手动添加以下依赖:")
        print("  - requests")
        print("  - beautifulsoup4")
        print("  - tqdm")
    print("=" * 60)


if __name__ == "__main__":
    main()
