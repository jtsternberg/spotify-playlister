#!/usr/bin/env python3
"""Core for sync-status.sh — parse dry-run output and score match quality.

Invoked by sync-status.sh with file paths; not meant to be run directly.
Reads the `sync-youtube --both` and `sync-remove --both` output that the wrapper
captured (both commands are dry runs by default), cross-references the
export-csv dump and the match cache,
optionally fetches YouTube durations via yt-dlp, and prints a consolidated
"what's pending + are the matches good" report for a playlist pair.
"""
from __future__ import annotations
import argparse, csv, html, re, sqlite3, subprocess, sys

# Title words that often signal a wrong/partial recording. Advisory only —
# "live"/"cover" can be intentional, so they inform a flag, never a hard fail.
RED_FLAGS = ("clip", "trailer", "teaser", "reaction", "cover", "remix",
             "sped up", "slowed", "nightcore", "karaoke", "instrumental",
             "snippet", "preview", "full episode", "movie", "8d audio")
DUR_TOLERANCE_S = 15  # |spotify - youtube| beyond this → likely a different cut


def parse_adds(path):
    """Return (spotify_to_yt, yt_to_spotify) add lists from sync-youtube output."""
    s2y, y2s = [], []
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        m = re.match(r"^Would add: (\d+)\. (.+)$", ln)
        if m:  # Spotify → YouTube; detail line: "   <title> | <channel> | <url>"
            title, channel, url = (nxt.strip().split(" | ") + ["", "", ""])[:3]
            s2y.append({"pos": int(m.group(1)), "label": m.group(2),
                        "yt_title": html.unescape(title),
                        "channel": html.unescape(channel), "url": url})
            continue
        m = re.match(r"^Would add to Spotify: (.+)$", ln)
        if m:  # YouTube → Spotify; detail: "   <artist> - <track> | <spotify_url>"
            label, sp_url = (nxt.strip().split(" | ") + ["", ""])[:2]
            y2s.append({"yt_title": html.unescape(m.group(1)),
                        "label": label, "spotify_url": sp_url})
    return s2y, y2s


def parse_removes(path):
    """Return (yt_orphans, spotify_orphans) from sync-remove output."""
    yt, sp = [], []
    for ln in open(path, encoding="utf-8", errors="replace"):
        m = re.match(r"^Would remove from YouTube: (.+?) \| (\S+)$", ln.strip())
        if m:
            yt.append({"yt_title": html.unescape(m.group(1)), "url": m.group(2)})
            continue
        m = re.match(r"^Would remove from Spotify: (.+)$", ln.strip())
        if m:
            sp.append({"label": m.group(1)})
    return yt, sp


def video_id(url):
    m = re.search(r"(?:v=|youtu\.be/|/watch\?v=)([\w-]{11})", url or "")
    return m.group(1) if m else ""


def load_csv(path):
    by_pos, by_track = {}, {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            by_pos[r["position"]] = r
            by_track[r["track_id"]] = r
    return by_pos, by_track


def cache_video_for_track(db, track_id):
    try:
        con = sqlite3.connect(db)
        row = con.execute(
            "SELECT youtube_video_id, youtube_title, youtube_channel "
            "FROM track_matches WHERE spotify_track_id = ?", (track_id,)).fetchone()
        con.close()
        return row or (None, None, None)
    except sqlite3.Error:
        return (None, None, None)


def yt_durations(ids):
    """Batch-fetch YouTube durations (seconds) for video ids. {} if yt-dlp fails."""
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return {}
    urls = [f"https://youtu.be/{i}" for i in ids]
    try:
        out = subprocess.run(
            ["yt-dlp", *urls, "--skip-download", "--no-warnings",
             "--print", "%(id)s\t%(duration)s"],
            capture_output=True, text=True, check=False).stdout
    except FileNotFoundError:
        return {}
    durs = {}
    for ln in out.splitlines():
        if "\t" in ln:
            vid, d = ln.split("\t", 1)
            durs[vid] = int(float(d)) if d not in ("", "NA", "None") else None
    return durs


def fmt_mmss(secs):
    if secs is None:
        return "?:??"
    return f"{secs // 60}:{secs % 60:02d}"


def quality(sp_secs, yt_secs, yt_title, channel):
    """Return (ok, note). ok=False means review; note explains why."""
    reasons = []
    if sp_secs is not None and yt_secs is not None:
        delta = abs(sp_secs - yt_secs)
        if delta > DUR_TOLERANCE_S:
            reasons.append(f"length off by {delta}s ({fmt_mmss(sp_secs)} vs {fmt_mmss(yt_secs)})")
    low = (yt_title or "").lower()
    hits = [w for w in RED_FLAGS if w in low]
    if hits:
        reasons.append("title: " + ", ".join(hits))
    ch = (channel or "").lower()
    official = ch.endswith("- topic") or "vevo" in ch or "official" in ch
    if reasons:  # only mention channel when something else is already suspect
        reasons.append("official channel" if official else "unverified channel")
    return (not reasons, "; ".join(reasons))


def report_adds(title, items, get_sp_secs, get_yt_secs, get_meta, durations_on):
    print(f"\n## {title}: {len(items)}")
    if not items:
        print("  (nothing pending)")
        return 0
    flagged = 0
    for it in items:
        sp_secs = get_sp_secs(it)
        yt_secs = get_yt_secs(it) if durations_on else None
        yt_title, channel = get_meta(it)
        ok, note = quality(sp_secs, yt_secs, yt_title, channel)
        mark = "  ✓" if ok else "  ⚠"
        if not ok:
            flagged += 1
        print(f"{mark} {it['label']}")
        print(f"      → {yt_title} | {channel or '?'}")
        if note:
            print(f"      ! {note}")
    return flagged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--adds", required=True)
    ap.add_argument("--removes", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--durations", dest="durations", action="store_true", default=True)
    ap.add_argument("--no-durations", dest="durations", action="store_false")
    ap.add_argument("--adds-note", default="")
    ap.add_argument("--removes-note", default="")
    args = ap.parse_args()

    by_pos, by_track = load_csv(args.csv)
    s2y, y2s = parse_adds(args.adds)
    yt_orphans, sp_orphans = parse_removes(args.removes)

    def sp_secs_from_pos(it):
        r = by_pos.get(str(it["pos"]))
        return int(int(r["duration_ms"]) / 1000) if r and r.get("duration_ms") else None

    def sp_secs_from_url(it):
        tid = video_id_spotify(it["spotify_url"])
        r = by_track.get(tid)
        return int(int(r["duration_ms"]) / 1000) if r and r.get("duration_ms") else None

    # Resolve YouTube durations up front in one batch.
    s2y_ids = {video_id(it["url"]): it for it in s2y}
    y2s_vid = {}
    for it in y2s:
        tid = video_id_spotify(it["spotify_url"])
        vid, ytitle, ch = cache_video_for_track(args.db, tid)
        y2s_vid[id(it)] = (vid, ytitle, ch)
    durs = yt_durations(list(s2y_ids.keys()) + [v[0] for v in y2s_vid.values()]) \
        if args.durations else {}

    print("=" * 60)
    print("SYNC STATUS — both directions")
    print(f"(duration check: {'on' if args.durations else 'off'}; "
          f"tolerance ±{DUR_TOLERANCE_S}s)")
    print("=" * 60)

    if args.adds_note:
        print("\n## Add previews: UNAVAILABLE")
        print(f"  ! {args.adds_note}")
        f1 = f2 = 0
    else:
        f1 = report_adds(
            "Add to YouTube (in Spotify, missing from YouTube)", s2y,
            sp_secs_from_pos,
            lambda it: durs.get(video_id(it["url"])),
            lambda it: (it["yt_title"], it["channel"]),
            args.durations)
        f2 = report_adds(
            "Add to Spotify (in YouTube, missing from Spotify)", y2s,
            sp_secs_from_url,
            lambda it: durs.get(y2s_vid[id(it)][0]),
            lambda it: (it["yt_title"], y2s_vid[id(it)][2]),
            args.durations)

    if args.removes_note:
        print("\n## Removal check: UNAVAILABLE")
        print(f"  ! {args.removes_note}")
        print("  Resolve the unmapped track(s) first — run sync-youtube to "
              "search+cache, or fix a bad match with find-match.sh — then retry.")
    else:
        print(f"\n## Remove from YouTube (orphans not in Spotify): {len(yt_orphans)}")
        for it in yt_orphans:
            print(f"  - {it['yt_title']} | {it['url']}")
        print(f"\n## Remove from Spotify (orphans not in YouTube): {len(sp_orphans)}")
        for it in sp_orphans:
            print(f"  - {it['label']}")

    print("\n" + "=" * 60)
    total_add = len(s2y) + len(y2s)
    add_txt = "add preview unavailable" if args.adds_note else f"{total_add} to add"
    rm_txt = ("remove check unavailable" if args.removes_note
              else f"{len(yt_orphans) + len(sp_orphans)} to remove")
    print(f"Pending: {add_txt}, {rm_txt}. Matches to review: {f1 + f2}.")
    if f1 + f2:
        print("Fix a flagged match with find-match.sh + map-youtube before syncing.")


def video_id_spotify(url):
    m = re.search(r"track[:/]([A-Za-z0-9]+)", url or "")
    return m.group(1) if m else ""


if __name__ == "__main__":
    main()
