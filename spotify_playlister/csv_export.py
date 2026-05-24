from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, TextIO

from .models import PlaylistTrack


FIELDNAMES = [
    "position",
    "track_name",
    "artists",
    "album_name",
    "duration",
    "duration_ms",
    "explicit",
    "release_date",
    "isrc",
    "spotify_url",
    "added_at",
    "is_local",
    "track_id",
]


def write_tracks_csv(tracks: Iterable[PlaylistTrack], output: Path | TextIO) -> None:
    should_close = False
    if isinstance(output, Path):
        handle = output.open("w", newline="", encoding="utf-8")
        should_close = True
    else:
        handle = output

    try:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        for track in tracks:
            writer.writerow(
                {
                    "position": track.position,
                    "track_name": track.track_name,
                    "artists": track.artists_text,
                    "album_name": track.album_name,
                    "duration": track.duration,
                    "duration_ms": track.duration_ms,
                    "explicit": track.explicit,
                    "release_date": track.release_date,
                    "isrc": track.isrc,
                    "spotify_url": track.spotify_url,
                    "added_at": track.added_at,
                    "is_local": track.is_local,
                    "track_id": track.track_id,
                }
            )
    finally:
        if should_close:
            handle.close()
