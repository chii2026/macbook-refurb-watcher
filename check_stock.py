"""
Apple整備済製品 在庫監視スクリプト
- 一覧ページで「14インチ」「M5」を含む商品(の個別ページURL)を探す
- 見つかった候補ページをそれぞれ開いて、メモリ容量(16GB/24GB/32GBのいずれか)が
  記載されているか確認する
- 前回の状態(state.json)と比較し、「なし→あり」に変わったタイミングでDiscordに通知
"""

import json
import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# ===== 設定 =====
TARGET_URL = "https://www.apple.com/jp/shop/refurbished/mac/macbook-pro"
REQUIRED_KEYWORDS = ["14インチ", "M5"]  # 一覧ページの商品名にすべて含まれている必要がある条件
MEMORY_OPTIONS = ["16GB", "24GB", "32GB"]  # 個別ページの説明にこのいずれかが含まれればOK
STATE_FILE = Path("state.json")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def fetch_candidate_products(page):
    """一覧ページを開いて、REQUIRED_KEYWORDSをすべて含む商品の(名前, URL)一覧を取得する"""
    page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)  # 描画待ち

    links = page.eval_on_selector_all(
        "a[href*='/shop/product/']",
        "els => els.map(el => ({text: el.innerText.trim(), href: el.href}))",
    )

    seen = set()
    candidates = []
    for link in links:
        name = link.get("text", "")
        url = link.get("href", "")
        if not name or not url:
            continue
        if all(kw in name for kw in REQUIRED_KEYWORDS):
            if url not in seen:
                seen.add(url)
                candidates.append({"name": name, "url": url})
    return candidates


def check_memory_on_product_page(page, url):
    """商品個別ページを開いて、MEMORY_OPTIONSのいずれかが含まれるか確認する"""
    page.goto(url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(1500)
    body_text = page.inner_text("body")
    for mem in MEMORY_OPTIONS:
        if mem in body_text:
            return mem
    return None


def fetch_matching_products():
    """条件(14インチ・M5・メモリ16/24/32GBのいずれか)に合う商品を探す"""
    matches = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, locale="ja-JP")
        page = context.new_page()

        candidates = fetch_candidate_products(page)
        print(f"一覧ページでの候補数(14インチ・M5): {len(candidates)}")

        # 候補が多すぎる場合の安全弁(実行時間を抑えるため上限を設ける)
        MAX_CANDIDATES_TO_CHECK = 20
        for item in candidates[:MAX_CANDIDATES_TO_CHECK]:
            time.sleep(1)  # サイトへの負荷を抑えるための小休止
            memory = check_memory_on_product_page(page, item["url"])
            if memory:
                matches.append(f"{item['name']} - {memory} [{item['url']}]")

        browser.close()
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

    MAX_ITEMS = 10
    shown_items = items[:MAX_ITEMS]
    lines = "\n".join(f"・{item}" for item in shown_items)
    remaining = len(items) - len(shown_items)
    if remaining > 0:
        lines += f"\n…他 {remaining} 件"

    content = (
        "🎉 **在庫が見つかりました！**\n"
        f"{lines}\n\n"
        f"{TARGET_URL}"
    )
    content = content[:1900]  # Discordの2000文字制限に対する安全マージン

    payload = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            print("Discord通知 送信結果:", res.status)
    except Exception as e:
        print("Discord通知の送信に失敗しました:", e, file=sys.stderr)


def main():
    matches = fetch_matching_products()
    found_now = len(matches) > 0

    prev_state = load_previous_state()
    found_before = prev_state.get("found", False)

    print(f"現在の該当件数: {len(matches)}")
    for m in matches:
        print(" -", m)

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
