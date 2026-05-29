# The sync cache — schema & debugging

`sync-youtube` and `sync-remove` don't re-search YouTube on every run. They read
resolved matches from a SQLite cache and treat it as the source of truth. When a
sync adds or removes something unexpected, the cache is the explanation. This
file is how to inspect it.

Default location: `~/.spotify-playlister/sync.sqlite` (override with `--sync-db`).

## Schema

Two tables:

```sql
CREATE TABLE track_matches (
    track_key          TEXT PRIMARY KEY,
    spotify_track_id   TEXT,
    isrc               TEXT,
    query              TEXT NOT NULL,   -- the search string used to find the video
    youtube_video_id   TEXT NOT NULL,
    youtube_title      TEXT NOT NULL,
    youtube_channel    TEXT NOT NULL,
    youtube_url        TEXT NOT NULL,
    updated_at         INTEGER NOT NULL
);

CREATE TABLE playlists (              -- saved Spotify↔YouTube pairs (used by the web UI)
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    spotify_playlist_id   TEXT NOT NULL,
    spotify_name          TEXT NOT NULL DEFAULT '',
    youtube_playlist_id   TEXT NOT NULL,
    youtube_title         TEXT NOT NULL DEFAULT '',
    notes                 TEXT NOT NULL DEFAULT '',
    last_synced_at        INTEGER,
    created_at            INTEGER NOT NULL,
    updated_at            INTEGER NOT NULL,
    UNIQUE(spotify_playlist_id, youtube_playlist_id)
);
```

## How removal decides (so you can predict it)

`sync-remove --from-spotify` (the default) implements
`remove_youtube_items_not_in_spotify` in `spotify_playlister/sync.py`:

1. It **requires every Spotify track to be cached** first. If any isn't, it
   raises a `SyncError` instead of running — a partial cache can never cause a
   wrong deletion. (So if a dry run *succeeds*, the Spotify side is fully
   cached.)
2. It builds `expected_video_ids` = the set of `youtube_video_id`s the cache
   maps the current Spotify tracks to.
3. It walks the live YouTube playlist. Any item whose `video_id` is **not** in
   `expected_video_ids` is flagged for removal.

`--from-youtube` is the mirror: it keeps Spotify tracks whose cached
`youtube_video_id` appears in the live YouTube playlist, and flags the rest.

The key consequence: **comparison is by video ID, via the cache.** A song that
genuinely exists in both playlists will still be flagged if its Spotify track is
cached to a *different* YouTube upload than the one sitting in the playlist. The
fix is `map-youtube` to point the cache at the right video — not `--apply`.

## Debugging recipe

When asked "why is X being removed/added," answer it with data, not guesses.
The bundled `scripts/cache-lookup.sh` wraps the queries below (count + lookup by
video id + search by term) into one call:

```bash
${CLAUDE_SKILL_DIR}/scripts/cache-lookup.sh                  # row count only
${CLAUDE_SKILL_DIR}/scripts/cache-lookup.sh "<song or artist>"  # search
${CLAUDE_SKILL_DIR}/scripts/cache-lookup.sh --video <VIDEO_ID>  # by video id
# --db <path> overrides the cache location to match a non-default --sync-db.
```

Raw SQL if you need a query the script doesn't cover:

```bash
DB="$HOME/.spotify-playlister/sync.sqlite"

# Is the surprising video mapped from anything at all?
sqlite3 -header -column "$DB" \
  "SELECT spotify_track_id, query, youtube_video_id, youtube_title
   FROM track_matches WHERE youtube_video_id = '<VIDEO_ID>';"

# Search the cache by song/artist text:
sqlite3 -header -column "$DB" \
  "SELECT youtube_video_id, query, youtube_title
   FROM track_matches
   WHERE query LIKE '%<TERM>%' OR youtube_title LIKE '%<TERM>%';"

# How many matches are cached (sanity-check against the 'N kept' line):
sqlite3 "$DB" "SELECT COUNT(*) FROM track_matches;"
```

Interpreting it:

- **No row maps to the video, and no row mentions the song** → that song isn't
  in the Spotify playlist (or was never cached). In `--from-spotify` remove mode
  it's a genuine orphan; removal is correct.
- **The song is cached but to a different `youtube_video_id`** → the playlist
  holds a different upload than the cache expects. Don't delete; `map-youtube`
  the correct video, then re-run the dry run.
- **The Spotify track isn't cached at all** → a `sync-remove` dry run would have
  errored, not run. If you're mid-investigation, resolve it with a bare
  `sync-youtube` (dry by default — it still populates the cache) or `map-youtube`.

Always re-run the dry run after editing the cache to confirm the flag clears
before anyone passes `--apply`.
