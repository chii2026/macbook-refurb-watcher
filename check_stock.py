"""
Apple整備済製品 在庫監視スクリプト
- 対象ページを開いて、条件に合う商品(14インチ / M5 / 32GB)が出品されているか確認
- 前回の状態(state.json)と比較し、「なし→あり」に変わったタイミングでDiscordに通知
"""

import json
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

# ===== 設定 =====
TARGET_URL = "https://www.apple.com/jp/shop/refurbished/mac/macbook-pro"
KEYWORDS = ["MacBook"]  # テスト用: 一時的に緩い条件にしています
STATE_FILE = Path("state.json")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def fetch_products():
    """ページを開いて商品名っぽいテキストの一覧を取得する"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, locale="ja-JP")
        page = context.new_page()
        page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)

        # 商品カードのテキストをまとめて取得する。
        # Appleのサイト構造は変わりやすいため、まずページ全体のテキストから
        # 「商品名らしき行」を正規表現で拾う方式にしておく(壊れにくさ優先)。
        page.wait_for_timeout(3000)  # 描画待ち
        body_text = page.inner_text("body")

        browser.close()
    return body_text


def find_matching_lines(body_text: str):
    """KEYWORDSを全て含む行を抽出する"""
    matches = []
    for line in body_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if all(kw in line for kw in KEYWORDS):
            matches.append(line)
    return matches


def load_previous_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"found": False, "items": []}


def save_state(found: bool, items: list):
    STATE_FILE.write_text(
        json.dumps({"found": found, "items": items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def notify_discord(items: list):
    if not DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL が設定されていません。通知をスキップします。")
        return

    import urllib.request

    lines = "\n".join(f"・{item}" for item in items)
    content = (
        "🎉 **在庫が見つかりました！**\n"
        f"{lines}\n\n"
        f"{TARGET_URL}"
    )
    payload = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            print("Discord通知 送信結果:", res.status)
    except Exception as e:
        print("Discord通知の送信に失敗しました:", e, file=sys.stderr)


def main():
    body_text = fetch_products()
    matches = find_matching_lines(body_text)
    found_now = len(matches) > 0

    prev_state = load_previous_state()
    found_before = prev_state.get("found", False)

    print(f"現在の該当件数: {len(matches)}")
    for m in matches:
        print(" -", m)

    # 「なし→あり」に変わった時だけ通知する
    if found_now and not found_before:
        print("新しく在庫が見つかりました。Discordに通知します。")
        notify_discord(matches)
    elif found_now and found_before:
        print("前回から継続して在庫あり。通知はスキップします。")
    else:
        print("該当商品なし。")

    save_state(found_now, matches)


if __name__ == "__main__":
    main()
