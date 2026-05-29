#!/usr/bin/env bash
# cache-lookup.sh — inspect the spotify-playlister sync cache to explain why a
# sync/remove flagged an item. Answers "why is X being added/removed?" with data.
#
# Usage:
#   cache-lookup.sh                 # cache summary (row count)
#   cache-lookup.sh <term>          # rows whose query/title match <term> (case-insensitive)
#   cache-lookup.sh --video <id>    # rows mapped to a specific YouTube video id
#   cache-lookup.sh --db <path> ... # override the cache location
#
# Default DB: $HOME/.spotify-playlister/sync.sqlite (or --sync-db used elsewhere).
set -euo pipefail

DB="${HOME}/.spotify-playlister/sync.sqlite"
VIDEO=""
TERM=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db)    DB="$2"; shift 2 ;;
    --video) VIDEO="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) TERM="$1"; shift ;;
  esac
done

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "error: sqlite3 not found on PATH" >&2
  exit 1
fi
if [[ ! -f "$DB" ]]; then
  echo "error: cache not found at $DB (run a sync to create it, or pass --db)" >&2
  exit 1
fi

count=$(sqlite3 "$DB" "SELECT COUNT(*) FROM track_matches;")
echo "cache: $DB"
echo "cached matches: $count"

if [[ -n "$VIDEO" ]]; then
  echo
  echo "=== rows mapped to video '$VIDEO' ==="
  sqlite3 -header -column "$DB" \
    "SELECT spotify_track_id, query, youtube_video_id, youtube_title
     FROM track_matches WHERE youtube_video_id = '$VIDEO';"
elif [[ -n "$TERM" ]]; then
  echo
  echo "=== rows matching '$TERM' ==="
  # Escape single quotes for the LIKE clause.
  esc=${TERM//\'/\'\'}
  sqlite3 -header -column "$DB" \
    "SELECT youtube_video_id, query, youtube_title
     FROM track_matches
     WHERE query LIKE '%${esc}%' OR youtube_title LIKE '%${esc}%';"
fi
