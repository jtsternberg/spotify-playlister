#!/usr/bin/env bash
# find-match.sh — set up a corrected Spotify→YouTube mapping in one call.
# Given a playlist and a search term, it finds the matching Spotify track(s)
# (with track_id) and lists ranked YouTube candidates via yt-dlp, then prints a
# ready-to-edit `map-youtube` command. Use it when the auto-matcher picked a bad
# video (movie clip, cover, live cut) and you want to choose deliberately.
#
# Usage:
#   find-match.sh <playlist> <search-term> [--limit N]
#   find-match.sh 0hl2Pe... "stuck in the middle" --limit 8
#
# <playlist> is a Spotify playlist ID, URI, or URL. <search-term> matches
# case-insensitively against track name and artists. --limit caps YouTube
# candidates (default 10). Read-only: it makes no changes, just hands you the
# pieces for a map-youtube call.
set -euo pipefail

PLAYLIST="${1:-}"
TERM_ARG="${2:-}"
LIMIT=10
shift 2 2>/dev/null || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --limit) LIMIT="$2"; shift 2 ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$PLAYLIST" || -z "$TERM_ARG" ]]; then
  echo "usage: find-match.sh <playlist> <search-term> [--limit N]" >&2
  exit 2
fi

# Resolve the spotify-playlister CLI: prefer PATH (activated venv), else the
# repo venv resolved relative to this script (repo root is 4 dirs up).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
if command -v spotify-playlister >/dev/null 2>&1; then
  CLI=(spotify-playlister)
elif [[ -x "$REPO_ROOT/.venv/bin/spotify-playlister" ]]; then
  CLI=("$REPO_ROOT/.venv/bin/spotify-playlister")
else
  echo "error: spotify-playlister not found on PATH or at $REPO_ROOT/.venv/bin/" >&2
  exit 1
fi
command -v yt-dlp >/dev/null 2>&1 || { echo "error: yt-dlp not found on PATH" >&2; exit 1; }
PY="python3"; [[ -x "$REPO_ROOT/.venv/bin/python" ]] && PY="$REPO_ROOT/.venv/bin/python"

# Pull the playlist once (network/auth) into a temp file. Run from the repo root
# so the CLI finds the project .env for Spotify credentials. The CSV goes to a
# file (not a pipe) because the Python core reads its script from stdin via the
# heredoc — stdin can't carry both the script and the data.
tmpd="$(mktemp -d)"; trap 'rm -rf "$tmpd"' EXIT
csv_file="$tmpd/playlist.csv"; err_file="$tmpd/err"
(cd "$REPO_ROOT" && "${CLI[@]}" export-csv "$PLAYLIST" >"$csv_file" 2>"$err_file") || {
  echo "error: export-csv failed for $PLAYLIST" >&2
  sed 's/^/  /' "$err_file" >&2
  exit 1; }

MATCH_TERM="$TERM_ARG" LIMIT="$LIMIT" CSV_FILE="$csv_file" "$PY" - <<'PY'
import csv, os, subprocess, sys

term = os.environ["MATCH_TERM"].lower()
limit = int(os.environ.get("LIMIT", "10"))
with open(os.environ["CSV_FILE"], newline="") as fh:
    rows = list(csv.DictReader(fh))
matches = [r for r in rows
           if term in (r.get("track_name") or "").lower()
           or term in (r.get("artists") or "").lower()]

if matches:
    print(f'Spotify matches for "{os.environ["MATCH_TERM"]}":')
    print(f'{"pos":>3}  {"track_name":40.40}  {"artists":28.28}  track_id')
    for r in matches:
        print(f'{r["position"]:>3}  {r["track_name"]:40.40}  {r["artists"]:28.28}  {r["track_id"]}')
    first = matches[0]
    primary_artist = (first.get("artists") or "").split(";")[0].strip()
    query = f'{primary_artist} {first["track_name"]}'.strip()
    spotify_url = first.get("spotify_url") or f'spotify:track:{first["track_id"]}'
    if len(matches) > 1:
        print(f'\n(note: {len(matches)} matches — searching YouTube for the first; '
              f'narrow the term for the others)')
else:
    print(f'No Spotify track matches "{os.environ["MATCH_TERM"]}" in this playlist.')
    query = os.environ["MATCH_TERM"]
    spotify_url = "<SPOTIFY_TRACK_URL>"

fmt = "%(id)s | %(duration>%M:%S)s | %(channel)s | %(title)s | views=%(view_count)s"
print(f'\nYouTube candidates for "{query}":')
try:
    out = subprocess.run(
        ["yt-dlp", f"ytsearch{limit}:{query}", "--flat-playlist",
         "--no-warnings", "--print", fmt],
        capture_output=True, text=True, check=True).stdout.strip()
    print(out if out else "  (no results)")
except subprocess.CalledProcessError as e:
    print(f"  yt-dlp error: {e.stderr.strip() or e}", file=sys.stderr)

print('\nPick a video id above, then map it (edit <VIDEO_ID>):')
print(f'  spotify-playlister map-youtube "{spotify_url}" "https://youtu.be/<VIDEO_ID>"')
print('Re-run the relevant sync (bare = preview) afterward to confirm the corrected match.')
PY
