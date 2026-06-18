# Spotify Playlister

Export Spotify playlists you created or collaborate on to CSV, or recreate them as YouTube playlists.

## Setup

Create a Spotify app at <https://developer.spotify.com/dashboard>, then add this redirect URI:

```text
http://127.0.0.1:8765/callback
```

Install the CLI:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Create a local `.env` file:

```bash
cp .env.example .env
```

Then fill in `SPOTIFY_CLIENT_ID` with your Spotify app's client ID. `.env` is ignored by git.

## Export CSV

List your Spotify playlists:

```bash
.venv/bin/spotify-playlister playlists
```

Export by playlist ID, URI, or Spotify URL:

```bash
.venv/bin/spotify-playlister export-csv "https://open.spotify.com/playlist/..." --output playlist.csv
```

The CSV includes:

- position
- track name
- artists
- album
- Spotify URL
- duration
- release date
- ISRC
- whether the row is a local Spotify file

## Import From CSV

Add tracks from a CSV into a Spotify playlist — either an existing one or a new one created on the fly. Like other mutating commands, `import-csv` is a dry run by default:

```bash
# Preview: resolve tracks and print what would be added (nothing changes)
.venv/bin/spotify-playlister import-csv playlist.csv --title "Imported Playlist"

# Create a new playlist and add the tracks
.venv/bin/spotify-playlister import-csv playlist.csv --title "Imported Playlist" --apply

# Add into an existing playlist
.venv/bin/spotify-playlister import-csv playlist.csv --playlist-id <id-or-url> --apply
```

Exactly one of `--playlist-id` or `--title` is required.

### CSV format

The CSV is designed to round-trip with `export-csv` but is lenient for hand-authored files. Recognized columns (case-insensitive):

| Column | Purpose |
|--------|---------|
| `track_id` | Spotify track ID — used first if present |
| `spotify_url` | Full Spotify track URL — used if no `track_id` |
| `track_name` | Track title — used as search fallback |
| `artists` | Artist names (semicolon-separated) — used with `track_name` for search |

Unknown columns are ignored. Each row is resolved in order: `track_id` → `spotify_url` → search by `track_name` + `artists`. Rows that can't be resolved are skipped and reported.

Row numbers in the output count **data rows** (the header is excluded), so "row 1" is the first track — i.e. line 2 of the file.

Search results are flagged as `(search guess)` in the dry-run preview — review them before applying. Pass `--no-search` to skip the search fallback entirely and only use rows with a `track_id` or `spotify_url`.

New playlists are **private by default**; add `--public` to make them public.

## Export To YouTube

Install YouTube support:

```bash
source .venv/bin/activate
python -m pip install -e '.[youtube]'
```

Create an OAuth client in Google Cloud with the YouTube Data API enabled. If you use a Web application client, add this exact Authorized redirect URI:

```text
http://localhost:8766/
```

You can either download the OAuth client JSON or put the client values in `.env`:

```bash
YOUTUBE_CLIENT_ID="your-google-oauth-client-id"
YOUTUBE_CLIENT_SECRET="your-google-oauth-client-secret"
YOUTUBE_REDIRECT_URI="http://localhost:8766/"
```

`export-youtube` is a dry run by default — it searches and prints matches without creating or inserting anything:

```bash
.venv/bin/spotify-playlister export-youtube "https://open.spotify.com/playlist/..."
```

The CLI waits between YouTube searches to avoid per-minute quota errors. If your Google project is still rate-limited, use a slower delay:

```bash
.venv/bin/spotify-playlister export-youtube "https://open.spotify.com/playlist/..." \
  --youtube-search-delay 5
```

When the dry run looks right, add `--apply` to create a new YouTube playlist and add the best video search result for each Spotify track:

```bash
.venv/bin/spotify-playlister export-youtube "https://open.spotify.com/playlist/..." \
  --title "Imported from Spotify" \
  --apply
```

Use an existing YouTube playlist instead:

```bash
.venv/bin/spotify-playlister export-youtube "spotify:playlist:..." \
  --youtube-playlist-id "PL..." \
  --apply
```

If you prefer the downloaded JSON file instead of `.env`, add `--youtube-client-secrets ~/Downloads/client_secret.json`.

## Sync To An Existing YouTube Playlist

For playlists that already exist in both Spotify and YouTube, sync missing Spotify tracks into the YouTube playlist. Like the other commands, `sync-youtube` is a dry run by default:

```bash
.venv/bin/spotify-playlister sync-youtube "https://open.spotify.com/playlist/..." \
  --youtube-playlist-id "PL..."
```

This default is one-way from Spotify to YouTube. You can make the direction explicit:

```bash
.venv/bin/spotify-playlister sync-youtube "https://open.spotify.com/playlist/..." \
  --youtube-playlist-id "PL..." \
  --from-spotify
```

To add YouTube playlist items missing from Spotify, use `--from-youtube`:

```bash
.venv/bin/spotify-playlister sync-youtube "https://open.spotify.com/playlist/..." \
  --youtube-playlist-id "PL..." \
  --from-youtube
```

By default, YouTube-to-Spotify sync only uses cached mappings. This avoids adding bad Spotify guesses from unrelated YouTube videos. To allow Spotify search for uncached YouTube videos, opt in explicitly:

```bash
.venv/bin/spotify-playlister sync-youtube "https://open.spotify.com/playlist/..." \
  --youtube-playlist-id "PL..." \
  --from-youtube \
  --spotify-search-uncached
```

To add missing tracks in both directions, use `--both`:

```bash
.venv/bin/spotify-playlister sync-youtube "https://open.spotify.com/playlist/..." \
  --youtube-playlist-id "PL..." \
  --both
```

The sync command keeps a SQLite cache at `~/.spotify-playlister/sync.sqlite` so later runs can reuse Spotify-to-YouTube matches instead of searching again. When the dry run looks right, add `--apply` to make the changes:

```bash
.venv/bin/spotify-playlister sync-youtube "https://open.spotify.com/playlist/..." \
  --youtube-playlist-id "PL..." \
  --apply
```

YouTube-to-Spotify sync uses cached mappings when available. With `--spotify-search-uncached`, it falls back to Spotify search from the YouTube video title. Review the dry-run output before re-running with `--apply`.

## Remove Stale Items

Remove-mode sync is separate and dry-runs by default. To find YouTube videos that are not mapped from the current Spotify playlist:

```bash
.venv/bin/spotify-playlister sync-remove "https://open.spotify.com/playlist/..." \
  --youtube-playlist-id "PL..."
```

Apply the removals only after reviewing the output:

```bash
.venv/bin/spotify-playlister sync-remove "https://open.spotify.com/playlist/..." \
  --youtube-playlist-id "PL..." \
  --apply
```

Use `--from-youtube` to remove Spotify tracks that are not mapped from the YouTube playlist, or `--both` to remove in both directions. Removals use the local mapping cache as the source of truth and stop if the Spotify playlist has uncached tracks.

## Manually Map A Track

If the automatic match is missing or wrong, add a cache mapping yourself:

```bash
.venv/bin/spotify-playlister map-youtube \
  "https://open.spotify.com/track/..." \
  "https://www.youtube.com/watch?v=..."
```

The next `sync-youtube` run will reuse that mapping in either direction.

## Browser UI

Run the local browser UI to manage saved playlist pairs, manual mappings, sync previews, and remove previews:

```bash
.venv/bin/spotify-playlister web
```

The UI stores playlist pairs and mappings in the same SQLite database used by the CLI. It binds to `127.0.0.1:8877` by default and opens your browser automatically.

To start it without opening a browser:

```bash
.venv/bin/spotify-playlister web --no-open
```

To use a different host or port:

```bash
.venv/bin/spotify-playlister web --host 127.0.0.1 --port 8899
```

To stop the server, press `Ctrl-C` in the terminal where it is running. If you started it in the background, stop that shell job with `kill %1` or find and stop the process using the port:

```bash
lsof -ti tcp:8877 | xargs kill
```

## Update YouTube Playlist Privacy

Change an existing playlist to `private`, `unlisted`, or `public`:

```bash
.venv/bin/spotify-playlister set-youtube-privacy "PL..." unlisted
```

## Notes

Spotify's playlist item endpoint is currently intended for playlists owned by or collaborative with the authenticated user. YouTube matching is search-based, so every mutating command (`export-youtube`, `sync-youtube`, `sync-remove`) is a dry run by default — review the output, then re-run with `--apply` to make changes.
