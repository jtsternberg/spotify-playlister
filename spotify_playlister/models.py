from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlaylistTrack:
    position: int
    added_at: str
    is_local: bool
    track_id: str
    track_name: str
    artists: tuple[str, ...]
    album_name: str
    duration_ms: int
    explicit: bool
    release_date: str
    isrc: str
    spotify_url: str

    @property
    def artists_text(self) -> str:
        return "; ".join(self.artists)

    @property
    def duration(self) -> str:
        seconds = round(self.duration_ms / 1000)
        minutes, seconds = divmod(seconds, 60)
        return f"{minutes}:{seconds:02d}"

    @property
    def youtube_query(self) -> str:
        parts = [self.track_name, self.artists_text]
        if self.album_name:
            parts.append(self.album_name)
        return " ".join(part for part in parts if part)
