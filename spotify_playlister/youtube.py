from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import PlaylistTrack

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube"]


class YouTubeError(RuntimeError):
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


class YouTubeClient:
    def __init__(self, service: object) -> None:
        self.service = service

    @classmethod
    def from_client_secrets(cls, client_secrets: Path, token_path: Path) -> "YouTubeClient":
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
                flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), YOUTUBE_SCOPES)
                credentials = flow.run_local_server(port=0)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(credentials.to_json(), encoding="utf-8")
            token_path.chmod(0o600)

        return cls(build("youtube", "v3", credentials=credentials))

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

    def search_video(self, query: str) -> YouTubeVideo | None:
        response = (
            self.service.search()
            .list(part="snippet", q=query, type="video", maxResults=1, videoEmbeddable="true")
            .execute()
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


def match_tracks(client: YouTubeClient, tracks: Iterable[PlaylistTrack]) -> list[YouTubeMatch]:
    matches: list[YouTubeMatch] = []
    for track in tracks:
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
