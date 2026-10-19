# Spotify Liked Songs → Playlist

把 Spotify「按讚的歌曲」全部複製到一個新建立的歌單，並附帶整理歌單的小工具。

| 腳本 | 用途 |
| --- | --- |
| `liked_to_playlist.py` | 讀取所有按讚歌曲，建立新歌單並全部加入，歌單資訊存到 `last_playlist.json` |
| `dedupe_playlist.py` | 移除歌單中完全重複的曲目；feat. 版本不同或疑似翻唱的只列出不刪除 |
| `list_playlists.py` | 列出帳號所有歌單（名稱、id、曲目數），方便排查同名歌單 |

## 使用方式

1. 到 [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) 建立 App，
   在 Redirect URIs 加入 `http://127.0.0.1:8888/callback`（需與 `.env` 一致）
2. 安裝套件：`pip install -r requirements.txt`
3. 複製 `.env.example` 為 `.env`，填入 Client ID / Secret
4. 執行：`python liked_to_playlist.py`

第一次執行會開啟瀏覽器要求授權，token 會快取在本機 `.cache`（已被 `.gitignore` 排除）。
