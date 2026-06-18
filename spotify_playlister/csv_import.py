from __future__ import annotations
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .spotify import SpotifyClient, extract_track_id


@dataclass(frozen=True)
class CsvRow:
    line: int
    track_id: str
    spotify_url: str
    track_name: str
    artists: str


@dataclass(frozen=True)
class Resolved:
    row: CsvRow
    track_id: str
    label: str
    source: str  # "id" | "url" | "search"


def read_rows(path: Path) -> list[CsvRow]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for line_num, raw in enumerate(reader, start=1):
            normalized = {k.lower().strip(): (v or "").strip() for k, v in raw.items()}
            rows.append(CsvRow(
                line=line_num,
                track_id=normalized.get("track_id", ""),
                spotify_url=normalized.get("spotify_url", ""),
                track_name=normalized.get("track_name", ""),
                artists=normalized.get("artists", ""),
            ))
    return rows


def resolve_rows(
    spotify: SpotifyClient,
    rows: Iterable[CsvRow],
    *,
    search: bool = True,
    on_search: Callable[[CsvRow], None] | None = None,
) -> tuple[list[Resolved], list[CsvRow]]:
    resolved: list[Resolved] = []
    unresolved: list[CsvRow] = []

    for row in rows:
        if row.track_id:
            label = _label_from_row(row, row.track_id)
            resolved.append(Resolved(row=row, track_id=row.track_id, label=label, source="id"))
            continue

        if row.spotify_url:
            extracted = extract_track_id(row.spotify_url)
            if extracted and extracted != row.spotify_url:
                label = _label_from_row(row, extracted)
                resolved.append(Resolved(row=row, track_id=extracted, label=label, source="url"))
                continue

        if search and row.track_name:
            if on_search:
                on_search(row)
            query_parts = [row.track_name]
            if row.artists:
                query_parts.append(row.artists.replace("; ", " "))
            hits = spotify.search_tracks(" ".join(query_parts), limit=1)
            if hits:
                hit = hits[0]
                label = f"{hit.artists_text} - {hit.track_name}"
                resolved.append(Resolved(row=row, track_id=hit.track_id, label=label, source="search"))
                continue

        unresolved.append(row)

    return resolved, unresolved


def _label_from_row(row: CsvRow, track_id: str) -> str:
    parts = []
    if row.artists:
        parts.append(row.artists.replace("; ", ", "))
    if row.track_name:
        parts.append(row.track_name)
    return " - ".join(parts) if parts else track_id
