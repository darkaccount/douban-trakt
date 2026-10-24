import argparse
import sys
import os

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from core.config import Config
from core.database import LocalDatabase
from core.trakt_client import TraktClient
from core.douban_client import DoubanClient
from core.matcher import Matcher, DiffResult
from core.syncer import Syncer

console = Console()


def init_clients(db: LocalDatabase):
    """Validate credentials and initialize clients."""
    if not Config.validate_trakt():
        console.print(
            Panel(
                "[bold red]缺少 Trakt 配置！[/bold red]\n"
                "请在 .env 文件中配置：\n"
                "TRAKT_CLIENT_ID=你的Web端Client_ID\n"
                "TRAKT_ACCESS_TOKEN=你的Web端Access_Token\n\n"
                "[yellow]获取方法：[/yellow]在电脑浏览器打开 trakt.tv 登录后，按 F12 并在 Console 中输入：\n"
                "[cyan]const key = Object.keys(localStorage).find(k => k.startsWith('oidc.user:'));\n"
                "const d = JSON.parse(localStorage.getItem(key));\n"
                "const clientId = d.client_id || d.profile?.aud || key.split(':').pop();\n"
                "console.log(`TRAKT_CLIENT_ID=${clientId}\\nTRAKT_ACCESS_TOKEN=${d.access_token}`);[/cyan]",
                title="Trakt 认证缺失",
            )
        )
        sys.exit(1)

    if not Config.validate_douban():
        console.print(
            Panel(
                "[bold red]缺少 豆瓣 配置！[/bold red]\n"
                "请在 .env 文件中配置：\n"
                "DOUBAN_USER_ID=你的豆瓣用户ID\n"
                "DOUBAN_COOKIE=包含dbcl2与ck的Cookie完整字符串\n\n"
                "[yellow]获取方法：[/yellow]登录 movie.douban.com，按 F12 -> Network (网络) 刷新任意请求，复制 Request Headers 中的 Cookie。",
                title="豆瓣 认证缺失",
            )
        )
        sys.exit(1)

    trakt = TraktClient(Config.TRAKT_CLIENT_ID, Config.TRAKT_ACCESS_TOKEN)
    douban = DoubanClient(Config.DOUBAN_USER_ID, Config.DOUBAN_COOKIE)
    return trakt, douban


def cmd_check(args=None):
    """Check credentials and test connections."""
    console.print("\n[bold cyan]=== 正在检查账号连通性 ===[/bold cyan]")
    db = LocalDatabase()
    trakt, douban = init_clients(db)

    # Test Trakt
    try:
        trakt_user = trakt.test_connection()
        console.print(
            f"[green]✔ Trakt 连通成功！[/green] 用户名: [bold]{trakt_user.get('username')}[/bold] (VIP: {trakt_user.get('vip')})"
        )
    except Exception as e:
        console.print(f"[red]✘ Trakt 连接失败:[/red] {e}")

    # Test Douban
    try:
        douban_status = douban.test_connection()
        if douban_status.get("status") == "ok":
            console.print(
                f"[green]✔ 豆瓣 连通成功！[/green] 页面: [bold]{douban_status.get('page_title')}[/bold] (CSRF ck已识别: {douban_status.get('has_ck')})"
            )
        else:
            console.print(f"[yellow]⚠ 豆瓣警告:[/yellow] {douban_status.get('message')}")
    except Exception as e:
        console.print(f"[red]✘ 豆瓣连接失败:[/red] {e}")


def run_diff(
    trakt: TraktClient,
    douban: DoubanClient,
    db: LocalDatabase,
    force_full_douban: bool = False,
    resolve_remaining_imdb: bool = False,
    force_refresh_trakt: bool = False,
) -> DiffResult:
    """Fetch data from both platforms and calculate diff."""
    console.print("\n[bold cyan]1. 获取 Trakt 看过记录（电影 + 电视剧，智能缓存模式）...[/bold cyan]")
    trakt_items = trakt.get_all_watched(db=db, force_refresh=force_refresh_trakt)

    console.print("\n[bold cyan]2. 获取 豆瓣 看过记录（智能缓存模式）...[/bold cyan]")
    douban_items = douban.get_watched_movies(db, force_full=force_full_douban)

    console.print("\n[bold cyan]3. 比对两端差异...[/bold cyan]")
    diff = Matcher.compute_diff(
        trakt_items,
        douban_items,
        douban,
        db,
        resolve_remaining_imdb=resolve_remaining_imdb,
        trakt_client=trakt,
    )
    return diff


def print_diff_report(diff: DiffResult):
    """Render a visual report of the diff results."""
    table = Table(title="[bold]Trakt 与 豆瓣 影视比对结果[/bold]")
    table.add_column("分类", style="cyan", no_wrap=True)
    table.add_column("数量", style="bold magenta", justify="right")
    table.add_column("说明", style="white")

    table.add_row("两端共有 (已对齐)", str(len(diff.both)), "两平台均已标记看过，保持原样，无需同步")
    table.add_row("豆瓣独有 (待同步到 Trakt)", str(len(diff.douban_only)), "豆瓣已看但 Trakt 未标记，将携带原观看日期同步至 Trakt")
    table.add_row("Trakt独有 (待同步到 豆瓣)", str(len(diff.trakt_only)), "Trakt已看但 豆瓣 未标记，将检索豆瓣对应条目并同步")
    if diff.trakt_ignored:
        table.add_row("Trakt独有 (已忽略/豆瓣未收录)", str(len(diff.trakt_ignored)), "经检索确认豆瓣未收录或已加入忽略名单，无需同步")
    if diff.douban_missing_imdb:
        table.add_row("豆瓣待核实 (未对齐 IMDb)", str(len(diff.douban_missing_imdb)), "尚未匹配到对应 IMDb 编号，暂不自动推送")

    console.print("\n", table, "\n")

    # Preview sample items
    if diff.douban_only:
        sub_table = Table(title=f"待同步至 Trakt 预览（前 {min(5, len(diff.douban_only))} / {len(diff.douban_only)} 部）")
        sub_table.add_column("标题", style="green")
        sub_table.add_column("年份", style="yellow")
        sub_table.add_column("IMDb ID", style="cyan")
        sub_table.add_column("观看日期", style="magenta")
        for m in diff.douban_only[:5]:
            sub_table.add_row(m.title, str(m.year or "-"), m.imdb_id or "-", m.watched_at or "-")
        console.print(sub_table, "\n")

    if diff.trakt_only:
        sub_table = Table(title=f"待同步至 豆瓣 预览（前 {min(5, len(diff.trakt_only))} / {len(diff.trakt_only)} 部）")
        sub_table.add_column("标题", style="green")
        sub_table.add_column("年份", style="yellow")
        sub_table.add_column("IMDb ID", style="cyan")
        sub_table.add_column("观看日期", style="magenta")
        for m in diff.trakt_only[:5]:
            sub_table.add_row(m.title, str(m.year or "-"), m.imdb_id or "-", m.watched_at or "-")
        console.print(sub_table, "\n")


def cmd_diff(args=None):
    """Run diff only (Dry-Run preview)."""
    db = LocalDatabase()
    trakt, douban = init_clients(db)
    force_full = getattr(args, "force_full", False)
    resolve_missing = getattr(args, "resolve_missing", False)
    refresh_trakt = getattr(args, "refresh_trakt", False)

    diff = run_diff(
        trakt,
        douban,
        db,
        force_full_douban=force_full,
        resolve_remaining_imdb=resolve_missing,
        force_refresh_trakt=refresh_trakt,
    )
    print_diff_report(diff)
    return diff


def cmd_sync_to_trakt(args=None):
    """Sync Douban-only items to Trakt."""
    db = LocalDatabase()
    trakt, douban = init_clients(db)
    dry_run = getattr(args, "dry_run", False)
    force_full = getattr(args, "force_full", False)
    resolve_missing = getattr(args, "resolve_missing", False)
    refresh_trakt = getattr(args, "refresh_trakt", False)

    diff = run_diff(
        trakt,
        douban,
        db,
        force_full_douban=force_full,
        resolve_remaining_imdb=resolve_missing,
        force_refresh_trakt=refresh_trakt,
    )
    print_diff_report(diff)

    if not diff.douban_only:
        console.print("[green]没有需要同步到 Trakt 的独有条目！[/green]")
        return

    Syncer.sync_to_trakt(diff.douban_only, trakt, db, dry_run=dry_run)


def cmd_sync_to_douban(args=None):
    """Sync Trakt-only items to Douban."""
    db = LocalDatabase()
    trakt, douban = init_clients(db)
    dry_run = getattr(args, "dry_run", False)
    force_full = getattr(args, "force_full", False)
    resolve_missing = getattr(args, "resolve_missing", False)
    refresh_trakt = getattr(args, "refresh_trakt", False)

    diff = run_diff(
        trakt,
        douban,
        db,
        force_full_douban=force_full,
        resolve_remaining_imdb=resolve_missing,
        force_refresh_trakt=refresh_trakt,
    )
    print_diff_report(diff)

    if not diff.trakt_only:
        console.print("[green]没有需要同步到 豆瓣 的独有条目！[/green]")
        return

    Syncer.sync_to_douban(
        diff.trakt_only,
        douban,
        db,
        delay_min=Config.SYNC_DELAY_MIN,
        delay_max=Config.SYNC_DELAY_MAX,
        dry_run=dry_run,
        trakt_client=trakt,
    )


def cmd_sync_all(args=None):
    """Bidirectional difference sync."""
    db = LocalDatabase()
    trakt, douban = init_clients(db)
    dry_run = getattr(args, "dry_run", False)
    force_full = getattr(args, "force_full", False)
    resolve_missing = getattr(args, "resolve_missing", False)
    refresh_trakt = getattr(args, "refresh_trakt", False)

    diff = run_diff(
        trakt,
        douban,
        db,
        force_full_douban=force_full,
        resolve_remaining_imdb=resolve_missing,
        force_refresh_trakt=refresh_trakt,
    )
    print_diff_report(diff)

    if diff.douban_only:
        console.print("\n[bold cyan]=== 执行同步：豆瓣独有 -> Trakt ===[/bold cyan]")
        Syncer.sync_to_trakt(diff.douban_only, trakt, db, dry_run=dry_run)

    if diff.trakt_only:
        console.print("\n[bold cyan]=== 执行同步：Trakt独有 -> 豆瓣 ===[/bold cyan]")
        Syncer.sync_to_douban(
            diff.trakt_only,
            douban,
            db,
            delay_min=Config.SYNC_DELAY_MIN,
            delay_max=Config.SYNC_DELAY_MAX,
            dry_run=dry_run,
            trakt_client=trakt,
        )


def cmd_manage_ignored(args=None):
    """View and manage ignored items."""
    db = LocalDatabase()
    account_id = getattr(args, "account", None) if args else None
    unignore_target = getattr(args, "unignore", None) if args else None

    if unignore_target:
        removed = 0
        for item in db.get_ignored_items(None):
            if item["identifier"] == unignore_target:
                db.unignore_item(item["account_id"], item["identifier"])
                removed += 1
        if removed:
            console.print(f"[green]✔ 已解除忽略 {removed} 条记录: {unignore_target}[/green]")
        else:
            console.print(f"[yellow]未找到标识为 {unignore_target} 的忽略记录。[/yellow]")
        return

    ignored = db.get_ignored_items(account_id)
    if not ignored:
        console.print("[yellow]当前忽略表中没有条目。[/yellow]")
        return

    table = Table(title=f"[bold]已忽略 / 豆瓣未收录条目列表 ({len(ignored)} 部)[/bold]")
    table.add_column("标识 (IMDb / 编号)", style="cyan")
    table.add_column("标题", style="green")
    table.add_column("年份", style="yellow")
    table.add_column("原因", style="magenta")
    table.add_column("记录时间", style="dim")

    for item in ignored[:30]:
        table.add_row(
            str(item["identifier"] or "-"),
            str(item["title"] or "-"),
            str(item["year"] or "-"),
            str(item["reason"] or "-"),
            str(item["created_at"] or "-"),
        )

    console.print(table)
    if len(ignored) > 30:
        console.print(f"[dim]（仅展示前 30 条，共 {len(ignored)} 条）[/dim]")


def interactive_menu():
    """Interactive visual menu for running commands directly without typing flags."""
    while True:
        menu_text = (
            f"[bold green]当前账号配置：[/bold green]\n"
            f"  豆瓣 ID: [cyan]{Config.DOUBAN_USER_ID or '未设置'}[/cyan]\n"
            f"  Trakt:   [cyan]{'已配置' if Config.validate_trakt() else '未配置'}[/cyan]\n\n"
            "[bold white]请选择功能编号：[/bold white]\n"
            "  [bold yellow][1][/bold yellow] 检查账号连通性 (Check)\n"
            "  [bold yellow][2][/bold yellow] 查看差异比对报告 (Diff 预览，极速本地匹配)\n"
            "  [bold yellow][3][/bold yellow] 将豆瓣独有影视同步到 Trakt (保留观影日期)\n"
            "  [bold yellow][4][/bold yellow] 将 Trakt 独有影视同步到 豆瓣 (受控写入)\n"
            "  [bold yellow][5][/bold yellow] 一键双向增量同步 (互相补全各自独有条目)\n"
            "  [bold yellow][6][/bold yellow] 深度解析：在线补全剩余未对齐条目的 IMDb 编号\n"
            "  [bold yellow][7][/bold yellow] 强制刷新 Trakt 远程缓存\n"
            "  [bold yellow][8][/bold yellow] 查看已忽略条目列表 (豆瓣未收录/下架条目)\n"
            "  [bold yellow][0][/bold yellow] 退出程序\n"
        )
        console.print(Panel(menu_text, title="[bold cyan]🎬 Trakt <-> 豆瓣 影视同步助手[/bold cyan]", border_style="blue"))

        choice = input("请输入选项编号 [0-8]: ").strip()

        if choice == "1":
            cmd_check()
        elif choice == "2":
            cmd_diff()
        elif choice == "3":
            cmd_sync_to_trakt()
        elif choice == "4":
            cmd_sync_to_douban()
        elif choice == "5":
            cmd_sync_all()
        elif choice == "6":
            class Args:
                force_full = False
                resolve_missing = True
                refresh_trakt = False
                dry_run = False
            cmd_diff(Args())
        elif choice == "7":
            class RefreshArgs:
                force_full = False
                resolve_missing = False
                refresh_trakt = True
                dry_run = False
            cmd_diff(RefreshArgs())
        elif choice == "8":
            cmd_manage_ignored()
            target = input("\n如需解除忽略，请输入条目标识 (直接回车返回): ").strip()
            if target:
                class UnignoreArgs:
                    unignore = target
                    account = None
                cmd_manage_ignored(UnignoreArgs())
        elif choice in ("0", "q", "quit", "exit"):
            console.print("[green]已退出。[/green]")
            break
        else:
            console.print("[red]输入无效，请输入 0 到 8 之间的数字。[/red]")

        input("\n按回车键返回主菜单...")


def main():
    # If no command arguments provided, launch interactive menu
    if len(sys.argv) == 1:
        interactive_menu()
        return

    parser = argparse.ArgumentParser(description="Trakt 与 豆瓣 影视看过列表差异比对与双向增量同步工具")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # check
    subparsers.add_parser("check", help="检查凭证有效性与两端账号连通状态")

    # diff
    p_diff = subparsers.add_parser("diff", help="仅比对并输出差异报告（Dry Run 预览）")
    p_diff.add_argument("--force-full", action="store_true", help="强制全量抓取豆瓣历史，忽略增量缓存早期终止优化")
    p_diff.add_argument("--resolve-missing", action="store_true", help="对未匹配的疑似独有条目在线爬取豆瓣条目解析 IMDb 编号")
    p_diff.add_argument("--refresh-trakt", action="store_true", help="强制全量重新拉取 Trakt 远程数据并更新本地缓存")

    # sync-to-trakt
    p_to_trakt = subparsers.add_parser("sync-to-trakt", help="将豆瓣独有电影/剧集同步到 Trakt（保留观看日期）")
    p_to_trakt.add_argument("--dry-run", action="store_true", help="演练模式，仅打印将要同步的内容，不实际写入")
    p_to_trakt.add_argument("--force-full", action="store_true", help="强制全量抓取豆瓣历史")
    p_to_trakt.add_argument("--resolve-missing", action="store_true", help="对未匹配的疑似独有条目在线爬取解析 IMDb 编号")
    p_to_trakt.add_argument("--refresh-trakt", action="store_true", help="强制全量重新拉取 Trakt 远程数据")

    # sync-to-douban
    p_to_douban = subparsers.add_parser("sync-to-douban", help="将 Trakt 独有电影/剧集同步到豆瓣（保留观看日期）")
    p_to_douban.add_argument("--dry-run", action="store_true", help="演练模式，仅打印将要同步的内容，不实际写入")
    p_to_douban.add_argument("--force-full", action="store_true", help="强制全量抓取豆瓣历史")
    p_to_douban.add_argument("--resolve-missing", action="store_true", help="对未匹配的疑似独有条目在线爬取解析 IMDb 编号")
    p_to_douban.add_argument("--refresh-trakt", action="store_true", help="强制全量重新拉取 Trakt 远程数据")

    # sync-all
    p_all = subparsers.add_parser("sync-all", help="双向增量同步（互相同步各自独有条目）")
    p_all.add_argument("--dry-run", action="store_true", help="演练模式，仅打印将要同步的内容，不实际写入")
    p_all.add_argument("--force-full", action="store_true", help="强制全量抓取豆瓣历史")
    p_all.add_argument("--resolve-missing", action="store_true", help="对未匹配的疑似独有条目在线爬取解析 IMDb 编号")
    p_all.add_argument("--refresh-trakt", action="store_true", help="强制全量重新拉取 Trakt 远程数据")

    # ignored
    p_ignored = subparsers.add_parser("ignored", help="查看已忽略 / 豆瓣未收录条目列表")
    p_ignored.add_argument("--account", default=None, help="仅查看指定账号的忽略记录")
    p_ignored.add_argument(
        "--unignore",
        metavar="IDENTIFIER",
        default=None,
        help="解除忽略指定条目（identifier 见列表第一列，如 IMDb ID）",
    )

    args = parser.parse_args()

    if args.command == "check":
        cmd_check(args)
    elif args.command == "diff":
        cmd_diff(args)
    elif args.command == "sync-to-trakt":
        cmd_sync_to_trakt(args)
    elif args.command == "sync-to-douban":
        cmd_sync_to_douban(args)
    elif args.command == "sync-all":
        cmd_sync_all(args)
    elif args.command == "ignored":
        cmd_manage_ignored(args)


if __name__ == "__main__":
    main()
