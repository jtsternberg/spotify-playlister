from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .csv_export import write_tracks_csv
from .env import load_env
from .spotify import SpotifyClient, SpotifyError, cache_dir, extract_playlist_id
from .youtube import YouTubeClient, YouTubeError, match_tracks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spotify-playlister")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("playlists", help="List Spotify playlists available to the authenticated user.")

    export_csv = subparsers.add_parser("export-csv", help="Export a Spotify playlist to CSV.")
    export_csv.add_argument("playlist", help="Spotify playlist ID, URI, or open.spotify.com playlist URL.")
    export_csv.add_argument("--output", "-o", type=Path, help="CSV output path. Defaults to stdout.")

    export_youtube = subparsers.add_parser("export-youtube", help="Create or update a YouTube playlist from a Spotify playlist.")
    export_youtube.add_argument("playlist", help="Spotify playlist ID, URI, or open.spotify.com playlist URL.")
    export_youtube.add_argument("--youtube-client-secrets", type=Path, required=True, help="Google OAuth Desktop client JSON.")
    export_youtube.add_argument("--youtube-playlist-id", help="Existing YouTube playlist ID to add videos to.")
    export_youtube.add_argument("--title", help="Title for a new YouTube playlist.")
    export_youtube.add_argument("--description", default="Imported from Spotify with spotify-playlister.")
    export_youtube.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private")
    export_youtube.add_argument("--dry-run", action="store_true", help="Search YouTube and print matches without creating or inserting.")

    return parser


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        spotify = SpotifyClient.from_env()
        if args.command == "playlists":
            return list_playlists(spotify)
        if args.command == "export-csv":
            return export_csv(spotify, args)
        if args.command == "export-youtube":
            return export_youtube(spotify, args)
    except (SpotifyError, YouTubeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def list_playlists(spotify: SpotifyClient) -> int:
    playlists = spotify.list_playlists()
    for playlist in playlists:
        owner = (playlist.get("owner") or {}).get("display_name") or ""
        print(f"{playlist.get('id')}\t{playlist.get('name')}\t{owner}")
    return 0


def export_csv(spotify: SpotifyClient, args: argparse.Namespace) -> int:
    playlist_id = extract_playlist_id(args.playlist)
    tracks = spotify.playlist_tracks(playlist_id)
    if args.output:
        write_tracks_csv(tracks, args.output)
        print(f"Wrote {len(tracks)} tracks to {args.output}")
    else:
        write_tracks_csv(tracks, sys.stdout)
    return 0


def export_youtube(spotify: SpotifyClient, args: argparse.Namespace) -> int:
    playlist_id = extract_playlist_id(args.playlist)
    tracks = spotify.playlist_tracks(playlist_id)
    youtube = YouTubeClient.from_client_secrets(
        args.youtube_client_secrets.expanduser(),
        cache_dir() / "youtube-token.json",
    )
    matches = match_tracks(youtube, tracks)

    for match in matches:
        print(f"{match.track.position}. {match.track.artists_text} - {match.track.track_name}")
        print(f"   {match.title} | {match.channel} | {match.url}")

    missing = len(tracks) - len(matches)
    if missing:
        print(f"Skipped {missing} tracks with no YouTube search result.", file=sys.stderr)

    if args.dry_run:
        print(f"Dry run complete. Found {len(matches)} YouTube matches for {len(tracks)} Spotify tracks.")
        return 0

    youtube_playlist_id = args.youtube_playlist_id
    if not youtube_playlist_id:
        spotify_playlist = spotify.playlist(playlist_id)
        title = args.title or f"{spotify_playlist.get('name', 'Spotify playlist')} (Spotify import)"
        youtube_playlist_id = youtube.create_playlist(title, args.description, args.privacy)
        print(f"Created YouTube playlist: https://www.youtube.com/playlist?list={youtube_playlist_id}")

    for match in matches:
        youtube.add_video(youtube_playlist_id, match.video_id)
        print(f"Added {match.video_id}")

    print(f"Added {len(matches)} videos to https://www.youtube.com/playlist?list={youtube_playlist_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
