from __future__ import annotations

import os
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import ParseResult, urlparse
from zoneinfo import ZoneInfo

from .models import PlaylistTrack

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube"]
DEFAULT_YOUTUBE_REDIRECT_URI = "http://localhost:8766/"
DEFAULT_SEARCH_DELAY_SECONDS = 2.0
DEFAULT_RATE_LIMIT_RETRY_SECONDS = 65.0
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


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
            from google.auth.exceptions import RefreshError
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
                try:
                    credentials.refresh(Request())
                except RefreshError:
                    # Stale refresh token — fall through to interactive re-auth below.
                    credentials = None
            if not credentials or not credentials.valid:
                if client_secrets:
                    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), YOUTUBE_SCOPES)
                else:
                    flow = InstalledAppFlow.from_client_config(_client_config_from_env(), YOUTUBE_SCOPES)
                redirect = _youtube_redirect()
                try:
                    credentials = flow.run_local_server(
                        host=redirect.hostname or "localhost",
                        port=redirect.port or 8766,
                        redirect_uri_trailing_slash=redirect.path.endswith("/"),
                        access_type="offline",
                        prompt="consent select_account",
                    )
                except OSError as exc:
                    port = redirect.port or 8766
                    raise YouTubeError(
                        f"Auth callback port {port} is already in use — another authorization may be in "
                        f"progress. Close the previous browser tab or kill the process holding port {port}, "
                        "then rerun."
                    ) from exc
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(credentials.to_json(), encoding="utf-8")
            token_path.chmod(0o600)

        return cls(build("youtube", "v3", credentials=credentials))

    @classmethod
    def from_client_secrets(cls, client_secrets: Path, token_path: Path) -> "YouTubeClient":
        return cls.from_oauth_config(token_path, client_secrets)

    def create_playlist(self, title: str, description: str = "", privacy_status: str = "private") -> str:
        response = _execute_youtube_request(
            self.service.playlists()
            .insert(
                part="snippet,status",
                body={
                    "snippet": {"title": title, "description": description},
                    "status": {"privacyStatus": privacy_status},
                },
            )
        )
        return str(response["id"])

    def set_playlist_privacy(self, playlist_id: str, privacy_status: str) -> str:
        playlist = self.playlist(playlist_id)
        snippet = playlist.get("snippet") or {}
        response = _execute_youtube_request(
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
        )
        return str((response.get("status") or {}).get("privacyStatus") or privacy_status)

    def playlist(self, playlist_id: str) -> dict:
        response = _execute_youtube_request(self.service.playlists().list(part="snippet,status", id=playlist_id))
        items = response.get("items", [])
        if not items:
            raise YouTubeError(f"YouTube playlist not found: {playlist_id}")
        return items[0]

    def search_video(self, query: str) -> YouTubeVideo | None:
        response = _execute_youtube_request(
            self.service.search().list(part="snippet", q=query, type="video", maxResults=1, videoEmbeddable="true")
        )

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

    def video(self, video_id: str) -> YouTubeVideo:
        video_id = extract_video_id(video_id)
        response = _execute_youtube_request(self.service.videos().list(part="snippet", id=video_id))
        items = response.get("items", [])
        if not items:
            raise YouTubeError(f"YouTube video not found: {video_id}")
        snippet = items[0].get("snippet") or {}
        return YouTubeVideo(
            video_id=video_id,
            title=snippet.get("title", ""),
            channel=snippet.get("channelTitle", ""),
            url=f"https://www.youtube.com/watch?v={video_id}",
        )

    def add_video(self, playlist_id: str, video_id: str) -> None:
        _execute_youtube_request(
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
        )

    def remove_playlist_item(self, playlist_item_id: str) -> None:
        _execute_youtube_request(self.service.playlistItems().delete(id=playlist_item_id))

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
            response = _execute_youtube_request(request)
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
    on_match: Callable[["YouTubeMatch"], None] | None = None,
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
            youtube_match = YouTubeMatch(
                track=track,
                video_id=match.video_id,
                title=match.title,
                channel=match.channel,
                url=match.url,
            )
            if on_match:
                on_match(youtube_match)
            matches.append(youtube_match)
    return matches


def extract_video_id(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.netloc.endswith("youtu.be"):
        return parsed.path.strip("/")
    if parsed.netloc.endswith("youtube.com") or parsed.netloc.endswith("youtube-nocookie.com"):
        if parsed.path == "/watch":
            query = urllib.parse.parse_qs(parsed.query)
            if query.get("v"):
                return query["v"][0]
        for prefix in ("/shorts/", "/embed/"):
            if parsed.path.startswith(prefix):
                return parsed.path.removeprefix(prefix).split("/", 1)[0]
    return value


def extract_playlist_id(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.netloc.endswith("youtube.com") or parsed.netloc.endswith("youtube-nocookie.com"):
        query = urllib.parse.parse_qs(parsed.query)
        if query.get("list"):
            return query["list"][0]
    return value


def playlist_url(playlist_id: str) -> str:
    return f"https://www.youtube.com/playlist?list={extract_playlist_id(playlist_id)}"


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


def _execute_youtube_request(request):
    try:
        return request.execute()
    except Exception as exc:
        quota_reason = _quota_error_reason(exc)
        if quota_reason == "rateLimitExceeded":
            raise YouTubeRateLimitError(
                "YouTube API hit a per-minute rate limit. Wait a minute or increase --youtube-search-delay."
                + _youtube_api_detail(exc)
            ) from exc
        if quota_reason == "quotaExceeded":
            raise YouTubeQuotaExceededError(
                f"YouTube API hit the project quota limit. Daily quota resets {quota_reset_description()}; "
                "or increase quota in Google Cloud."
                + _youtube_api_detail(exc)
            ) from exc
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status == 401 and "youtubeSignupRequired" in _error_details(exc):
            raise YouTubeError(
                "The authenticated Google account has no YouTube channel — create a channel at youtube.com "
                "or re-authenticate with an account that has one "
                "(delete ~/.spotify-playlister/youtube-token.json and rerun)."
                + _youtube_api_detail(exc)
            ) from exc
        raise


def _error_details(exc: Exception) -> str:
    content = getattr(exc, "content", None)
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    if isinstance(content, str):
        return content
    details = str(exc)
    return details


def _youtube_api_detail(exc: Exception) -> str:
    status = getattr(getattr(exc, "resp", None), "status", None)
    details = _error_details(exc)
    if status:
        return f"\n\nYouTube HTTP {status}: {details}"
    return f"\n\nYouTube error: {details}"


def quota_reset_description(now: datetime | None = None) -> str:
    now = now or datetime.now().astimezone()
    reset = next_quota_reset(now)
    return f"in {_human_duration(reset - now)} at {_format_local_time(reset)}"


def next_quota_reset(now: datetime | None = None) -> datetime:
    now = now or datetime.now().astimezone()
    pacific_now = now.astimezone(PACIFIC_TZ)
    reset = pacific_now.replace(hour=0, minute=0, second=0, microsecond=0)
    if pacific_now >= reset:
        reset += timedelta(days=1)
    return reset.astimezone(now.tzinfo)


def _human_duration(delta: timedelta) -> str:
    total_minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}hr {minutes}min"
    if hours:
        return f"{hours}hr"
    return f"{minutes}min"


def _format_local_time(value: datetime) -> str:
    hour = value.strftime("%I").lstrip("0") or "0"
    return f"{hour}:{value.strftime('%M %p')}"


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
