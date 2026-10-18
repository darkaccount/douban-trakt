import os
import sys
import subprocess
import requests


def send_notification(title: str, content: str):
    """
    Unified notification sender compatible with Qinglong and standalone environments.
    Priority:
    1. Qinglong native Python sendNotify.py
    2. Qinglong native Node.js sendNotify.js
    3. Direct webhooks: ServerChan, Bark, Telegram, PushPlus, DingTalk, WeChat Work, Feishu.
    """
    print(f"\n[通知推送] {title}\n{content}")

    # 1. Try Qinglong native Python sendNotify
    try:
        ql_paths = ["/ql/data/scripts", "/ql/scripts", os.path.dirname(os.path.abspath(__file__))]
        for p in ql_paths:
            if p not in sys.path and os.path.exists(p):
                sys.path.append(p)

        from sendNotify import send
        send(title, content)
        return
    except Exception:
        pass

    # 2. Try Qinglong native Node.js sendNotify.js
    for js_dir in ["/ql/data/scripts", "/ql/scripts"]:
        js_file = os.path.join(js_dir, "sendNotify.js")
        if os.path.exists(js_file):
            try:
                # Sanitize content for shell
                safe_title = title.replace('"', '\\"').replace("`", "")
                safe_content = content.replace('"', '\\"').replace("`", "")
                cmd = f'node -e "const notify = require(\'{js_file}\'); notify.sendNotify(\'{safe_title}\', \'{safe_content}\');"'
                res = subprocess.run(cmd, shell=True, text=True, timeout=12, capture_output=True)
                if res.returncode == 0:
                    return
            except Exception:
                pass

    # 3. ServerChan (Server酱)
    server_key = os.getenv("PUSH_KEY", "").strip()
    if server_key:
        try:
            url = f"https://sctapi.ftqq.com/{server_key}.send" if server_key.startswith("SCT") else f"https://sc.ftqq.com/{server_key}.send"
            requests.post(url, data={"title": title, "desp": content}, timeout=10)
        except Exception as e:
            print(f"Server酱推送失败: {e}")

    # 4. Bark (iOS)
    bark_key = os.getenv("BARK_PUSH", "").strip()
    if bark_key:
        try:
            if not bark_key.startswith("http"):
                bark_url = f"https://api.day.app/{bark_key}/{title}/{content}"
            else:
                bark_url = f"{bark_key}/{title}/{content}"
            requests.get(bark_url, timeout=10)
        except Exception as e:
            print(f"Bark推送失败: {e}")

    # 5. Telegram Bot
    tg_token = os.getenv("TG_BOT_TOKEN", "").strip()
    tg_chat_id = os.getenv("TG_USER_ID", "").strip()
    if tg_token and tg_chat_id:
        try:
            url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
            requests.post(url, json={"chat_id": tg_chat_id, "text": f"*{title}*\n\n{content}", "parse_mode": "Markdown"}, timeout=10)
        except Exception as e:
            print(f"Telegram推送失败: {e}")

    # 6. PushPlus (推送加)
    push_plus = os.getenv("PUSH_PLUS_TOKEN", "").strip()
    if push_plus:
        try:
            url = "http://www.pushplus.plus/send"
            requests.post(url, json={"token": push_plus, "title": title, "content": content}, timeout=10)
        except Exception as e:
            print(f"PushPlus推送失败: {e}")

    # 7. 企业微信机器人 (QYWX_KEY)
    qywx_key = os.getenv("QYWX_KEY", "").strip()
    if qywx_key:
        try:
            url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={qywx_key}"
            requests.post(url, json={"msgtype": "text", "text": {"content": f"{title}\n\n{content}"}}, timeout=10)
        except Exception as e:
            print(f"企业微信推送失败: {e}")

    # 8. 飞书机器人 (FSKEY)
    fs_key = os.getenv("FSKEY", "").strip()
    if fs_key:
        try:
            url = f"https://open.feishu.cn/open-apis/bot/v2/hook/{fs_key}"
            requests.post(url, json={"msg_type": "text", "content": {"text": f"{title}\n\n{content}"}}, timeout=10)
        except Exception as e:
            print(f"飞书推送失败: {e}")
