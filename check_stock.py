"""
Apple整備済製品 在庫監視スクリプト
- 一覧ページに埋め込まれている商品データ(メモリ・ストレージ・価格つき)を直接読み取る
- 「14インチMacBook Pro」「無印M5」「メモリ24GBまたは32GB」の商品を探す
- 前回(state.json)になかった商品が出てきたら、その商品だけDiscordに通知する
- ページが読めなかった時・通知に失敗した時は、エラーで止めてstate.jsonを書き換えない
  (次回の実行でやり直せるようにするため)
"""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

# ===== 設定 =====
TARGET_URL = "https://www.apple.com/jp/shop/refurbished/mac/macbook-pro"
TITLE_KEYWORD = "14インチMacBook Pro"  # 商品名にこれを含むものだけ対象
CHIP_KEYWORD = "M5チップ"  # 無印M5だけ(「M5 Proチップ」「M5 Maxチップ」は一致しない)
TARGET_MEMORY = ["24GB", "32GB"]  # 通知したいメモリ容量
STATE_FILE = Path("state.json")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# 一覧ページの商品データから必要な項目だけ取り出す
EXTRACT_TILES_JS = """
() => window.REFURB_GRID_BOOTSTRAP.tiles.map(t => {
    const dims = (t.filters && t.filters.dimensions) || {};
    const price = (t.price && t.price.currentPrice) || {};
    return {
        title: t.title || "",
        part: t.partNumber || "",
        url: t.productDetailsUrl || "",
        price: price.amount || "",
        memory: dims.tsMemorySize || "",
        storage: dims.dimensionCapacity || "",
    };
})
"""


def fetch_tiles():
    """一覧ページを開いて、掲載されている全商品のデータを取得する"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, locale="ja-JP")
        page = context.new_page()
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_function(
            "() => window.REFURB_GRID_BOOTSTRAP"
            " && Array.isArray(window.REFURB_GRID_BOOTSTRAP.tiles)",
            timeout=30000,
        )
        tiles = page.evaluate(EXTRACT_TILES_JS)
        browser.close()
    return tiles


def key_from_part(part: str) -> str:
    """型番「G1KK4J/A」→ 比較用のキー「g1kk4j」"""
    return part.split("/")[0].strip().lower()


def key_from_url(url: str) -> str:
    """商品URLから比較用のキーを取り出す(以前の形式のstate.json用)"""
    m = re.search(r"/shop/product/([^/?]+)", url or "")
    return m.group(1).lower() if m else ""


def select_matches(tiles: list):
    """条件に合う商品を選ぶ。戻り値は (14インチ無印M5の総数, 通知対象のリスト)"""
    base_count = 0
    matches = []
    for t in tiles:
        title = t.get("title", "")
        if TITLE_KEYWORD not in title or CHIP_KEYWORD not in title:
            continue
        base_count += 1

        memory = (t.get("memory") or "").upper()  # "32gb" → "32GB"
        # メモリが読み取れなかった商品は、見逃しを防ぐため「不明」として通知対象に残す
        if memory and memory not in TARGET_MEMORY:
            continue

        url = t.get("url", "").split("?")[0]
        if url.startswith("/"):
            url = "https://www.apple.com" + url

        matches.append({
            "key": key_from_part(t.get("part", "")) or key_from_url(url),
            "name": title,
            "url": url,
            "memory": memory or "不明",
            "storage": (t.get("storage") or "").upper() or "不明",
            "price": t.get("price") or "不明",
            "nano": "Nano-texture" in title,
        })
    return base_count, matches


def load_previous_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"found": False, "items": []}


def save_state(found: bool, items: list):
    STATE_FILE.write_text(
        json.dumps({"found": found, "items": items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_message(items: list) -> str:
    """Discordの2000文字制限に収まるよう、商品単位で入る分だけ載せる"""
    char_limit = 1900
    header = "🎉 **新しい在庫が見つかりました！**\n\n"

    blocks = []
    total_len = len(header)
    for item in items:
        line = f"MacBook Pro / M5 / {item['memory']} / {item['storage']} / {item['price']}"
        if item.get("nano"):
            line += " / Nano-texture"
        block = f"{line}\n🔗 {item['url']}"
        added_len = len(block) + 2  # 区切りの空行ぶん
        if total_len + added_len > char_limit:
            break
        blocks.append(block)
        total_len += added_len

    text = header + "\n\n".join(blocks)
    remaining = len(items) - len(blocks)
    if remaining > 0:
        text += f"\n\n…他 {remaining} 件(Appleのページで確認してください)\n{TARGET_URL}"
    return text


def notify_discord(items: list) -> bool:
    """Discordに通知する。成功したらTrue、失敗したらFalseを返す"""
    if not DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL が設定されていません。", file=sys.stderr)
        return False

    payload = json.dumps({"content": build_message(items)}).encode("utf-8")
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            print("Discord通知 送信結果:", res.status)
        return True
    except Exception as e:
        print("Discord通知の送信に失敗しました:", e, file=sys.stderr)
        return False


def main():
    tiles = fetch_tiles()
    print(f"一覧ページの商品数: {len(tiles)}")
    if not tiles:
        sys.exit("一覧ページから商品を1件も読み取れませんでした。state.jsonは変更しません。")

    base_count, matches = select_matches(tiles)
    print(f"14インチ・無印M5の在庫: {base_count}件(うち通知対象 {'/'.join(TARGET_MEMORY)}: {len(matches)}件)")
    for m in matches:
        print(" -", m["memory"], m["storage"], m["price"], "Nano-texture" if m["nano"] else "", m["key"])

    prev_state = load_previous_state()
    prev_keys = {
        item.get("key") or key_from_url(item.get("url", ""))
        for item in prev_state.get("items", [])
    }

    # 前回のリストになかった商品 = 新入荷
    new_items = [m for m in matches if m["key"] not in prev_keys]

    if new_items:
        print(f"新入荷 {len(new_items)} 件を検出。Discordに通知します。")
        if not notify_discord(new_items):
            sys.exit("通知に失敗したため、state.jsonは変更しません(次回の実行で再通知します)。")
    elif matches:
        print("在庫はあるが新入荷なし。通知はスキップします。")
    else:
        print("該当商品なし。")

    save_state(len(matches) > 0, matches)


if __name__ == "__main__":
    main()
