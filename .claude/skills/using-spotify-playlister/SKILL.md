---
name: using-spotify-playlister
description: >-
  Drive the `spotify-playlister` CLI in this repo — import tracks from a CSV into
  Spotify, export Spotify playlists to CSV, mirror them as YouTube playlists,
  sync tracks between Spotify and YouTube in either direction, prune stale items,
  hand-map tricky track→video matches, and run the local web UI.
when_to_use: >-
  Use whenever the user is working with Spotify playlists, YouTube playlists,
  the import-csv / export-csv / export-youtube / sync-youtube / sync-remove /
  map-youtube / set-youtube-privacy / web subcommands, the sync cache at
  ~/.spotify-playlister/sync.sqlite, or asks why an item is being added/removed
  by a sync — even if they don't name the CLI explicitly.
---

# Using spotify-playlister

`spotify-playlister` is the CLI in this repo. It reads playlists you own or
collaborate on from Spotify and reproduces / reconciles them on YouTube. This
skill is the operating manual: how to invoke each command safely, the mental
model that explains *why* a sync does what it does, and how to debug surprising
results.

## Mental model — read this first

Three ideas explain almost every behavior:

1. **Spotify is the catalog; YouTube is matched by search.** There's no clean ID
   mapping between the two services, so the tool searches YouTube for each
   Spotify track and picks the best video. Search is fuzzy, so it can pick the
   wrong upload — that's why dry runs matter and why you can hand-correct with
   `map-youtube`.

2. **A local SQLite cache is the source of truth for sync/remove.** Every
   resolved match is stored in `~/.spotify-playlister/sync.sqlite`. `sync-*` and
   `sync-remove` operate against this cache, *not* a fresh search — so they're
   fast and deterministic, but they trust whatever the cache says. Removal
   compares by **YouTube video ID**: a song present in both playlists but cached
   to a *different* upload looks like an orphan and gets flagged. (When a sync
   result is surprising, the cache is almost always the explanation — see
   [references/sync-cache.md](references/sync-cache.md).)

3. **Every mutating command is a dry run by default — `--apply` commits.**
   `export-youtube`, `sync-youtube`, and `sync-remove` all preview by default
   and only change anything when you add `--apply`. There's no `--dry-run` flag
   (it's the default and would be redundant). So the safe move is always the
   same: run it bare, read the "Would add/remove … N kept" summary, then re-run
   with `--apply` once it looks right. Nothing mutates Spotify or YouTube until
   `--apply` is present.

## Invoking the CLI

The console script lives in the project venv. From the repo root, either
activate it or call the binary directly:

```bash
source .venv/bin/activate && spotify-playlister <command> ...
# or, without activating:
.venv/bin/spotify-playlister <command> ...
```

All commands accept a playlist as an **ID, `spotify:playlist:...` URI, or
`open.spotify.com/playlist/...` URL** — they're interchangeable.

First-time setup (Spotify app + Google OAuth client + `.env`) is documented in
the repo `README.md`; point the user there if auth isn't configured yet.
YouTube features need the extra installed: `pip install -e '.[youtube]'`.

## Commands

### `playlists` — list what you can export
```bash
spotify-playlister playlists
```
Lists Spotify playlists the authenticated user owns or collaborates on. Use it
to grab a playlist ID before any other command.

### `export-csv` — dump a playlist to CSV
```bash
spotify-playlister export-csv <playlist> -o playlist.csv   # omit -o for stdout
```
Columns: position, track, artists, album, Spotify URL, duration, release date,
ISRC, and a local-file flag. Read-only; safe to run anytime.

### `import-csv` — add tracks from a CSV into a Spotify playlist  ⚠️ commits only with `--apply`
```bash
# Preview: resolve tracks and show what would be added (nothing changes):
spotify-playlister import-csv playlist.csv --title "New Playlist"

# Create a new Spotify playlist and add the tracks:
spotify-playlister import-csv playlist.csv --title "New Playlist" --apply

# Add into an existing Spotify playlist:
spotify-playlister import-csv playlist.csv --playlist-id <id-or-url> --apply
```
Exactly one of `--playlist-id` (existing playlist) or `--title` (create new) is required. New
playlists are private by default; `--public` opts in. Resolution order per row: `track_id` column
→ `spotify_url` column → search by `track_name` + `artists` (search hits are flagged `(search guess)`
in the preview — review before applying). Pass `--no-search` to skip search and skip unresolvable
rows instead.

### `export-youtube` — create/fill a YouTube playlist  ⚠️ commits only with `--apply`
```bash
# Preview (default): search and print matches, change nothing:
spotify-playlister export-youtube <playlist>

# Create a new YouTube playlist (add --apply to actually do it):
spotify-playlister export-youtube <playlist> --title "Imported from Spotify" --apply

# Add into an existing one:
spotify-playlister export-youtube <playlist> --youtube-playlist-id PL... --apply
```
Key flags: `--title`, `--description`, `--privacy private|unlisted|public`
(default `private`), `--youtube-client-secrets <json>`. For rate-limit pain, see
[Rate limits](#rate-limits-youtube).

### `sync-youtube` — add missing tracks  ⚠️ commits only with `--apply`
For playlists that already exist on both sides. Adds tracks missing from the
*target*. Previews by default; one-directional by default (Spotify → YouTube).
```bash
spotify-playlister sync-youtube <playlist> --youtube-playlist-id PL...          # preview
spotify-playlister sync-youtube <playlist> --youtube-playlist-id PL... --apply  # commit
```
Direction flags (mutually exclusive):
- `--from-spotify` (default) — add Spotify tracks missing from YouTube.
- `--from-youtube` — add YouTube items missing from Spotify.
- `--both` — add in both directions. Never deletes.

`--from-youtube`/`--both` use **only cached mappings** unless you pass
`--spotify-search-uncached`, which lets it search Spotify from the YouTube
title. That search "can make bad guesses" — review the preview before applying.

### `sync-remove` — prune stale items  ⚠️ deletes only with `--apply`
The mirror image of `sync-youtube`: removes items that the cache says aren't in
the *opposite* playlist.
```bash
# Preview (default): which YouTube videos aren't mapped from this Spotify playlist?
spotify-playlister sync-remove <playlist> --youtube-playlist-id PL...

# Actually delete, after reviewing:
spotify-playlister sync-remove <playlist> --youtube-playlist-id PL... --apply
```
Direction flags: `--from-spotify` (default, removes YouTube items),
`--from-youtube` (removes Spotify tracks), `--both`. Removal is **cache-driven
and compares by video ID**; it aborts if the Spotify playlist has uncached
tracks (so a partial cache can't cause wrong deletions). If a removal looks
wrong, don't `--apply` — investigate the cache via
[references/sync-cache.md](references/sync-cache.md).

### `map-youtube` — fix a bad or missing match
```bash
spotify-playlister map-youtube <spotify-track> <youtube-video>
```
Manually caches a Spotify-track → YouTube-video mapping. Use it when the
automatic match is wrong or absent; the next `sync-*` run reuses it in either
direction. This is the right tool when `sync-remove` wants to delete something
that *is* the correct song but is cached to the wrong upload. Mapping only
writes the local cache (reversible — remap anytime), so it's safe to do before
any mutating sync.

**Finding the right video.** The auto-matcher sometimes picks a weak result (a
movie clip, a cover, a live cut) because it takes the top search hit. The
bundled helper sets up a deliberate correction in one call — it finds the
matching Spotify track(s) and lists ranked YouTube candidates, then prints a
ready-to-edit `map-youtube` command:
```bash
${CLAUDE_SKILL_DIR}/scripts/find-match.sh <playlist> "<song or artist>" [--limit N]
```
Pick by signal, not rank: a "- Topic"/VEVO/official-label channel is safest; a
duration matching the studio track (vs. a shorter OST/clip) confirms it's the
full song; high view counts on fan uploads are popular but riskier (takedowns).
Paste the chosen id into the printed `map-youtube` command, then re-run the
relevant preview (the bare sync command) to confirm the corrected match before
applying.

By hand (if you want a one-off search without the playlist lookup):
```bash
yt-dlp "ytsearch10:<artist> <title>" --flat-playlist --no-warnings \
  --print "%(id)s | %(duration>%M:%S)s | %(channel)s | %(title)s | views=%(view_count)s"
```

### `set-youtube-privacy`
```bash
spotify-playlister set-youtube-privacy PL... private|unlisted|public
```

### `web` — local browser UI
```bash
spotify-playlister web                 # opens http://127.0.0.1:8877
spotify-playlister web --no-open       # don't auto-open a browser
spotify-playlister web --host 127.0.0.1 --port 8899
```
Manages saved playlist pairs, manual mappings, and sync/remove previews against
the same SQLite cache. Stop with Ctrl-C; if backgrounded, `lsof -ti tcp:8877 |
xargs kill`.

## Rate limits (YouTube)

YouTube search burns API quota. The tool already paces requests; if Google still
throttles, slow it down:
```bash
spotify-playlister export-youtube <playlist> --youtube-search-delay 5
```
`--youtube-rate-limit-retry <seconds>` controls the one retry after a rate-limit
hit (`0` = fail immediately). Same flags exist on `sync-youtube`.

## Common workflows

- **Copy/seed a Spotify playlist from a CSV:** `import-csv <file> --title "..."` (preview) →
  review search guesses → rerun with `--apply`. Search fallback can mis-pick — always review the
  dry-run output before applying. Use `--no-search` if you only want exact id/url matches.

- **"What's waiting to sync, and are the matches good?" (both directions):** the
  go-to status check for a saved pair. Runs both previews and scores each pending
  add's match quality (Spotify-vs-YouTube duration delta + title/channel flags),
  so wrong matches like a song mapped to the wrong upload surface before you sync:
  ```bash
  ${CLAUDE_SKILL_DIR}/scripts/sync-status.sh <playlist> --youtube-playlist-id PL...
  ```
  Read-only for the playlists (it never adds/removes), but note a side effect: the
  preview searches and **caches** matches for any uncached tracks. `--no-durations`
  skips the per-item YouTube fetch (faster). Each ⚠ line is a match to fix with
  `find-match.sh` + `map-youtube` before applying a sync. The same tracks can
  appear under both "add to YouTube" and "remove from Spotify" — that's the
  direction duality; pick the way you actually want to sync.
- **Mirror a Spotify playlist to a brand-new YouTube one:** `export-youtube
  <playlist> --title "..."` (preview) → review → rerun with `--apply`.
- **Keep an existing pair in sync:** `sync-youtube <playlist>
  --youtube-playlist-id PL...` (preview) → review → rerun with `--apply`. Add
  `--both` to fill gaps on both sides.
- **Clean up after deleting tracks on Spotify:** `sync-remove <playlist>
  --youtube-playlist-id PL...` (preview) → `--apply`.
- **"Why is this item being added/removed?"** It's the cache. Inspect mappings
  fast with the bundled helper, then read the deep dive if needed:
  ```bash
  ${CLAUDE_SKILL_DIR}/scripts/cache-lookup.sh "<song or artist>"   # search
  ${CLAUDE_SKILL_DIR}/scripts/cache-lookup.sh --video <video_id>   # by video
  ```
  Interpretation rules are in [references/sync-cache.md](references/sync-cache.md).

## Verify before claiming success

The dry-run-by-default output is the contract — read the "Would add/remove … N
kept" summary and confirm it matches intent before re-running with `--apply`.
For `--apply` runs, the printed Added/Removed counts are the evidence; quote them
rather than assuming the run did what was asked.
