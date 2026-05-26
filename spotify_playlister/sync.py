from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .models import PlaylistTrack
from .spotify import SpotifyClient
from .youtube import (
    DEFAULT_RATE_LIMIT_RETRY_SECONDS,
    DEFAULT_SEARCH_DELAY_SECONDS,
    YouTubeClient,
    YouTubeMatch,
    YouTubePlaylistItem,
    YouTubeRateLimitError,
)


@dataclass(frozen=True)
class SyncResult:
    matched: list[YouTubeMatch]
    added: list[YouTubeMatch]
    already_present: list[YouTubeMatch]
    missing: list[PlaylistTrack]
    dry_run: bool


@dataclass(frozen=True)
class SpotifySyncResult:
    matched: list[YouTubeMatch]
    added: list[YouTubeMatch]
    already_present: list[YouTubeMatch]
    missing: list[YouTubePlaylistItem]
    dry_run: bool


class SyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class SyncRemoveResult:
    direction: str
    removed: list[YouTubeMatch | YouTubePlaylistItem]
    kept: list[YouTubeMatch | YouTubePlaylistItem]
    unknown: list[YouTubePlaylistItem]
    dry_run: bool


@dataclass(frozen=True)
class SyncedPlaylist:
    id: int
    spotify_playlist_id: str
    spotify_name: str
    youtube_playlist_id: str
    youtube_title: str
    notes: str
    last_synced_at: int | None
    created_at: int
    updated_at: int


class MatchStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "MatchStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def matches(self) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT track_key, spotify_track_id, isrc, query, youtube_video_id,
                   youtube_title, youtube_channel, youtube_url, updated_at
            FROM track_matches
            ORDER BY updated_at DESC, query COLLATE NOCASE
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def get(self, track: PlaylistTrack) -> YouTubeMatch | None:
        row = self.connection.execute(
            """
            SELECT youtube_video_id, youtube_title, youtube_channel, youtube_url
            FROM track_matches
            WHERE track_key = ?
            """,
            (track_key(track),),
        ).fetchone()
        if not row:
            return None
        return YouTubeMatch(
            track=track,
            video_id=row["youtube_video_id"],
            title=row["youtube_title"],
            channel=row["youtube_channel"],
            url=row["youtube_url"],
        )

    def get_by_youtube_video_id(self, video_id: str) -> YouTubeMatch | None:
        row = self.connection.execute(
            """
            SELECT spotify_track_id, isrc, query, youtube_video_id, youtube_title, youtube_channel, youtube_url
            FROM track_matches
            WHERE youtube_video_id = ?
            """,
            (video_id,),
        ).fetchone()
        if not row:
            return None
        track = PlaylistTrack(
            position=0,
            added_at="",
            is_local=False,
            track_id=row["spotify_track_id"] or "",
            track_name=row["query"] or "",
            artists=(),
            album_name="",
            duration_ms=0,
            explicit=False,
            release_date="",
            isrc=row["isrc"] or "",
            spotify_url=f"https://open.spotify.com/track/{row['spotify_track_id']}" if row["spotify_track_id"] else "",
        )
        return YouTubeMatch(
            track=track,
            video_id=row["youtube_video_id"],
            title=row["youtube_title"],
            channel=row["youtube_channel"],
            url=row["youtube_url"],
        )

    def youtube_video_ids_for_tracks(self, tracks: Iterable[PlaylistTrack]) -> set[str]:
        ids: set[str] = set()
        for track in tracks:
            match = self.get(track)
            if match:
                ids.add(match.video_id)
        return ids

    def set(self, match: YouTubeMatch) -> None:
        now = int(time.time())
        self.connection.execute(
            """
            INSERT INTO track_matches (
                track_key, spotify_track_id, isrc, query, youtube_video_id,
                youtube_title, youtube_channel, youtube_url, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(track_key) DO UPDATE SET
                spotify_track_id = excluded.spotify_track_id,
                isrc = excluded.isrc,
                query = excluded.query,
                youtube_video_id = excluded.youtube_video_id,
                youtube_title = excluded.youtube_title,
                youtube_channel = excluded.youtube_channel,
                youtube_url = excluded.youtube_url,
                updated_at = excluded.updated_at
            """,
            (
                track_key(match.track),
                match.track.track_id,
                match.track.isrc,
                match.track.youtube_query,
                match.video_id,
                match.title,
                match.channel,
                match.url,
                now,
            ),
        )
        self.connection.commit()

    def delete_match(self, track_key: str) -> bool:
        cursor = self.connection.execute("DELETE FROM track_matches WHERE track_key = ?", (track_key,))
        self.connection.commit()
        return cursor.rowcount > 0

    def playlists(self) -> list[SyncedPlaylist]:
        rows = self.connection.execute(
            """
            SELECT id, spotify_playlist_id, spotify_name, youtube_playlist_id, youtube_title,
                   notes, last_synced_at, created_at, updated_at
            FROM playlists
            ORDER BY updated_at DESC, spotify_name COLLATE NOCASE
            """
        ).fetchall()
        return [self._playlist_from_row(row) for row in rows]

    def get_playlist(self, playlist_id: int) -> SyncedPlaylist | None:
        row = self.connection.execute(
            """
            SELECT id, spotify_playlist_id, spotify_name, youtube_playlist_id, youtube_title,
                   notes, last_synced_at, created_at, updated_at
            FROM playlists
            WHERE id = ?
            """,
            (playlist_id,),
        ).fetchone()
        return self._playlist_from_row(row) if row else None

    def upsert_playlist(
        self,
        *,
        spotify_playlist_id: str,
        youtube_playlist_id: str,
        spotify_name: str = "",
        youtube_title: str = "",
        notes: str = "",
    ) -> SyncedPlaylist:
        now = int(time.time())
        self.connection.execute(
            """
            INSERT INTO playlists (
                spotify_playlist_id, spotify_name, youtube_playlist_id, youtube_title,
                notes, last_synced_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(spotify_playlist_id, youtube_playlist_id) DO UPDATE SET
                spotify_name = excluded.spotify_name,
                youtube_title = excluded.youtube_title,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (spotify_playlist_id, spotify_name, youtube_playlist_id, youtube_title, notes, now, now),
        )
        self.connection.commit()
        row = self.connection.execute(
            """
            SELECT id, spotify_playlist_id, spotify_name, youtube_playlist_id, youtube_title,
                   notes, last_synced_at, created_at, updated_at
            FROM playlists
            WHERE spotify_playlist_id = ? AND youtube_playlist_id = ?
            """,
            (spotify_playlist_id, youtube_playlist_id),
        ).fetchone()
        return self._playlist_from_row(row)

    def mark_playlist_synced(self, playlist_id: int) -> None:
        now = int(time.time())
        self.connection.execute("UPDATE playlists SET last_synced_at = ?, updated_at = ? WHERE id = ?", (now, now, playlist_id))
        self.connection.commit()

    def delete_playlist(self, playlist_id: int) -> bool:
        cursor = self.connection.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        self.connection.commit()
        return cursor.rowcount > 0

    def _migrate(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS track_matches (
                track_key TEXT PRIMARY KEY,
                spotify_track_id TEXT,
                isrc TEXT,
                query TEXT NOT NULL,
                youtube_video_id TEXT NOT NULL,
                youtube_title TEXT NOT NULL,
                youtube_channel TEXT NOT NULL,
                youtube_url TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                spotify_playlist_id TEXT NOT NULL,
                spotify_name TEXT NOT NULL DEFAULT '',
                youtube_playlist_id TEXT NOT NULL,
                youtube_title TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                last_synced_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(spotify_playlist_id, youtube_playlist_id)
            )
            """
        )
        self.connection.commit()

    def _playlist_from_row(self, row) -> SyncedPlaylist:
        return SyncedPlaylist(
            id=int(row["id"]),
            spotify_playlist_id=row["spotify_playlist_id"],
            spotify_name=row["spotify_name"],
            youtube_playlist_id=row["youtube_playlist_id"],
            youtube_title=row["youtube_title"],
            notes=row["notes"],
            last_synced_at=row["last_synced_at"],
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


def track_key(track: PlaylistTrack) -> str:
    if track.track_id:
        return f"spotify:{track.track_id}"
    if track.isrc:
        return f"isrc:{track.isrc}"
    return f"query:{track.youtube_query.casefold()}"


def sync_playlist(
    client: YouTubeClient,
    tracks: Iterable[PlaylistTrack],
    youtube_playlist_id: str,
    store: MatchStore,
    *,
    dry_run: bool,
    delay_seconds: float = DEFAULT_SEARCH_DELAY_SECONDS,
    rate_limit_retry_seconds: float = DEFAULT_RATE_LIMIT_RETRY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    on_progress: Callable[[str], None] | None = None,
) -> SyncResult:
    existing = set(client.playlist_video_ids(youtube_playlist_id))
    matched: list[YouTubeMatch] = []
    added: list[YouTubeMatch] = []
    already_present: list[YouTubeMatch] = []
    missing: list[PlaylistTrack] = []
    searched = 0

    for track in tracks:
        match = store.get(track)
        if match:
            _progress(on_progress, f"Cached match for {track.position}. {track.artists_text} - {track.track_name}")
        else:
            if searched and delay_seconds > 0:
                sleep(delay_seconds)
            searched += 1
            _progress(on_progress, f"Searching YouTube for {track.position}. {track.artists_text} - {track.track_name}")
            match = _search_with_retry(client, track, rate_limit_retry_seconds, sleep)
            if match:
                store.set(match)

        if not match:
            missing.append(track)
            continue

        matched.append(match)
        if match.video_id in existing:
            already_present.append(match)
            continue

        if not dry_run:
            client.add_video(youtube_playlist_id, match.video_id)
        existing.add(match.video_id)
        added.append(match)

    return SyncResult(matched=matched, added=added, already_present=already_present, missing=missing, dry_run=dry_run)


def sync_spotify_from_youtube(
    spotify: SpotifyClient,
    youtube: YouTubeClient,
    spotify_playlist_id: str,
    youtube_playlist_id: str,
    store: MatchStore,
    *,
    dry_run: bool,
    spotify_tracks: list[PlaylistTrack] | None = None,
    allow_spotify_search: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> SpotifySyncResult:
    spotify_tracks = spotify_tracks if spotify_tracks is not None else spotify.playlist_tracks(spotify_playlist_id)
    existing = {track.track_id for track in spotify_tracks if track.track_id}
    youtube_items = youtube.playlist_items(youtube_playlist_id)
    matched: list[YouTubeMatch] = []
    added: list[YouTubeMatch] = []
    already_present: list[YouTubeMatch] = []
    missing: list[YouTubePlaylistItem] = []

    for index, item in enumerate(youtube_items, start=1):
        match = store.get_by_youtube_video_id(item.video_id)
        if match:
            _progress(on_progress, f"Cached Spotify match for {index}. {item.title}")
            if not match.track.track_id:
                match = None
        if not match:
            if not allow_spotify_search:
                _progress(on_progress, f"Skipping uncached YouTube item {index}. {item.title}")
                missing.append(item)
                continue
            _progress(on_progress, f"Searching Spotify for {index}. {item.title}")
            candidates = spotify.search_tracks(item.title, limit=1)
            if not candidates:
                missing.append(item)
                continue
            track = candidates[0]
            match = YouTubeMatch(
                track=track,
                video_id=item.video_id,
                title=item.title,
                channel="",
                url=f"https://www.youtube.com/watch?v={item.video_id}",
            )
            store.set(match)

        matched.append(match)
        if match.track.track_id in existing:
            already_present.append(match)
            continue

        if not dry_run:
            spotify.add_tracks(spotify_playlist_id, [match.track.track_id])
        existing.add(match.track.track_id)
        added.append(match)

    return SpotifySyncResult(matched=matched, added=added, already_present=already_present, missing=missing, dry_run=dry_run)


def remove_youtube_items_not_in_spotify(
    client: YouTubeClient,
    spotify_tracks: Iterable[PlaylistTrack],
    youtube_playlist_id: str,
    store: MatchStore,
    *,
    dry_run: bool,
    on_progress: Callable[[str], None] | None = None,
) -> SyncRemoveResult:
    spotify_tracks = list(spotify_tracks)
    uncached_tracks = [track for track in spotify_tracks if not store.get(track)]
    if uncached_tracks:
        raise SyncError(_incomplete_cache_message("remove YouTube items", uncached_tracks))

    expected_video_ids = store.youtube_video_ids_for_tracks(spotify_tracks)
    removed: list[YouTubePlaylistItem] = []
    kept: list[YouTubePlaylistItem] = []
    unknown: list[YouTubePlaylistItem] = []
    for item in client.playlist_items(youtube_playlist_id):
        if item.video_id in expected_video_ids:
            kept.append(item)
            continue
        unknown.append(item)
        removed.append(item)
        _progress(on_progress, f"{'Would remove' if dry_run else 'Removing'} YouTube item: {item.title}")
        if not dry_run:
            client.remove_playlist_item(item.playlist_item_id)
    return SyncRemoveResult(direction="from_spotify", removed=removed, kept=kept, unknown=unknown, dry_run=dry_run)


def remove_spotify_tracks_not_in_youtube(
    spotify: SpotifyClient,
    youtube: YouTubeClient,
    spotify_playlist_id: str,
    youtube_playlist_id: str,
    store: MatchStore,
    *,
    dry_run: bool,
    spotify_tracks: list[PlaylistTrack] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> SyncRemoveResult:
    spotify_tracks = spotify_tracks if spotify_tracks is not None else spotify.playlist_tracks(spotify_playlist_id)
    uncached_tracks = [track for track in spotify_tracks if not store.get(track)]
    if uncached_tracks:
        raise SyncError(_incomplete_cache_message("remove Spotify tracks", uncached_tracks))

    youtube_video_ids = {item.video_id for item in youtube.playlist_items(youtube_playlist_id)}
    removed: list[YouTubeMatch] = []
    kept: list[YouTubeMatch] = []
    for track in spotify_tracks:
        match = store.get(track)
        if match and match.video_id in youtube_video_ids:
            kept.append(match)
            continue
        placeholder = YouTubeMatch(track=track, video_id=match.video_id if match else "", title=match.title if match else "", channel="", url="")
        removed.append(placeholder)
        _progress(on_progress, f"{'Would remove' if dry_run else 'Removing'} Spotify track: {track.artists_text} - {track.track_name}")
        if not dry_run:
            spotify.remove_tracks(spotify_playlist_id, [track.track_id])
    return SyncRemoveResult(direction="from_youtube", removed=removed, kept=kept, unknown=[], dry_run=dry_run)


def _incomplete_cache_message(action: str, uncached_tracks: list[PlaylistTrack]) -> str:
    examples = ", ".join(f"{track.artists_text} - {track.track_name}" for track in uncached_tracks[:3])
    suffix = f" Examples: {examples}." if examples else ""
    return (
        f"Cannot safely {action}: {len(uncached_tracks)} Spotify tracks have no cached YouTube mapping. "
        "Run sync-youtube first, or add manual mappings with map-youtube, then retry sync-remove."
        f"{suffix}"
    )


def _search_with_retry(
    client: YouTubeClient,
    track: PlaylistTrack,
    rate_limit_retry_seconds: float,
    sleep: Callable[[float], None],
) -> YouTubeMatch | None:
    try:
        video = client.search_video(track.youtube_query)
    except YouTubeRateLimitError:
        if rate_limit_retry_seconds <= 0:
            raise
        sleep(rate_limit_retry_seconds)
        video = client.search_video(track.youtube_query)
    if not video:
        return None
    return YouTubeMatch(track=track, video_id=video.video_id, title=video.title, channel=video.channel, url=video.url)


def _progress(callback: Callable[[str], None] | None, message: str) -> None:
    if callback:
        callback(message)
