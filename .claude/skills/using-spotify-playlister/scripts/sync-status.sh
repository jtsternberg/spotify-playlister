#!/usr/bin/env bash
# sync-status.sh — "what's waiting to sync, and are the matches good?" in one call.
# For a saved Spotify↔YouTube pair, runs both sync previews (dry by default),
# then reports four buckets — add→YouTube, add→Spotify, remove-from-YouTube,
# remove-from-Spotify — and scores each pending add's match quality by comparing
# Spotify vs YouTube duration (flags clips/edits/wrong cuts) plus title/channel
# heuristics. Read-only: it changes nothing.
#
# Usage:
#   sync-status.sh <playlist> --youtube-playlist-id <yt-id> [options]
#
# Options:
#   --youtube-playlist-id <id>   YouTube playlist to compare against (required)
#   --sync-db <path>             Cache DB (default ~/.spotify-playlister/sync.sqlite)
#   --no-durations               Skip the YouTube duration fetch (faster, less accurate)
#   --youtube-client-secrets <f> Passed through to the CLI if you don't use .env
#
# <playlist> is a Spotify playlist ID, URI, or URL.
set -euo pipefail

# First bare argument is the playlist; everything else is a flag (order-free).
PLAYLIST=""; YT_ID=""; SYNC_DB="$HOME/.spotify-playlister/sync.sqlite"; DURATIONS="--durations"
CLIENT_SECRETS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --youtube-playlist-id) YT_ID="$2"; shift 2 ;;
    --sync-db) SYNC_DB="$2"; shift 2 ;;
    --no-durations) DURATIONS="--no-durations"; shift ;;
    --youtube-client-secrets) CLIENT_SECRETS=(--youtube-client-secrets "$2"); shift 2 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown arg: $1" >&2; exit 2 ;;
    *) if [[ -z "$PLAYLIST" ]]; then PLAYLIST="$1"; shift
       else echo "unexpected arg: $1" >&2; exit 2; fi ;;
  esac
done

if [[ -z "$PLAYLIST" || -z "$YT_ID" ]]; then
  echo "usage: sync-status.sh <playlist> --youtube-playlist-id <yt-id> [--sync-db <p>] [--no-durations]" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
if command -v spotify-playlister >/dev/null 2>&1; then
  CLI=(spotify-playlister)
elif [[ -x "$REPO_ROOT/.venv/bin/spotify-playlister" ]]; then
  CLI=("$REPO_ROOT/.venv/bin/spotify-playlister")
else
  echo "error: spotify-playlister not found on PATH or at $REPO_ROOT/.venv/bin/" >&2
  exit 1
fi
PY="python3"; [[ -x "$REPO_ROOT/.venv/bin/python" ]] && PY="$REPO_ROOT/.venv/bin/python"
if [[ "$DURATIONS" == "--durations" ]] && ! command -v yt-dlp >/dev/null 2>&1; then
  echo "note: yt-dlp not found — skipping duration check (pass --no-durations to silence)" >&2
  DURATIONS="--no-durations"
fi

tmpd="$(mktemp -d)"; trap 'rm -rf "$tmpd"' EXIT
csv="$tmpd/csv"; adds="$tmpd/adds"; removes="$tmpd/removes"
csv_err="$tmpd/csv_err"; yt_err="$tmpd/yt_err"; rm_err="$tmpd/rm_err"
db_arg=(--sync-db "$SYNC_DB")
# ${arr[@]+"${arr[@]}"} guards empty-array expansion under set -u on bash 3.2 (macOS).
cs=("${CLIENT_SECRETS[@]+"${CLIENT_SECRETS[@]}"}")

echo "Gathering status for the pair (previews only — playlists won't change)…" >&2

# export-csv is the one hard dependency.
if ! ( cd "$REPO_ROOT" && "${CLI[@]}" export-csv "$PLAYLIST" ) >"$csv" 2>"$csv_err"; then
  echo "error: export-csv failed" >&2; sed 's/^/  /' "$csv_err" >&2; exit 1
fi

# sync-youtube/sync-remove are dry runs by default; --both covers each direction.
# A dry-run sync-youtube still searches and caches new matches (resolving
# uncached tracks); it just never edits the playlists without --apply.
# sync-remove deliberately aborts if a Spotify track has no cached match — that's
# status worth reporting, not a fatal error, so each call is captured independently.
adds_note=""
if ! ( cd "$REPO_ROOT" && "${CLI[@]}" sync-youtube "$PLAYLIST" --youtube-playlist-id "$YT_ID" --both "${cs[@]+"${cs[@]}"}" "${db_arg[@]}" ) >"$adds" 2>"$yt_err"; then
  adds_note="$(grep -i 'error:' "$yt_err" | head -1)"; : >"$adds"
fi
removes_note=""
if ! ( cd "$REPO_ROOT" && "${CLI[@]}" sync-remove "$PLAYLIST" --youtube-playlist-id "$YT_ID" --both "${cs[@]+"${cs[@]}"}" "${db_arg[@]}" ) >"$removes" 2>"$rm_err"; then
  removes_note="$(grep -i 'error:' "$rm_err" | head -1)"; : >"$removes"
fi

"$PY" "$SCRIPT_DIR/sync_status.py" \
  --csv "$csv" --adds "$adds" --removes "$removes" --db "$SYNC_DB" "$DURATIONS" \
  --adds-note "$adds_note" --removes-note "$removes_note"
