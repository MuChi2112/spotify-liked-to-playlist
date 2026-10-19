"""
整理指定歌單（預設：liked_to_playlist.py 建立的那個歌單）：

  1. 完全重複（歌名相同 + 歌手完全相同）→ 直接從歌單移除多餘的，只留最早加入的那首。
  2. 同一首歌但 feat. 名單不同（歌名相同，歌手名單有重疊但不完全一樣）→ 只印出來，不動它。
  3. 疑似翻唱（歌名相同，但歌手完全不同、毫無重疊）→ 只印出來，不動它。

比對邏輯是用「正規化後的歌名」＋「歌手名單」做分組，屬於簡單的文字比對，
不是 100% 準確（例如同名但完全不相關的兩首歌也可能被誤判成翻唱），
移除動作只套用在「完全重複」這一類，其餘兩類只會印出來讓你自己判斷。

使用方式：
  python dedupe_playlist.py

需要環境變數（沿用 .env）：
  SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET / SPOTIFY_REDIRECT_URI
  PLAYLIST_NAME（找不到 last_playlist.json 時，用這個名稱去你的歌單列表裡找）
"""

import itertools
import json
import os
import re
import sys
import time
from collections import defaultdict

from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException

load_dotenv()

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
PLAYLIST_NAME = os.getenv("PLAYLIST_NAME", "Liked Songs Playlist")

# 讀取私人歌單內容需要 playlist-read-private；移除曲目需要 playlist-modify-*。
SCOPE = "playlist-read-private playlist-modify-private playlist-modify-public"

PAGE_SIZE = 100
REMOVE_BATCH_SIZE = 100

FEAT_PATTERN = re.compile(
    r"\(feat[^)]*\)|\[feat[^\]]*\]|\bfeat\.?\s+.*$|\bft\.?\s+.*$|\bfeaturing\s+.*$",
    re.IGNORECASE,
)
BRACKET_PATTERN = re.compile(r"[\(\[][^)\]]*[\)\]]")
DASH_SUFFIX_PATTERN = re.compile(r"\s+-\s+.*$")
PUNCT_PATTERN = re.compile(r"[^\w\s]", re.UNICODE)
SPACE_PATTERN = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    t = title
    t = FEAT_PATTERN.sub("", t)
    t = BRACKET_PATTERN.sub("", t)
    t = DASH_SUFFIX_PATTERN.sub("", t)
    t = PUNCT_PATTERN.sub("", t)
    t = SPACE_PATTERN.sub(" ", t).strip().lower()
    return t


def normalize_artist(name: str) -> str:
    return SPACE_PATTERN.sub(" ", name).strip().lower()


def get_client() -> spotipy.Spotify:
    if not CLIENT_ID or not CLIENT_SECRET:
        sys.exit(
            "缺少 SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET，"
            "請先建立 .env 檔（可參考 .env.example）。"
        )
    auth_manager = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        cache_path=".cache-dedupe",
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth_manager)


def resolve_playlist_id(sp: spotipy.Spotify) -> tuple[str, str]:
    """回傳 (playlist_id, playlist_name)。優先讀 last_playlist.json，找不到就用名稱搜自己的歌單。"""
    if os.path.exists("last_playlist.json"):
        with open("last_playlist.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"從 last_playlist.json 讀到歌單：{data['name']!r} ({data['id']})")
        return data["id"], data["name"]

    print(f"找不到 last_playlist.json，改用名稱搜尋你的歌單：{PLAYLIST_NAME!r} ...")
    offset = 0
    while True:
        resp = sp.current_user_playlists(limit=50, offset=offset)
        items = resp.get("items", [])
        if not items:
            break
        for pl in items:
            if pl["name"] == PLAYLIST_NAME:
                return pl["id"], pl["name"]
        offset += 50
        if offset >= resp.get("total", 0):
            break
    sys.exit(f"找不到名稱為 {PLAYLIST_NAME!r} 的歌單，請確認 .env 裡的 PLAYLIST_NAME 或先跑 liked_to_playlist.py。")


def fetch_playlist_tracks(sp: spotipy.Spotify, playlist_id: str) -> list[dict]:
    """回傳歌單裡每首歌的 dict：position / id / uri / title / artists(tuple)。
    position 是歌單裡的絕對索引（含本地檔案/已下架曲目，才能對到移除用的 position）。"""
    items: list[dict] = []
    pos = 0
    offset = 0
    while True:
        resp = sp.playlist_items(playlist_id, limit=PAGE_SIZE, offset=offset)
        batch = resp.get("items", [])
        if not batch:
            break
        for it in batch:
            # 2026 改版後，這個端點把每首歌的欄位從 `track` 改名成 `item`；
            # 兩個都試，之後 Spotify 再改也比較不容易整包壞掉。
            track = it.get("item") or it.get("track")
            if track and track.get("id"):
                items.append(
                    {
                        "position": pos,
                        "id": track["id"],
                        "uri": track["uri"],
                        "title": track["name"],
                        "artists": tuple(a["name"] for a in track.get("artists", [])),
                    }
                )
            pos += 1
        offset += PAGE_SIZE
        if offset >= resp.get("total", 0):
            break
    return items


def print_exact_duplicates(groups: dict) -> list[dict]:
    """印出完全重複（歌名+歌手皆同）的組合，回傳要移除的項目清單（保留最早那首）。"""
    to_remove: list[dict] = []
    printed_any = False
    for group in groups.values():
        if len(group) <= 1:
            continue
        group_sorted = sorted(group, key=lambda x: x["position"])
        keep, dups = group_sorted[0], group_sorted[1:]
        if not printed_any:
            print("\n=== 🗑 完全重複（歌名+歌手皆相同，將移除多餘的，只留最早加入的）===")
            printed_any = True
        artists = ", ".join(keep["artists"])
        print(f"《{keep['title']}》- {artists}：共 {len(group_sorted)} 份，移除 {len(dups)} 份")
        to_remove.extend(dups)
    if not printed_any:
        print("\n=== 🗑 完全重複：沒有找到 ===")
    return to_remove


def print_feat_and_cover_variants(items: list[dict]) -> None:
    """同歌名但歌手不同：歌手有重疊→視為 feat./合作版本；完全無重疊→視為疑似翻唱。"""
    title_groups: dict[str, dict[frozenset, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for it in items:
        norm_title = normalize_title(it["title"])
        artist_set = frozenset(normalize_artist(a) for a in it["artists"])
        title_groups[norm_title][artist_set].append(it)

    feat_lines = []
    cover_lines = []
    for norm_title, artist_sets in title_groups.items():
        if len(artist_sets) < 2:
            continue
        for (set_a, group_a), (set_b, group_b) in itertools.combinations(artist_sets.items(), 2):
            item_a, item_b = group_a[0], group_b[0]
            label = (
                f"《{item_a['title']}》- {', '.join(item_a['artists'])}  vs  "
                f"《{item_b['title']}》- {', '.join(item_b['artists'])}"
            )
            if set_a & set_b:
                feat_lines.append(label)
            else:
                cover_lines.append(label)

    print("\n=== 🎤 同一首歌但 feat./合作名單不同（歌手名單有重疊，僅列出不會動它）===")
    if feat_lines:
        for line in feat_lines:
            print(line)
    else:
        print("沒有找到")

    print("\n=== 🎙 疑似翻唱（同歌名、歌手完全不同，僅列出不會動它）===")
    if cover_lines:
        for line in cover_lines:
            print(line)
    else:
        print("沒有找到")


def remove_duplicates(sp: spotipy.Spotify, playlist_id: str, first_batch: list[dict]) -> int:
    """實際從歌單移除完全重複的曲目，移除到沒有為止（每次都重新抓取以確保 position 正確）。"""
    total_removed = 0
    pending = first_batch
    while pending:
        batch = pending[:REMOVE_BATCH_SIZE]
        payload_items = [{"uri": it["uri"], "positions": [it["position"]]} for it in batch]
        sp.playlist_remove_specific_occurrences_of_items(playlist_id, payload_items)
        total_removed += len(batch)
        print(f"已移除 {len(batch)} 首（累計 {total_removed} 首）...")
        time.sleep(0.2)

        # 重新抓取，確認是否還有殘留（同一首歌被移除後，其他份的 position 可能已改變）
        items = fetch_playlist_tracks(sp, playlist_id)
        groups = defaultdict(list)
        for it in items:
            key = (normalize_title(it["title"]), frozenset(normalize_artist(a) for a in it["artists"]))
            groups[key].append(it)
        pending = []
        for group in groups.values():
            if len(group) > 1:
                group_sorted = sorted(group, key=lambda x: x["position"])
                pending.extend(group_sorted[1:])
    return total_removed


def main() -> None:
    sp = get_client()
    me = sp.current_user()
    print(f"已登入帳號：{me.get('display_name') or me.get('id')}")

    playlist_id, playlist_name = resolve_playlist_id(sp)
    print(f"正在讀取歌單《{playlist_name}》的曲目...")
    items = fetch_playlist_tracks(sp, playlist_id)
    print(f"共 {len(items)} 首曲目。")

    exact_groups = defaultdict(list)
    for it in items:
        key = (normalize_title(it["title"]), frozenset(normalize_artist(a) for a in it["artists"]))
        exact_groups[key].append(it)

    to_remove = print_exact_duplicates(exact_groups)
    print_feat_and_cover_variants(items)

    if to_remove:
        print(f"\n開始移除完全重複的 {len(to_remove)} 首曲目...")
        removed = remove_duplicates(sp, playlist_id, to_remove)
        print(f"完成，共移除 {removed} 首完全重複的曲目。")
    else:
        print("\n沒有完全重複的曲目需要移除。")


if __name__ == "__main__":
    main()
