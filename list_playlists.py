"""列出目前帳號所有歌單的名稱、id、曲目數量，用來排查同名歌單的問題。"""

import os

from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

SCOPE = "playlist-read-private"

auth_manager = SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE,
    cache_path=".cache-dedupe",
    open_browser=True,
)
sp = spotipy.Spotify(auth_manager=auth_manager)

me = sp.current_user()
print(f"帳號：{me.get('display_name') or me.get('id')}\n")

SEARCH_KEYWORD = "mix"

offset = 0
all_playlists = []
while True:
    resp = sp.current_user_playlists(limit=50, offset=offset)
    items = resp.get("items", [])
    if not items:
        break
    all_playlists.extend(items)
    offset += 50
    if offset >= resp.get("total", 0):
        break

print(f"共 {len(all_playlists)} 個歌單：\n")
for pl in all_playlists:
    if not pl:
        print("[跳過] 這一項是 None（可能是已失效/資料夾項目）")
        continue
    owner = pl.get("owner", {}).get("id", "?")
    owner_name = pl.get("owner", {}).get("display_name", owner)
    mine = "自己的" if owner == me["id"] else f"別人的({owner_name})"
    total = pl.get("tracks", pl.get("items", {})).get("total", "?")
    print(f"[{mine}] {pl['name']!r}  id={pl['id']}  曲目數={total}  public={pl['public']}")

print(f"\n=== 名稱包含 {SEARCH_KEYWORD!r}（不分大小寫）的歌單 ===")
matches = [pl for pl in all_playlists if pl and SEARCH_KEYWORD.lower() in pl["name"].lower()]
if not matches:
    print("沒有找到，麻煩把正確歌單名稱或分享連結貼給我。")
else:
    for pl in matches:
        owner = pl.get("owner", {}).get("id", "?")
        owner_name = pl.get("owner", {}).get("display_name", owner)
        mine = "自己的" if owner == me["id"] else f"別人的({owner_name})，可能無法用 API 移除曲目"
        total = pl.get("tracks", pl.get("items", {})).get("total", "?")
        print(f"[{mine}] {pl['name']!r}  id={pl['id']}  曲目數={total}  public={pl['public']}")
