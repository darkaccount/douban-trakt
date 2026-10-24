# -*- coding: utf-8 -*-
"""
核心依赖自检与自动安装（供青龙入口脚本使用）。

本模块绝不能在顶层导入任何第三方包，否则会在依赖缺失时连带崩溃。
"""
import importlib
import subprocess
import sys

# (导入名, pip 包名)
REQUIRED_PACKAGES = [
    ("requests", "requests>=2.31.0"),
    ("bs4", "beautifulsoup4>=4.12.0"),
]


def _missing_packages():
    missing = []
    for mod_name, pkg_spec in REQUIRED_PACKAGES:
        try:
            importlib.import_module(mod_name)
        except ImportError:
            missing.append(pkg_spec)
    return missing


def ensure_dependencies():
    """确保核心依赖可导入；缺失时自动 pip 安装一次。

    返回 True 表示依赖全部就绪，False 表示仍缺失（调用方应退出）。
    """
    missing = _missing_packages()
    if not missing:
        return True

    print(f"⚠ 检测到缺少核心依赖: {', '.join(missing)}")
    print("正在尝试自动安装（等同于手动 pip install）...")

    base_cmd = [sys.executable, "-m", "pip", "install", "--no-cache-dir"]
    res = subprocess.run(base_cmd + missing)
    if res.returncode != 0:
        # 新版 Debian/Ubuntu 容器可能因 PEP 668 拒绝安装，追加该参数重试一次
        print("常规安装未成功，尝试附带 --break-system-packages 重试...")
        res = subprocess.run(base_cmd + ["--break-system-packages"] + missing)

    # 以实际导入结果为准（pip 成功但装进了别的解释器等情况）
    still_missing = _missing_packages()
    if still_missing:
        print("=" * 60)
        print(f"✘ 自动安装后仍缺少依赖: {', '.join(still_missing)}")
        print("👉 请在青龙面板「依赖管理」->「Python3」中手动添加: requests beautifulsoup4")
        print("   添加后重跑本任务即可；也可能是网络受限，稍后重试。")
        print("=" * 60)
        return False

    print("✔ 核心依赖自动安装成功。")
    return True
