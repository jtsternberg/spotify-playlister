from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from .models import PlaylistTrack

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"
DEFAULT_SCOPES = "playlist-read-private playlist-read-collaborative playlist-modify-private playlist-modify-public"


class SpotifyError(RuntimeError):
    pass


class SpotifyApiError(SpotifyError):
    def __init__(self, status: int, details: str) -> None:
        super().__init__(f"Spotify API returned HTTP {status}: {details}")
        self.status = status
        self.details = details


def cache_dir() -> Path:
    root = os.environ.get("SPOTIFY_PLAYLISTER_CACHE")
    if root:
        return Path(root).expanduser()
    return Path.home() / ".spotify-playlister"


def extract_playlist_id(value: str) -> str:
    value = value.strip()
    if value.startswith("spotify:playlist:"):
        return value.rsplit(":", 1)[-1]
    if "open.spotify.com/playlist/" in value:
        parsed = urllib.parse.urlparse(value)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "playlist":
            return parts[1]
    return value


def playlist_url(playlist_id: str) -> str:
    return f"https://open.spotify.com/playlist/{extract_playlist_id(playlist_id)}"


def extract_track_id(value: str) -> str:
    value = value.strip()
    if value.startswith("spotify:track:"):
        return value.rsplit(":", 1)[-1]
    if "open.spotify.com/track/" in value:
        parsed = urllib.parse.urlparse(value)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "track":
            return parts[1]
    return value


def _urlsafe_b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _json_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
    json_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None
    request_headers = dict(headers or {})
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    if json_data is not None:
        body = json.dumps(json_data).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, method=method, headers=request_headers, data=body)
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise SpotifyApiError(exc.code, details) from exc


class _CallbackHandler(BaseHTTPRequestHandler):
    server: "_OAuthServer"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        returned_state = params.get("state", [None])[0]
        if returned_state != self.server.expected_state:
            self.server.auth_error = "OAuth state mismatch"
        else:
            self.server.auth_code = params.get("code", [None])[0]
            self.server.auth_error = params.get("error", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Spotify authorization complete. You can close this tab.")

    def log_message(self, format: str, *args: object) -> None:
        return


class _OAuthServer(HTTPServer):
    auth_code: str | None = None
    auth_error: str | None = None
    expected_state: str | None = None


class SpotifyClient:
    def __init__(
        self,
        client_id: str,
        redirect_uri: str = DEFAULT_REDIRECT_URI,
        scopes: str = DEFAULT_SCOPES,
        token_path: Path | None = None,
    ) -> None:
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self.token_path = token_path or cache_dir() / "spotify-token.json"
        self._token: dict[str, Any] | None = None

    @classmethod
    def from_env(cls) -> "SpotifyClient":
        client_id = os.environ.get("SPOTIFY_CLIENT_ID")
        if not client_id:
            raise SpotifyError("Set SPOTIFY_CLIENT_ID to your Spotify app client ID.")
        return cls(
            client_id=client_id,
            redirect_uri=os.environ.get("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI),
        )

    def list_playlists(self) -> list[dict[str, Any]]:
        playlists: list[dict[str, Any]] = []
        url = f"{API_BASE}/me/playlists?limit=50"
        while url:
            payload = self.get(url)
            playlists.extend(payload.get("items", []))
            url = payload.get("next")
        return playlists

    def playlist(self, playlist_id: str) -> dict[str, Any]:
        return self.get(f"{API_BASE}/playlists/{playlist_id}?fields=id,name,description,collaborative,external_urls,owner(display_name,id)")

    def playlist_tracks(self, playlist_id: str) -> list[PlaylistTrack]:
        tracks: list[PlaylistTrack] = []
        offset = 0
        url = f"{API_BASE}/playlists/{playlist_id}/items?limit=100&offset={offset}"
        while url:
            payload = self.get(url)
            for item in payload.get("items", []):
                track = parse_playlist_item(item, len(tracks) + 1)
                if track:
                    tracks.append(track)
            url = payload.get("next")
        return tracks

    def search_tracks(self, query: str, limit: int = 1) -> list[PlaylistTrack]:
        params = urllib.parse.urlencode({"q": query, "type": "track", "limit": limit})
        payload = self.get(f"{API_BASE}/search?{params}")
        items = ((payload.get("tracks") or {}).get("items") or [])
        tracks: list[PlaylistTrack] = []
        for item in items:
            track = parse_spotify_track(item, len(tracks) + 1)
            if track:
                tracks.append(track)
        return tracks

    def track(self, track_id: str) -> PlaylistTrack:
        payload = self.get(f"{API_BASE}/tracks/{extract_track_id(track_id)}")
        track = parse_spotify_track(payload, 1)
        if not track:
            raise SpotifyError(f"Spotify track not found: {track_id}")
        return track

    def current_user_id(self) -> str:
        payload = self.get(f"{API_BASE}/me")
        return str(payload.get("id") or "")

    def ensure_playlist_writable(self, playlist_id: str) -> None:
        playlist = self.playlist(playlist_id)
        owner = playlist.get("owner") or {}
        owner_id = str(owner.get("id") or "")
        owner_name = str(owner.get("display_name") or owner_id or "unknown")
        current_user_id = self.current_user_id()
        if owner_id == current_user_id or playlist.get("collaborative"):
            return
        name = playlist.get("name") or playlist_id
        raise SpotifyError(
            f"Spotify playlist '{name}' is owned by {owner_name}, so this account cannot add tracks to it. "
            "Use a Spotify playlist owned by this account, make the playlist collaborative, or run without --from-youtube/--both."
        )

    def create_playlist(self, name: str, public: bool = False, description: str = "") -> str:
        user_id = self.current_user_id()
        headers = {"Authorization": f"Bearer {self.access_token()}"}
        try:
            payload = _json_request(
                f"{API_BASE}/users/{user_id}/playlists",
                method="POST",
                headers=headers,
                json_data={"name": name, "public": public, "description": description},
            )
        except SpotifyApiError as exc:
            if exc.status == 403:
                raise SpotifyError(
                    "Spotify refused to create the playlist. Confirm authorization includes "
                    "playlist-modify-private/playlist-modify-public; if you just changed scopes, "
                    f"delete {self.token_path} and rerun."
                ) from exc
            raise
        playlist_id = str(payload.get("id") or "")
        if not playlist_id:
            raise SpotifyError("Spotify did not return an id for the new playlist.")
        return playlist_id

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        headers = {"Authorization": f"Bearer {self.access_token()}"}
        for index in range(0, len(track_ids), 100):
            chunk = [track_id for track_id in track_ids[index : index + 100] if track_id]
            if not chunk:
                continue
            try:
                _json_request(
                    f"{API_BASE}/playlists/{playlist_id}/items",
                    method="POST",
                    headers=headers,
                    json_data={"uris": [f"spotify:track:{track_id}" for track_id in chunk]},
                )
            except SpotifyApiError as exc:
                if exc.status == 403:
                    raise SpotifyError(
                        f"Spotify refused to add tracks to playlist {playlist_id}. "
                        "Confirm this Spotify account can modify the playlist and that authorization includes "
                        "playlist-modify-private/playlist-modify-public. If you just changed scopes, delete "
                        f"{self.token_path} and rerun."
                    ) from exc
                raise

    def remove_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        headers = {"Authorization": f"Bearer {self.access_token()}"}
        for index in range(0, len(track_ids), 100):
            chunk = [track_id for track_id in track_ids[index : index + 100] if track_id]
            if not chunk:
                continue
            _json_request(
                f"{API_BASE}/playlists/{playlist_id}/tracks",
                method="DELETE",
                headers=headers,
                json_data={"tracks": [{"uri": f"spotify:track:{track_id}"} for track_id in chunk]},
            )

    def get(self, url: str) -> dict[str, Any]:
        return _json_request(url, headers={"Authorization": f"Bearer {self.access_token()}"})

    def access_token(self) -> str:
        token = self._load_token()
        if token and not self._has_required_scopes(token):
            self._token = self._authorize()
            self._save_token(self._token)
            return str(self._token["access_token"])
        if token and token.get("expires_at", 0) > time.time() + 60:
            return str(token["access_token"])
        if token and token.get("refresh_token"):
            self._token = self._refresh_token(str(token["refresh_token"]))
            self._save_token(self._token)
            return str(self._token["access_token"])
        self._token = self._authorize()
        self._save_token(self._token)
        return str(self._token["access_token"])

    def _has_required_scopes(self, token: dict[str, Any]) -> bool:
        granted = set(str(token.get("scope", "")).split())
        required = set(self.scopes.split())
        return required.issubset(granted)

    def _load_token(self) -> dict[str, Any] | None:
        if self._token:
            return self._token
        if not self.token_path.exists():
            return None
        self._token = json.loads(self.token_path.read_text(encoding="utf-8"))
        return self._token

    def _save_token(self, token: dict[str, Any]) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(token, indent=2), encoding="utf-8")
        self.token_path.chmod(0o600)

    def _authorize(self) -> dict[str, Any]:
        verifier = _urlsafe_b64(secrets.token_bytes(64))
        challenge = _urlsafe_b64(hashlib.sha256(verifier.encode("ascii")).digest())
        state = secrets.token_urlsafe(24)
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "scope": self.scopes,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
        parsed_redirect = urllib.parse.urlparse(self.redirect_uri)
        server = _OAuthServer((parsed_redirect.hostname or "127.0.0.1", parsed_redirect.port or 8765), _CallbackHandler)
        server.expected_state = state
        try:
            print(f"Opening Spotify authorization URL:\n{auth_url}")
            webbrowser.open(auth_url)
            while not server.auth_code and not server.auth_error:
                server.handle_request()
        finally:
            server.server_close()
        if server.auth_error:
            raise SpotifyError(f"Spotify authorization failed: {server.auth_error}")
        token = _json_request(
            TOKEN_URL,
            method="POST",
            data={
                "client_id": self.client_id,
                "grant_type": "authorization_code",
                "code": server.auth_code or "",
                "redirect_uri": self.redirect_uri,
                "code_verifier": verifier,
            },
        )
        return _with_expiry(token)

    def _refresh_token(self, refresh_token: str) -> dict[str, Any]:
        current = self._load_token() or {}
        token = _json_request(
            TOKEN_URL,
            method="POST",
            data={
                "client_id": self.client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        if "refresh_token" not in token:
            token["refresh_token"] = refresh_token
        if "scope" not in token and "scope" in current:
            token["scope"] = current["scope"]
        return _with_expiry(token)


def _with_expiry(token: dict[str, Any]) -> dict[str, Any]:
    token["expires_at"] = time.time() + int(token.get("expires_in", 3600))
    return token


def parse_playlist_item(item: dict[str, Any], position: int) -> PlaylistTrack | None:
    track = item.get("track") or item.get("item")
    if not track or track.get("type") != "track":
        return None

    return parse_spotify_track(
        track,
        position,
        added_at=item.get("added_at") or "",
        is_local=bool(item.get("is_local") or track.get("is_local")),
    )


def parse_spotify_track(track: dict[str, Any], position: int, added_at: str = "", is_local: bool | None = None) -> PlaylistTrack | None:
    if not track or track.get("type") != "track":
        return None

    album = track.get("album") or {}
    artists = tuple(artist.get("name", "") for artist in track.get("artists", []) if artist.get("name"))
    external_urls = track.get("external_urls") or {}
    external_ids = track.get("external_ids") or {}
    return PlaylistTrack(
        position=position,
        added_at=added_at,
        is_local=bool(track.get("is_local")) if is_local is None else is_local,
        track_id=track.get("id") or "",
        track_name=track.get("name") or "",
        artists=artists,
        album_name=album.get("name") or "",
        duration_ms=int(track.get("duration_ms") or 0),
        explicit=bool(track.get("explicit")),
        release_date=album.get("release_date") or "",
        isrc=external_ids.get("isrc") or "",
        spotify_url=external_urls.get("spotify") or "",
    )
