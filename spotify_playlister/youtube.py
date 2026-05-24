from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import ParseResult, urlparse

from .models import PlaylistTrack

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube"]
DEFAULT_YOUTUBE_REDIRECT_URI = "http://localhost:8766/"
DEFAULT_SEARCH_DELAY_SECONDS = 2.0
DEFAULT_RATE_LIMIT_RETRY_SECONDS = 65.0


class YouTubeError(RuntimeError):
    pass


class YouTubeRateLimitError(YouTubeError):
    pass


class YouTubeQuotaExceededError(YouTubeError):
    pass


@dataclass(frozen=True)
class YouTubeVideo:
    video_id: str
    title: str
    channel: str
    url: str


@dataclass(frozen=True)
class YouTubeMatch:
    track: PlaylistTrack
    video_id: str
    title: str
    channel: str
    url: str


@dataclass(frozen=True)
class YouTubePlaylistItem:
    playlist_item_id: str
    video_id: str
    title: str


class YouTubeClient:
    def __init__(self, service: object) -> None:
        self.service = service

    @classmethod
    def from_oauth_config(cls, token_path: Path, client_secrets: Path | None = None) -> "YouTubeClient":
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise YouTubeError("Install YouTube support with: python3 -m pip install -e '.[youtube]'") from exc

        credentials = None
        if token_path.exists():
            credentials = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_SCOPES)
        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                if client_secrets:
                    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), YOUTUBE_SCOPES)
                else:
                    flow = InstalledAppFlow.from_client_config(_client_config_from_env(), YOUTUBE_SCOPES)
                redirect = _youtube_redirect()
                credentials = flow.run_local_server(
                    host=redirect.hostname or "localhost",
                    port=redirect.port or 8766,
                    redirect_uri_trailing_slash=redirect.path.endswith("/"),
                )
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(credentials.to_json(), encoding="utf-8")
            token_path.chmod(0o600)

        return cls(build("youtube", "v3", credentials=credentials))

    @classmethod
    def from_client_secrets(cls, client_secrets: Path, token_path: Path) -> "YouTubeClient":
        return cls.from_oauth_config(token_path, client_secrets)

    def create_playlist(self, title: str, description: str = "", privacy_status: str = "private") -> str:
        response = (
            self.service.playlists()
            .insert(
                part="snippet,status",
                body={
                    "snippet": {"title": title, "description": description},
                    "status": {"privacyStatus": privacy_status},
                },
            )
            .execute()
        )
        return str(response["id"])

    def set_playlist_privacy(self, playlist_id: str, privacy_status: str) -> str:
        playlist = self.playlist(playlist_id)
        snippet = playlist.get("snippet") or {}
        response = (
            self.service.playlists()
            .update(
                part="snippet,status",
                body={
                    "id": playlist_id,
                    "snippet": {
                        "title": snippet.get("title", "Spotify playlist"),
                        "description": snippet.get("description", ""),
                    },
                    "status": {"privacyStatus": privacy_status},
                },
            )
            .execute()
        )
        return str((response.get("status") or {}).get("privacyStatus") or privacy_status)

    def playlist(self, playlist_id: str) -> dict:
        response = self.service.playlists().list(part="snippet,status", id=playlist_id).execute()
        items = response.get("items", [])
        if not items:
            raise YouTubeError(f"YouTube playlist not found: {playlist_id}")
        return items[0]

    def search_video(self, query: str) -> YouTubeVideo | None:
        try:
            response = (
                self.service.search()
                .list(part="snippet", q=query, type="video", maxResults=1, videoEmbeddable="true")
                .execute()
            )
        except Exception as exc:
            quota_reason = _quota_error_reason(exc)
            if quota_reason == "rateLimitExceeded":
                raise YouTubeRateLimitError(
                    "YouTube search hit a per-minute rate limit. Wait a minute or increase --youtube-search-delay."
                ) from exc
            if quota_reason == "quotaExceeded":
                raise YouTubeQuotaExceededError(
                    "YouTube search hit the project quota limit. Wait for the YouTube Data API quota reset or increase quota in Google Cloud."
                ) from exc
            raise

        items = response.get("items", [])
        if not items:
            return None
        item = items[0]
        video_id = item["id"]["videoId"]
        snippet = item["snippet"]
        return YouTubeVideo(
            video_id=video_id,
            title=snippet.get("title", ""),
            channel=snippet.get("channelTitle", ""),
            url=f"https://www.youtube.com/watch?v={video_id}",
        )

    def add_video(self, playlist_id: str, video_id: str) -> None:
        (
            self.service.playlistItems()
            .insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            )
            .execute()
        )

    def playlist_items(self, playlist_id: str) -> list[YouTubePlaylistItem]:
        items: list[YouTubePlaylistItem] = []
        page_token = None
        while True:
            request = self.service.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            )
            response = request.execute()
            for item in response.get("items", []):
                content_details = item.get("contentDetails") or {}
                snippet = item.get("snippet") or {}
                video_id = content_details.get("videoId") or (snippet.get("resourceId") or {}).get("videoId")
                if not video_id:
                    continue
                items.append(
                    YouTubePlaylistItem(
                        playlist_item_id=item.get("id", ""),
                        video_id=video_id,
                        title=snippet.get("title", ""),
                    )
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                return items

    def playlist_video_ids(self, playlist_id: str) -> set[str]:
        return {item.video_id for item in self.playlist_items(playlist_id)}


def match_tracks(
    client: YouTubeClient,
    tracks: Iterable[PlaylistTrack],
    *,
    delay_seconds: float = DEFAULT_SEARCH_DELAY_SECONDS,
    rate_limit_retry_seconds: float = DEFAULT_RATE_LIMIT_RETRY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    on_progress: Callable[[PlaylistTrack], None] | None = None,
) -> list[YouTubeMatch]:
    matches: list[YouTubeMatch] = []
    for index, track in enumerate(tracks):
        if index and delay_seconds > 0:
            sleep(delay_seconds)
        if on_progress:
            on_progress(track)
        try:
            match = client.search_video(track.youtube_query)
        except YouTubeRateLimitError:
            if rate_limit_retry_seconds <= 0:
                raise
            sleep(rate_limit_retry_seconds)
            match = client.search_video(track.youtube_query)
        if match:
            matches.append(
                YouTubeMatch(
                    track=track,
                    video_id=match.video_id,
                    title=match.title,
                    channel=match.channel,
                    url=match.url,
                )
            )
    return matches


def _quota_error_reason(exc: Exception) -> str | None:
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status not in {403, 429}:
        return None
    details = _error_details(exc)
    if "rateLimitExceeded" in details:
        return "rateLimitExceeded"
    if "quotaExceeded" in details:
        return "quotaExceeded"
    if "quota" in details.lower() and status == 403:
        return "quotaExceeded"
    if "quota" in details.lower() and status == 429:
        return "rateLimitExceeded"
    return None


def _error_details(exc: Exception) -> str:
    content = getattr(exc, "content", None)
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    if isinstance(content, str):
        return content
    details = str(exc)
    return details


def _client_config_from_env() -> dict[str, dict[str, object]]:
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise YouTubeError(
            "Provide --youtube-client-secrets or set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET in .env."
        )

    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": os.environ.get("YOUTUBE_AUTH_URI", "https://accounts.google.com/o/oauth2/auth"),
            "token_uri": os.environ.get("YOUTUBE_TOKEN_URI", "https://oauth2.googleapis.com/token"),
            "redirect_uris": [os.environ.get("YOUTUBE_REDIRECT_URI", DEFAULT_YOUTUBE_REDIRECT_URI)],
        }
    }


def _youtube_redirect() -> ParseResult:
    redirect_uri = os.environ.get("YOUTUBE_REDIRECT_URI", DEFAULT_YOUTUBE_REDIRECT_URI)
    redirect = urlparse(redirect_uri)
    if redirect.scheme != "http" or not redirect.hostname:
        raise YouTubeError("YOUTUBE_REDIRECT_URI must be an http localhost URI, for example http://localhost:8766/.")
    if redirect.hostname not in {"localhost", "127.0.0.1"}:
        raise YouTubeError("YOUTUBE_REDIRECT_URI must use localhost or 127.0.0.1.")
    if redirect.path not in {"", "/"}:
        raise YouTubeError("YOUTUBE_REDIRECT_URI must use the root path, for example http://localhost:8766/.")
    return redirect
