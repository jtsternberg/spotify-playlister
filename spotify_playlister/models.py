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
        # Album name is deliberately excluded: it poisons search whenever the
        # album shares a title with another track (e.g. "Cages" on the album
        # "Holy Water" → returns Holy Water), is self-titled (doubles the
        # artist), or carries cruft like "(Deluxe)". Artist + track is the
        # reliable form.
        parts = [self.track_name, self.artists_text]
        return " ".join(part for part in parts if part)
