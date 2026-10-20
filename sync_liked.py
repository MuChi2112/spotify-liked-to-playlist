"""
把「按讚的歌曲」裡新增、但還不在目標歌單（預設：mix）裡的歌加進去，適合用 cron 定期執行。

  - 只會新增，不會移除歌單裡任何歌曲（取消按讚的歌也會留著）。
  - 比對方式跟 dedupe_playlist.py 一樣用「正規化歌名 + 歌手」，
    避免同一首歌的另一個版本被去重後又被加回來。
  - 新歌會插在歌單最上面，維持「越新按讚越上面」的順序。

使用方式：
  python sync_liked.py

第一次執行會開瀏覽器授權，token 快取在 .cache-sync。
要在沒有瀏覽器的伺服器上跑，先在本機授權一次，再把 .cache-sync 複製過去即可，
之後 spotipy 會自動用 refresh token 續期。

需要環境變數（沿用 .env）：
  SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET / SPOTIFY_REDIRECT_URI
  PLAYLIST_NAME（找不到 last_playlist.json 時，用這個名稱去你的歌單列表裡找）
"""

import os
import sys
import time
from datetime import datetime

from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException

from dedupe_playlist import (
    fetch_playlist_tracks,
    normalize_artist,
    normalize_title,
    resolve_playlist_id,
)

# cron 執行時的工作目錄不是這個資料夾，先切過來才找得到 .env / .cache-sync / last_playlist.json
os.chdir(os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

# 讀按讚歌曲 + 讀歌單內容（含私人歌單）+ 修改歌單
SCOPE = "user-library-read playlist-read-private playlist-modify-private playlist-modify-public"

BATCH_SIZE = 100  # Spotify 加入歌單 API 一次最多接受 100 首
FETCH_LIMIT = 50  # 讀取按讚歌曲一次最多 50 首


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


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
        cache_path=".cache-sync",
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth_manager)


def song_key(title: str, artists) -> tuple:
    return normalize_title(title), frozenset(normalize_artist(a) for a in artists)


def fetch_liked_tracks(sp: spotipy.Spotify) -> list[dict]:
    """回傳所有按讚歌曲（新按讚的在前），過濾掉已下架/查無資料的曲目。"""
    tracks: list[dict] = []
    offset = 0
    while True:
        resp = sp.current_user_saved_tracks(limit=FETCH_LIMIT, offset=offset)
        items = resp.get("items", [])
        if not items:
            break
        for item in items:
            track = item.get("track")
            if track and track.get("uri"):
                tracks.append(
                    {
                        "uri": track["uri"],
                        "title": track["name"],
                        "artists": [a["name"] for a in track.get("artists", [])],
                    }
                )
        offset += FETCH_LIMIT
        if offset >= resp.get("total", 0):
            break
        time.sleep(0.05)  # 稍微避免打太快觸發 rate limit
    return tracks


def main() -> None:
    sp = get_client()

    playlist_id, playlist_name = resolve_playlist_id(sp)
    existing = fetch_playlist_tracks(sp, playlist_id)
    existing_uris = {it["uri"] for it in existing}
    existing_keys = {song_key(it["title"], it["artists"]) for it in existing}

    liked = fetch_liked_tracks(sp)
    log(f"按讚歌曲 {len(liked)} 首，歌單《{playlist_name}》目前 {len(existing)} 首。")

    new_uris: list[str] = []
    for track in liked:
        key = song_key(track["title"], track["artists"])
        if track["uri"] in existing_uris or key in existing_keys:
            continue
        new_uris.append(track["uri"])
        existing_keys.add(key)  # 同一次執行裡的重複版本也只加一次

    if not new_uris:
        log("沒有新的按讚歌曲需要加入。")
        return

    log(f"有 {len(new_uris)} 首新歌要加入。")
    # new_uris 是新→舊；從最後一批開始依序插到最上面，最終順序才會維持新→舊。
    batches = [new_uris[i : i + BATCH_SIZE] for i in range(0, len(new_uris), BATCH_SIZE)]
    added = 0
    for batch in reversed(batches):
        try:
            sp.playlist_add_items(playlist_id, batch, position=0)
        except SpotifyException as e:
            log(f"加入這批曲目時發生錯誤，略過此批：{e}")
            continue
        added += len(batch)
        time.sleep(0.1)
    log(f"完成，共加入 {added} 首。")


if __name__ == "__main__":
    main()
