from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .csv_export import write_tracks_csv
from .env import load_env
from .spotify import SpotifyClient, SpotifyError, cache_dir, extract_playlist_id
from .sync import MatchStore, sync_playlist, sync_spotify_from_youtube
from .youtube import DEFAULT_RATE_LIMIT_RETRY_SECONDS, DEFAULT_SEARCH_DELAY_SECONDS, YouTubeClient, YouTubeError, match_tracks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spotify-playlister")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("playlists", help="List Spotify playlists available to the authenticated user.")

    export_csv = subparsers.add_parser("export-csv", help="Export a Spotify playlist to CSV.")
    export_csv.add_argument("playlist", help="Spotify playlist ID, URI, or open.spotify.com playlist URL.")
    export_csv.add_argument("--output", "-o", type=Path, help="CSV output path. Defaults to stdout.")

    export_youtube = subparsers.add_parser("export-youtube", help="Create or update a YouTube playlist from a Spotify playlist.")
    export_youtube.add_argument("playlist", help="Spotify playlist ID, URI, or open.spotify.com playlist URL.")
    export_youtube.add_argument(
        "--youtube-client-secrets",
        type=Path,
        help="Google OAuth Desktop client JSON. Defaults to YOUTUBE_CLIENT_ID/YOUTUBE_CLIENT_SECRET from .env.",
    )
    export_youtube.add_argument("--youtube-playlist-id", help="Existing YouTube playlist ID to add videos to.")
    export_youtube.add_argument("--title", help="Title for a new YouTube playlist.")
    export_youtube.add_argument("--description", default="Imported from Spotify with spotify-playlister.")
    export_youtube.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private")
    export_youtube.add_argument("--dry-run", action="store_true", help="Search YouTube and print matches without creating or inserting.")
    export_youtube.add_argument(
        "--youtube-search-delay",
        type=float,
        default=DEFAULT_SEARCH_DELAY_SECONDS,
        help="Seconds to wait between YouTube search requests. Defaults to %(default)s.",
    )
    export_youtube.add_argument(
        "--youtube-rate-limit-retry",
        type=float,
        default=DEFAULT_RATE_LIMIT_RETRY_SECONDS,
        help="Seconds to wait before retrying once after a YouTube search rate limit. Use 0 to fail immediately.",
    )

    sync_youtube = subparsers.add_parser("sync-youtube", help="Sync missing Spotify tracks into an existing YouTube playlist.")
    sync_youtube.add_argument("playlist", help="Spotify playlist ID, URI, or open.spotify.com playlist URL.")
    sync_youtube.add_argument("--youtube-playlist-id", required=True, help="Existing YouTube playlist ID to sync into.")
    sync_youtube.add_argument(
        "--youtube-client-secrets",
        type=Path,
        help="Google OAuth Desktop client JSON. Defaults to YOUTUBE_CLIENT_ID/YOUTUBE_CLIENT_SECRET from .env.",
    )
    sync_youtube.add_argument("--dry-run", action="store_true", help="Resolve matches and show changes without applying them.")
    sync_youtube.add_argument(
        "--sync-db",
        type=Path,
        default=cache_dir() / "sync.sqlite",
        help="SQLite database used to cache Spotify-to-YouTube matches. Defaults to %(default)s.",
    )
    sync_youtube.add_argument(
        "--youtube-search-delay",
        type=float,
        default=DEFAULT_SEARCH_DELAY_SECONDS,
        help="Seconds to wait between uncached YouTube search requests. Defaults to %(default)s.",
    )
    sync_youtube.add_argument(
        "--youtube-rate-limit-retry",
        type=float,
        default=DEFAULT_RATE_LIMIT_RETRY_SECONDS,
        help="Seconds to wait before retrying once after a YouTube search rate limit. Use 0 to fail immediately.",
    )
    sync_direction = sync_youtube.add_mutually_exclusive_group()
    sync_direction.add_argument(
        "--from-spotify",
        action="store_const",
        const="from_spotify",
        dest="sync_direction",
        help="Add Spotify tracks missing from the YouTube playlist. This is the default.",
    )
    sync_direction.add_argument(
        "--from-youtube",
        action="store_const",
        const="from_youtube",
        dest="sync_direction",
        help="Add YouTube playlist items missing from the Spotify playlist.",
    )
    sync_direction.add_argument(
        "--both",
        action="store_const",
        const="both",
        dest="sync_direction",
        help="Add missing tracks in both directions. Does not delete anything.",
    )
    sync_youtube.set_defaults(sync_direction="from_spotify")

    set_privacy = subparsers.add_parser("set-youtube-privacy", help="Update an existing YouTube playlist's privacy.")
    set_privacy.add_argument("youtube_playlist_id", help="YouTube playlist ID.")
    set_privacy.add_argument("privacy", choices=["private", "unlisted", "public"], help="New playlist privacy.")
    set_privacy.add_argument(
        "--youtube-client-secrets",
        type=Path,
        help="Google OAuth Desktop client JSON. Defaults to YOUTUBE_CLIENT_ID/YOUTUBE_CLIENT_SECRET from .env.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        load_env()
        parser = build_parser()
        args = parser.parse_args(argv)

        spotify = SpotifyClient.from_env()
        if args.command == "playlists":
            return list_playlists(spotify)
        if args.command == "export-csv":
            return export_csv(spotify, args)
        if args.command == "export-youtube":
            return export_youtube(spotify, args)
        if args.command == "sync-youtube":
            return sync_youtube(spotify, args)
        if args.command == "set-youtube-privacy":
            return set_youtube_privacy(args)
    except (SpotifyError, YouTubeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
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
    client_secrets = args.youtube_client_secrets.expanduser() if args.youtube_client_secrets else None
    youtube = YouTubeClient.from_oauth_config(cache_dir() / "youtube-token.json", client_secrets)
    matches = match_tracks(
        youtube,
        tracks,
        delay_seconds=args.youtube_search_delay,
        rate_limit_retry_seconds=args.youtube_rate_limit_retry,
        on_progress=lambda track: print(f"Searching YouTube for {track.position}. {track.artists_text} - {track.track_name}", file=sys.stderr),
    )

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


def sync_youtube(spotify: SpotifyClient, args: argparse.Namespace) -> int:
    playlist_id = extract_playlist_id(args.playlist)
    tracks = None
    client_secrets = args.youtube_client_secrets.expanduser() if args.youtube_client_secrets else None
    youtube = YouTubeClient.from_oauth_config(cache_dir() / "youtube-token.json", client_secrets)
    store = MatchStore(args.sync_db.expanduser())
    try:
        if not args.dry_run and args.sync_direction in {"from_youtube", "both"}:
            spotify.ensure_playlist_writable(playlist_id)

        if args.sync_direction in {"from_spotify", "both"}:
            tracks = spotify.playlist_tracks(playlist_id)
            result = sync_playlist(
                youtube,
                tracks,
                args.youtube_playlist_id,
                store,
                dry_run=args.dry_run,
                delay_seconds=args.youtube_search_delay,
                rate_limit_retry_seconds=args.youtube_rate_limit_retry,
                on_progress=lambda message: print(message, file=sys.stderr),
            )
            print_youtube_sync_result(result, args.dry_run)

        if args.sync_direction in {"from_youtube", "both"}:
            result = sync_spotify_from_youtube(
                spotify,
                youtube,
                playlist_id,
                args.youtube_playlist_id,
                store,
                dry_run=args.dry_run,
                spotify_tracks=tracks,
                on_progress=lambda message: print(message, file=sys.stderr),
            )
            print_spotify_sync_result(result, args.dry_run)
    finally:
        store.close()

    return 0


def print_youtube_sync_result(result, dry_run: bool) -> None:
    action = "Would add" if dry_run else "Added"
    for match in result.added:
        print(f"{action}: {match.track.position}. {match.track.artists_text} - {match.track.track_name}")
        print(f"   {match.title} | {match.channel} | {match.url}")

    if result.missing:
        print(f"Skipped {len(result.missing)} tracks with no YouTube search result.", file=sys.stderr)

    print(
        f"YouTube sync {'dry run ' if dry_run else ''}complete. "
        f"{len(result.added)} {'would be added' if dry_run else 'added'}, "
        f"{len(result.already_present)} already present, "
        f"{len(result.missing)} missing match, "
        f"{len(result.matched)} cached/resolved matches."
    )


def print_spotify_sync_result(result, dry_run: bool) -> None:
    action = "Would add to Spotify" if dry_run else "Added to Spotify"
    for match in result.added:
        print(f"{action}: {match.title}")
        print(f"   {match.track.artists_text} - {match.track.track_name} | {match.track.spotify_url}")

    if result.missing:
        print(f"Skipped {len(result.missing)} YouTube videos with no Spotify search result.", file=sys.stderr)

    print(
        f"Spotify sync {'dry run ' if dry_run else ''}complete. "
        f"{len(result.added)} {'would be added' if dry_run else 'added'}, "
        f"{len(result.already_present)} already present, "
        f"{len(result.missing)} missing match, "
        f"{len(result.matched)} cached/resolved matches."
    )


def set_youtube_privacy(args: argparse.Namespace) -> int:
    client_secrets = args.youtube_client_secrets.expanduser() if args.youtube_client_secrets else None
    youtube = YouTubeClient.from_oauth_config(cache_dir() / "youtube-token.json", client_secrets)
    privacy = youtube.set_playlist_privacy(args.youtube_playlist_id, args.privacy)
    print(f"Updated https://www.youtube.com/playlist?list={args.youtube_playlist_id} to {privacy}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
