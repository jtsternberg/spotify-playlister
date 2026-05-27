from __future__ import annotations

import json
import threading
import urllib.parse
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .spotify import SpotifyClient, extract_playlist_id, playlist_url as spotify_playlist_url
from .sync import (
    MatchStore,
    SyncError,
    remove_spotify_tracks_not_in_youtube,
    remove_youtube_items_not_in_spotify,
    sync_playlist,
    sync_spotify_from_youtube,
)
from .youtube import (
    DEFAULT_RATE_LIMIT_RETRY_SECONDS,
    DEFAULT_SEARCH_DELAY_SECONDS,
    YouTubeClient,
    YouTubeMatch,
    extract_playlist_id as extract_youtube_playlist_id,
    playlist_url as youtube_playlist_url,
)


class WebApp:
    def __init__(self, spotify: SpotifyClient, db_path: Path, youtube_client_secrets: Path | None = None) -> None:
        self.spotify = spotify
        self.db_path = db_path
        self.youtube_client_secrets = youtube_client_secrets
        self._youtube: YouTubeClient | None = None

    def youtube(self) -> YouTubeClient:
        if self._youtube is None:
            self._youtube = YouTubeClient.from_oauth_config(self.spotify.token_path.parent / "youtube-token.json", self.youtube_client_secrets)
        return self._youtube

    def store(self) -> MatchStore:
        return MatchStore(self.db_path)


def serve_web_app(
    spotify: SpotifyClient,
    *,
    db_path: Path,
    youtube_client_secrets: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8877,
    open_browser: bool = True,
) -> None:
    app = WebApp(spotify, db_path, youtube_client_secrets)

    class Handler(SpotifyPlaylisterHandler):
        web_app = app

    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{server.server_port}/"
    print(f"Spotify Playlister web UI: {url}")
    if open_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        server.server_close()


class SpotifyPlaylisterHandler(BaseHTTPRequestHandler):
    web_app: WebApp

    def do_GET(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                self._html(INDEX_HTML)
                return
            if parsed.path == "/api/state":
                self._json(self._state())
                return
            self._not_found()
        except Exception as exc:
            self._error(exc)

    def do_POST(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            payload = self._read_json()
            if parsed.path == "/api/playlists":
                self._json(self._save_playlist(payload))
                return
            if parsed.path == "/api/mappings":
                self._json(self._save_mapping(payload))
                return
            if parsed.path == "/api/sync":
                self._json(self._sync(payload))
                return
            if parsed.path == "/api/sync-remove":
                self._json(self._sync_remove(payload))
                return
            self._not_found()
        except Exception as exc:
            self._error(exc)

    def do_DELETE(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) == 3 and parts[:2] == ["api", "playlists"]:
                with self.web_app.store() as store:
                    deleted = store.delete_playlist(int(parts[2]))
                self._json({"deleted": deleted})
                return
            if len(parts) == 3 and parts[:2] == ["api", "mappings"]:
                key = urllib.parse.unquote(parts[2])
                with self.web_app.store() as store:
                    deleted = store.delete_match(key)
                self._json({"deleted": deleted})
                return
            self._not_found()
        except Exception as exc:
            self._error(exc)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _state(self) -> dict[str, object]:
        with self.web_app.store() as store:
            return {
                "playlists": [_playlist_payload(playlist) for playlist in store.playlists()],
                "mappings": store.matches(),
            }

    def _save_playlist(self, payload: dict[str, Any]) -> dict[str, object]:
        spotify_playlist_id = extract_playlist_id(str(payload.get("spotify_playlist_id") or ""))
        youtube_playlist_id = extract_youtube_playlist_id(str(payload.get("youtube_playlist_id") or ""))
        if not spotify_playlist_id or not youtube_playlist_id:
            raise WebError("Spotify playlist and YouTube playlist are required.")
        spotify_name = str(payload.get("spotify_name") or "")
        youtube_title = str(payload.get("youtube_title") or "")
        if not spotify_name:
            spotify_name = str(self.web_app.spotify.playlist(spotify_playlist_id).get("name") or "")
        if not youtube_title:
            youtube_title = str(((self.web_app.youtube().playlist(youtube_playlist_id).get("snippet") or {}).get("title")) or "")
        with self.web_app.store() as store:
            playlist = store.upsert_playlist(
                spotify_playlist_id=spotify_playlist_id,
                spotify_name=spotify_name,
                youtube_playlist_id=youtube_playlist_id,
                youtube_title=youtube_title,
                notes=str(payload.get("notes") or ""),
            )
        return {"playlist": asdict(playlist), "state": self._state()}

    def _save_mapping(self, payload: dict[str, Any]) -> dict[str, object]:
        spotify_track = str(payload.get("spotify_track") or "").strip()
        youtube_video = str(payload.get("youtube_video") or "").strip()
        if not spotify_track or not youtube_video:
            raise WebError("Spotify track and YouTube video are required.")
        track = self.web_app.spotify.track(spotify_track)
        video = self.web_app.youtube().video(youtube_video)
        with self.web_app.store() as store:
            store.set(YouTubeMatch(track=track, video_id=video.video_id, title=video.title, channel=video.channel, url=video.url))
        return {"mapping": {"spotify_track": track.track_name, "youtube_video_id": video.video_id}, "state": self._state()}

    def _sync(self, payload: dict[str, Any]) -> dict[str, object]:
        playlist_id = int(payload.get("playlist_id") or 0)
        direction = str(payload.get("direction") or "from_spotify")
        dry_run = bool(payload.get("dry_run", True))
        allow_spotify_search = bool(payload.get("allow_spotify_search", False))
        with self.web_app.store() as store:
            saved = store.get_playlist(playlist_id)
        if not saved:
            raise WebError("Saved playlist not found.")

        tracks = None
        summaries: list[dict[str, object]] = []
        if not dry_run and direction in {"from_youtube", "both"}:
            self.web_app.spotify.ensure_playlist_writable(saved.spotify_playlist_id)
        with self.web_app.store() as store:
            if direction in {"from_spotify", "both"}:
                tracks = self.web_app.spotify.playlist_tracks(saved.spotify_playlist_id)
                result = sync_playlist(
                    self.web_app.youtube(),
                    tracks,
                    saved.youtube_playlist_id,
                    store,
                    dry_run=dry_run,
                    delay_seconds=DEFAULT_SEARCH_DELAY_SECONDS,
                    rate_limit_retry_seconds=DEFAULT_RATE_LIMIT_RETRY_SECONDS,
                )
                summaries.append(_youtube_summary(result))
            if direction in {"from_youtube", "both"}:
                result = sync_spotify_from_youtube(
                    self.web_app.spotify,
                    self.web_app.youtube(),
                    saved.spotify_playlist_id,
                    saved.youtube_playlist_id,
                    store,
                    dry_run=dry_run,
                    spotify_tracks=tracks,
                    allow_spotify_search=allow_spotify_search,
                )
                summaries.append(_spotify_summary(result))
            if not dry_run:
                store.mark_playlist_synced(saved.id)
        return {"summaries": summaries, "state": self._state()}

    def _sync_remove(self, payload: dict[str, Any]) -> dict[str, object]:
        playlist_id = int(payload.get("playlist_id") or 0)
        direction = str(payload.get("direction") or "from_spotify")
        dry_run = bool(payload.get("dry_run", True))
        with self.web_app.store() as store:
            saved = store.get_playlist(playlist_id)
        if not saved:
            raise WebError("Saved playlist not found.")
        if direction not in {"from_spotify", "from_youtube"}:
            raise WebError("Remove direction must be from_spotify or from_youtube.")

        summaries: list[dict[str, object]] = []
        if not dry_run and direction == "from_youtube":
            self.web_app.spotify.ensure_playlist_writable(saved.spotify_playlist_id)
        with self.web_app.store() as store:
            if direction == "from_spotify":
                tracks = self.web_app.spotify.playlist_tracks(saved.spotify_playlist_id)
                result = remove_youtube_items_not_in_spotify(
                    self.web_app.youtube(),
                    tracks,
                    saved.youtube_playlist_id,
                    store,
                    dry_run=dry_run,
                )
                summaries.append(_youtube_remove_summary(result))
            if direction == "from_youtube":
                result = remove_spotify_tracks_not_in_youtube(
                    self.web_app.spotify,
                    self.web_app.youtube(),
                    saved.spotify_playlist_id,
                    saved.youtube_playlist_id,
                    store,
                    dry_run=dry_run,
                )
                summaries.append(_spotify_remove_summary(result))
        return {"summaries": summaries, "state": self._state()}

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _not_found(self) -> None:
        self._json({"error": "Not found"}, status=404)

    def _error(self, exc: Exception) -> None:
        status = 400 if isinstance(exc, (SyncError, WebError)) else 500
        self._json({"error": str(exc)}, status=status)


class WebError(RuntimeError):
    pass


def _playlist_payload(playlist) -> dict[str, object]:
    payload = asdict(playlist)
    payload["spotify_url"] = spotify_playlist_url(playlist.spotify_playlist_id)
    payload["youtube_url"] = youtube_playlist_url(playlist.youtube_playlist_id)
    return payload


def _youtube_summary(result) -> dict[str, object]:
    return {
        "direction": "Spotify to YouTube",
        "added": len(result.added),
        "already_present": len(result.already_present),
        "missing": len(result.missing),
        "matched": len(result.matched),
        "items": [
            {
                "spotify": f"{match.track.artists_text} - {match.track.track_name}",
                "youtube": match.title,
                "url": match.url,
            }
            for match in result.added
        ],
    }


def _spotify_summary(result) -> dict[str, object]:
    return {
        "direction": "YouTube to Spotify",
        "added": len(result.added),
        "already_present": len(result.already_present),
        "missing": len(result.missing),
        "matched": len(result.matched),
        "items": [
            {
                "youtube": match.title,
                "spotify": f"{match.track.artists_text} - {match.track.track_name}",
                "url": match.track.spotify_url,
            }
            for match in result.added
        ],
    }


def _youtube_remove_summary(result) -> dict[str, object]:
    return {
        "direction": "Remove from YouTube",
        "removed": len(result.removed),
        "kept": len(result.kept),
        "items": [
            {
                "youtube": item.title,
                "url": f"https://www.youtube.com/watch?v={item.video_id}",
            }
            for item in result.removed
        ],
    }


def _spotify_remove_summary(result) -> dict[str, object]:
    return {
        "direction": "Remove from Spotify",
        "removed": len(result.removed),
        "kept": len(result.kept),
        "items": [
            {
                "spotify": f"{match.track.artists_text} - {match.track.track_name}",
                "url": match.track.spotify_url,
            }
            for match in result.removed
        ],
    }


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Spotify Playlister</title>
  <style>
    :root {
      --ink: #161616;
      --muted: #63635f;
      --paper: #f5f2ea;
      --panel: #fffdf8;
      --line: #d8d0c2;
      --green: #1db954;
      --red: #d0352f;
      --yellow: #e4b84f;
      --blue: #2f6f9f;
      --shadow: 0 18px 50px rgba(39, 33, 23, .12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        linear-gradient(90deg, rgba(0,0,0,.025) 1px, transparent 1px) 0 0 / 32px 32px,
        linear-gradient(rgba(0,0,0,.025) 1px, transparent 1px) 0 0 / 32px 32px,
        var(--paper);
      color: var(--ink);
      font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
      letter-spacing: 0;
    }
    header {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 20px;
      align-items: end;
      padding: 34px clamp(18px, 4vw, 56px) 22px;
      border-bottom: 2px solid var(--ink);
      background: rgba(245, 242, 234, .92);
      position: sticky;
      top: 0;
      z-index: 2;
      backdrop-filter: blur(10px);
    }
    h1 {
      margin: 0;
      font: 800 clamp(34px, 6vw, 76px)/.9 Georgia, "Times New Roman", serif;
      max-width: 760px;
    }
    .status {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 2px solid var(--ink);
      padding: 10px 12px;
      background: var(--green);
      color: #07140b;
      font-weight: 800;
      box-shadow: 5px 5px 0 var(--ink);
    }
    main {
      display: grid;
      grid-template-columns: minmax(300px, 410px) 1fr;
      gap: 22px;
      padding: 24px clamp(18px, 4vw, 56px) 44px;
    }
    section, .panel {
      background: var(--panel);
      border: 2px solid var(--ink);
      box-shadow: var(--shadow);
    }
    .left {
      display: grid;
      gap: 18px;
      align-content: start;
    }
    h2 {
      margin: 0;
      padding: 14px 16px;
      border-bottom: 2px solid var(--ink);
      font-size: 15px;
      text-transform: uppercase;
      background: var(--yellow);
    }
    form { display: grid; gap: 12px; padding: 16px; }
    label { display: grid; gap: 6px; color: var(--muted); font-size: 12px; text-transform: uppercase; font-weight: 800; }
    input, textarea, select {
      width: 100%;
      border: 2px solid var(--ink);
      background: #fff;
      color: var(--ink);
      min-height: 40px;
      padding: 9px 10px;
      font: inherit;
    }
    textarea { min-height: 72px; resize: vertical; }
    button {
      border: 2px solid var(--ink);
      background: var(--ink);
      color: #fff;
      min-height: 40px;
      padding: 9px 12px;
      font: 800 13px ui-monospace, monospace;
      cursor: pointer;
      box-shadow: 4px 4px 0 var(--green);
    }
    button.secondary { background: #fff; color: var(--ink); box-shadow: 4px 4px 0 var(--yellow); }
    button.danger { box-shadow: 4px 4px 0 var(--red); }
    .content { display: grid; gap: 20px; align-content: start; }
    .toolbar {
      display: flex;
      gap: 10px;
      padding: 14px;
      border-bottom: 2px solid var(--ink);
      background: #ebe5d8;
      align-items: center;
      flex-wrap: wrap;
    }
    .toolbar input { max-width: 360px; }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 12px 14px;
      vertical-align: top;
      text-align: left;
      overflow-wrap: anywhere;
    }
    th {
      background: #f0eadf;
      color: #2c2924;
      font-size: 12px;
      text-transform: uppercase;
    }
    tr:hover td { background: #fff8df; }
    .playlist-list { display: grid; gap: 12px; padding: 14px; }
    .playlist {
      border: 2px solid var(--ink);
      padding: 12px;
      background: #fff;
      display: grid;
      gap: 10px;
    }
    .playlist strong { display: block; font-size: 15px; }
    .meta { color: var(--muted); font-size: 12px; }
    .actions {
      display: grid;
      gap: 12px;
    }
    .action-group {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }
    .action-label {
      width: 100%;
      color: var(--muted);
      font-size: 11px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: 0;
    }
    .result {
      white-space: pre-wrap;
      padding: 14px 16px;
      border-top: 2px solid var(--ink);
      background: #111;
      color: #f7f1df;
      min-height: 68px;
    }
    .pill {
      display: inline-block;
      padding: 3px 7px;
      border: 1px solid var(--ink);
      background: #fff;
      font-size: 11px;
      font-weight: 800;
    }
    @media (max-width: 980px) {
      header, main { display: block; }
      .status { margin-top: 18px; }
      .content { margin-top: 22px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Playlist sync desk</h1>
    <div class="status" id="status">Loading</div>
  </header>
  <main>
    <div class="left">
      <section>
        <h2>Save Playlist Pair</h2>
        <form id="playlistForm">
          <label>Spotify playlist<input name="spotify_playlist_id" placeholder="Spotify playlist URL or ID" required></label>
          <label>YouTube playlist<input name="youtube_playlist_id" placeholder="YouTube playlist URL or ID" required></label>
          <label>Notes<textarea name="notes" placeholder="What this pair is for"></textarea></label>
          <button>Save pair</button>
        </form>
      </section>
      <section>
        <h2>Manual Mapping</h2>
        <form id="mappingForm">
          <label>Spotify track<input name="spotify_track" placeholder="Spotify track URL or ID" required></label>
          <label>YouTube video<input name="youtube_video" placeholder="YouTube video URL or ID" required></label>
          <button>Save mapping</button>
        </form>
      </section>
    </div>
    <div class="content">
      <section>
        <h2>Playlists</h2>
        <div id="playlists" class="playlist-list"></div>
        <div class="result" id="result">Ready.</div>
      </section>
      <section>
        <h2>Mappings</h2>
        <div class="toolbar">
          <input id="filter" placeholder="Filter mappings">
          <label style="display:flex;grid:auto;gap:8px;align-items:center;text-transform:none;color:var(--ink)">
            <input id="allowSearch" type="checkbox" style="width:auto;min-height:0"> search uncached Spotify
          </label>
          <button class="secondary" id="refresh" type="button">Refresh</button>
        </div>
        <table>
          <thead><tr><th style="width:34%">Spotify</th><th style="width:42%">YouTube</th><th style="width:14%">Updated</th><th style="width:10%">Action</th></tr></thead>
          <tbody id="mappings"></tbody>
        </table>
      </section>
    </div>
  </main>
  <script>
    let state = { playlists: [], mappings: [] };
    const $ = (id) => document.getElementById(id);
    const api = async (path, options = {}) => {
      const response = await fetch(path, {
        ...options,
        headers: { "Content-Type": "application/json", ...(options.headers || {}) }
      });
      const payload = await response.json();
      if (!response.ok || payload.error) throw new Error(payload.error || response.statusText);
      return payload;
    };
    const setStatus = (text) => $("status").textContent = text;
    const setResult = (value) => $("result").textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    async function load() {
      setStatus("Loading");
      state = await api("/api/state");
      render();
      setStatus("Ready");
    }
    function render() {
      $("playlists").innerHTML = state.playlists.map(p => `
        <div class="playlist">
          <div>
            <strong>${spotifyPlaylistLink(p)}</strong>
            <div class="meta">Spotify ${spotifyPlaylistIdLink(p)}<br>YouTube ${youtubePlaylistLink(p)}</div>
          </div>
          <div class="meta">${p.last_synced_at ? "Last synced " + new Date(p.last_synced_at * 1000).toLocaleString() : "Never synced"} ${p.notes ? " - " + escapeHtml(p.notes) : ""}</div>
          <div class="actions">
            <div class="action-group">
              <div class="action-label">Add missing tracks</div>
              <button type="button" title="Show Spotify tracks that would be added to YouTube." onclick="sync(${p.id}, 'from_spotify', true)">Preview YouTube adds</button>
              <button type="button" title="Show YouTube videos that would be added back to Spotify." onclick="sync(${p.id}, 'from_youtube', true)">Preview Spotify adds</button>
              <button type="button" title="Preview adds in both directions." onclick="sync(${p.id}, 'both', true)">Preview both</button>
              <button class="secondary" type="button" title="Add missing tracks in both directions." onclick="sync(${p.id}, 'both', false)">Add missing both ways</button>
            </div>
            <div class="action-group">
              <div class="action-label">Remove stale tracks</div>
              <button type="button" title="Show YouTube playlist items that are not mapped from the current Spotify playlist." onclick="removeSync(${p.id}, 'from_spotify', true)">Preview YouTube removals</button>
              <button class="danger" type="button" title="Remove YouTube playlist items that are not mapped from the current Spotify playlist." onclick="removeSync(${p.id}, 'from_spotify', false)">Remove from YouTube</button>
              <button type="button" title="Show Spotify tracks that are not mapped from the current YouTube playlist." onclick="removeSync(${p.id}, 'from_youtube', true)">Preview Spotify removals</button>
              <button class="danger" type="button" title="Remove Spotify tracks that are not mapped from the current YouTube playlist." onclick="removeSync(${p.id}, 'from_youtube', false)">Remove from Spotify</button>
            </div>
            <button class="danger" type="button" title="Remove this saved playlist pair from the local database. This does not delete either real playlist." onclick="deletePlaylist(${p.id})">Forget pair</button>
          </div>
        </div>`).join("") || `<div class="meta" style="padding:16px">No playlist pairs saved yet.</div>`;
      const needle = $("filter").value.trim().toLowerCase();
      const rows = state.mappings.filter(m => !needle || `${m.query} ${m.youtube_title} ${m.youtube_channel}`.toLowerCase().includes(needle));
      $("mappings").innerHTML = rows.map(m => `
        <tr>
          <td><span class="pill">${escapeHtml(m.spotify_track_id || "query")}</span><br>${spotifyLink(m)}</td>
          <td><a href="${escapeHtml(m.youtube_url)}" target="_blank">${escapeHtml(m.youtube_title || m.youtube_video_id)}</a><br><span class="meta">${escapeHtml(m.youtube_channel || "")}</span></td>
          <td>${m.updated_at ? new Date(m.updated_at * 1000).toLocaleString() : ""}</td>
          <td><button class="danger" type="button" onclick="deleteMapping('${encodeURIComponent(m.track_key)}')">Delete</button></td>
        </tr>`).join("") || `<tr><td colspan="4" class="meta">No mappings found.</td></tr>`;
    }
    async function submitForm(form, path) {
      setStatus("Working");
      const payload = Object.fromEntries(new FormData(form).entries());
      const result = await api(path, { method: "POST", body: JSON.stringify(payload) });
      state = result.state || await api("/api/state");
      form.reset();
      render();
      setResult(result);
      setStatus("Ready");
    }
    async function sync(id, direction, dryRun) {
      setStatus("Syncing");
      const result = await api("/api/sync", { method: "POST", body: JSON.stringify({ playlist_id: id, direction, dry_run: dryRun, allow_spotify_search: $("allowSearch").checked }) });
      state = result.state;
      render();
      setResult(result.summaries);
      setStatus("Ready");
    }
    async function removeSync(id, direction, dryRun) {
      const target = direction === "from_spotify" ? "YouTube" : "Spotify";
      if (!dryRun && !confirm(`Remove stale tracks from ${target}? Run preview first if you have not reviewed the list.`)) return;
      setStatus(dryRun ? "Checking removals" : "Removing");
      const result = await api("/api/sync-remove", { method: "POST", body: JSON.stringify({ playlist_id: id, direction, dry_run: dryRun }) });
      state = result.state;
      render();
      setResult(result.summaries);
      setStatus("Ready");
    }
    async function deletePlaylist(id) {
      if (!confirm("Delete this saved pair?")) return;
      await api(`/api/playlists/${id}`, { method: "DELETE" });
      await load();
    }
    async function deleteMapping(key) {
      if (!confirm("Delete this mapping?")) return;
      await api(`/api/mappings/${key}`, { method: "DELETE" });
      await load();
    }
    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
    }
    function spotifyLink(mapping) {
      const label = escapeHtml(mapping.query || mapping.spotify_track_id || "");
      if (!mapping.spotify_track_id) return label;
      return `<a href="https://open.spotify.com/track/${encodeURIComponent(mapping.spotify_track_id)}" target="_blank">${label}</a>`;
    }
    function spotifyPlaylistLink(playlist) {
      return `<a href="${escapeHtml(playlist.spotify_url || spotifyPlaylistUrl(playlist.spotify_playlist_id))}" target="_blank">${escapeHtml(playlist.spotify_name || playlist.spotify_playlist_id)}</a>`;
    }
    function spotifyPlaylistIdLink(playlist) {
      return `<a href="${escapeHtml(playlist.spotify_url || spotifyPlaylistUrl(playlist.spotify_playlist_id))}" target="_blank">${escapeHtml(playlist.spotify_playlist_id)}</a>`;
    }
    function youtubePlaylistLink(playlist) {
      return `<a href="${escapeHtml(playlist.youtube_url || youtubePlaylistUrl(playlist.youtube_playlist_id))}" target="_blank">${escapeHtml(playlist.youtube_title || playlist.youtube_playlist_id)}</a>`;
    }
    function spotifyPlaylistUrl(id) {
      return `https://open.spotify.com/playlist/${encodeURIComponent(id)}`;
    }
    function youtubePlaylistUrl(id) {
      return `https://www.youtube.com/playlist?list=${encodeURIComponent(id)}`;
    }
    window.sync = sync;
    window.removeSync = removeSync;
    window.deletePlaylist = deletePlaylist;
    window.deleteMapping = deleteMapping;
    $("playlistForm").addEventListener("submit", event => { event.preventDefault(); submitForm(event.target, "/api/playlists").catch(showError); });
    $("mappingForm").addEventListener("submit", event => { event.preventDefault(); submitForm(event.target, "/api/mappings").catch(showError); });
    $("filter").addEventListener("input", render);
    $("refresh").addEventListener("click", () => load().catch(showError));
    function showError(error) { setStatus("Error"); setResult(error.message); }
    load().catch(showError);
  </script>
</body>
</html>"""
