"""
把 Spotify「按讚(已收藏)的歌曲」全部加入一個新建立的歌單。

使用方式：
  1. 安裝套件：pip install -r requirements.txt
  2. 複製 .env.example 為 .env，填入你的 Client ID / Secret / Redirect URI
  3. 執行：python liked_to_playlist.py
     第一次執行會自動開啟瀏覽器要求你登入並授權，授權後會在本機快取 token（.cache 檔）。

需要的 Spotify App 設定：
  - 到 https://developer.spotify.com/dashboard 建立 App
  - 在 App 的 "Redirect URIs" 加入跟 .env 裡 SPOTIFY_REDIRECT_URI 完全一致的網址
    （例如 http://127.0.0.1:8888/callback）
"""

import json
import os
import sys
import time

from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException

load_dotenv()

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
PLAYLIST_NAME = os.getenv("PLAYLIST_NAME", "Liked Songs Playlist")
PLAYLIST_DESCRIPTION = os.getenv("PLAYLIST_DESCRIPTION", "由程式自動從按讚歌曲建立")
PLAYLIST_PUBLIC = os.getenv("PLAYLIST_PUBLIC", "false").strip().lower() == "true"

# 需要的權限：讀取按讚歌曲、建立/修改歌單
SCOPE = "user-library-read playlist-modify-private playlist-modify-public"

BATCH_SIZE = 100  # Spotify 加入歌單 API 一次最多接受 100 首
FETCH_LIMIT = 50  # 讀取按讚歌曲一次最多 50 首


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
        cache_path=".cache",
        open_browser=True,
    )
    sp = spotipy.Spotify(auth_manager=auth_manager)
    token_info = auth_manager.get_cached_token()
    if token_info:
        print(f"[debug] 目前 token 的 scope：{token_info.get('scope')}")
    return sp


def fetch_all_liked_track_uris(sp: spotipy.Spotify) -> list[str]:
    """抓出所有按讚歌曲的 URI，並過濾掉已下架/查無資料的曲目。"""
    uris: list[str] = []
    offset = 0
    while True:
        resp = sp.current_user_saved_tracks(limit=FETCH_LIMIT, offset=offset)
        items = resp.get("items", [])
        if not items:
            break
        for item in items:
            track = item.get("track")
            if track and track.get("uri"):
                uris.append(track["uri"])
        offset += FETCH_LIMIT
        total = resp.get("total", 0)
        print(f"已讀取 {min(offset, total)}/{total} 首按讚歌曲...")
        if offset >= total:
            break
        time.sleep(0.05)  # 稍微避免打太快觸發 rate limit
    return uris


def chunked(seq: list[str], size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def main() -> None:
    sp = get_client()

    me = sp.current_user()
    print(f"已登入帳號：{me.get('display_name') or me.get('id')}")

    print("開始抓取按讚的歌曲...")
    uris = fetch_all_liked_track_uris(sp)
    print(f"共找到 {len(uris)} 首按讚歌曲。")

    if not uris:
        print("沒有找到任何按讚歌曲，結束。")
        return

    print(f"建立新歌單：{PLAYLIST_NAME!r} (public={PLAYLIST_PUBLIC})")
    try:
        # 注意：不要用 user_playlist_create()，Spotify 2026-02 的 Web API 遷移
        # 已把 Development Mode App 的 POST /users/{user_id}/playlists 端點下架，
        # 一律回 403。改用打 POST /me/playlists 的 current_user_playlist_create()。
        playlist = sp.current_user_playlist_create(
            name=PLAYLIST_NAME,
            public=PLAYLIST_PUBLIC,
            description=PLAYLIST_DESCRIPTION,
        )
    except SpotifyException as e:
        print("[debug] 建立歌單失敗，詳細資訊：")
        print(f"[debug]   http_status = {e.http_status}")
        print(f"[debug]   code        = {e.code}")
        print(f"[debug]   msg         = {e.msg}")
        print(f"[debug]   reason      = {e.reason}")
        print(f"[debug]   headers     = {e.headers}")
        raise
    playlist_id = playlist["id"]

    print("開始加入歌曲到新歌單...")
    added = 0
    for batch in chunked(uris, BATCH_SIZE):
        try:
            sp.playlist_add_items(playlist_id, batch)
        except SpotifyException as e:
            print(f"加入這批曲目時發生錯誤，略過此批：{e}")
            continue
        added += len(batch)
        print(f"已加入 {added}/{len(uris)} 首...")
        time.sleep(0.1)

    with open("last_playlist.json", "w", encoding="utf-8") as f:
        json.dump(
            {"id": playlist_id, "name": PLAYLIST_NAME, "url": playlist["external_urls"]["spotify"]},
            f, ensure_ascii=False, indent=2,
        )

    print(f"完成！歌單網址：{playlist['external_urls']['spotify']}")


if __name__ == "__main__":
    main()
