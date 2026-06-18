import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spotify_playlister import cli
from spotify_playlister.csv_import import CsvRow, read_rows, resolve_rows
from spotify_playlister.models import PlaylistTrack
from spotify_playlister.spotify import SpotifyApiError, SpotifyError


def _write_csv(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return Path(f.name)


def _make_track(track_id="tid1", name="Song", artists=("Artist",)):
    return PlaylistTrack(
        position=1,
        track_id=track_id,
        track_name=name,
        artists=tuple(artists),
        album_name="Album",
        spotify_url=f"https://open.spotify.com/track/{track_id}",
        duration_ms=200000,
        release_date="2020-01-01",
        isrc="USRC11600001",
        is_local=False,
        added_at="2020-01-01T00:00:00Z",
        explicit=False,
    )


class FakeSpotify:
    def __init__(self, search_results=None):
        self._search_results = search_results or []
        self.searched = []
        self.created_playlists = []
        self.added_tracks = []
        self.ensured_writable = []
        self.user_id = "user1"

    def search_tracks(self, query, limit=1):
        self.searched.append(query)
        return self._search_results[:limit]

    def create_playlist(self, name, public=False, description=""):
        self.created_playlists.append({"name": name, "public": public, "description": description})
        return "new-playlist-id"

    def add_tracks(self, playlist_id, track_ids):
        self.added_tracks.append((playlist_id, list(track_ids)))

    def ensure_playlist_writable(self, playlist_id):
        self.ensured_writable.append(playlist_id)

    def current_user_id(self):
        return self.user_id


# ── read_rows ──────────────────────────────────────────────────────────────────

class TestReadRows(unittest.TestCase):
    def test_reads_all_recognized_columns(self):
        p = _write_csv("track_id,spotify_url,track_name,artists\nid1,url1,Song,Artist\n")
        rows = read_rows(p)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].track_id, "id1")
        self.assertEqual(rows[0].spotify_url, "url1")
        self.assertEqual(rows[0].track_name, "Song")
        self.assertEqual(rows[0].artists, "Artist")
        self.assertEqual(rows[0].line, 1)

    def test_case_insensitive_headers(self):
        p = _write_csv("Track_ID,TRACK_NAME\nid1,My Song\n")
        rows = read_rows(p)
        self.assertEqual(rows[0].track_id, "id1")
        self.assertEqual(rows[0].track_name, "My Song")

    def test_ignores_unknown_columns(self):
        p = _write_csv("track_id,extra_col\nid1,ignored\n")
        rows = read_rows(p)
        self.assertEqual(rows[0].track_id, "id1")

    def test_empty_csv_returns_empty(self):
        p = _write_csv("track_id,track_name\n")
        rows = read_rows(p)
        self.assertEqual(rows, [])


# ── resolve_rows ───────────────────────────────────────────────────────────────

class TestResolveRows(unittest.TestCase):
    def test_exact_id_row(self):
        row = CsvRow(line=1, track_id="abc123", spotify_url="", track_name="Song", artists="Artist")
        spotify = FakeSpotify()
        resolved, unresolved = resolve_rows(spotify, [row])
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].track_id, "abc123")
        self.assertEqual(resolved[0].source, "id")
        self.assertEqual(unresolved, [])
        self.assertEqual(spotify.searched, [])

    def test_spotify_url_row(self):
        row = CsvRow(line=1, track_id="", spotify_url="https://open.spotify.com/track/tid999", track_name="Song", artists="A")
        spotify = FakeSpotify()
        resolved, unresolved = resolve_rows(spotify, [row])
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].track_id, "tid999")
        self.assertEqual(resolved[0].source, "url")
        self.assertEqual(spotify.searched, [])

    def test_search_fallback(self):
        row = CsvRow(line=1, track_id="", spotify_url="", track_name="My Song", artists="Artist A")
        hit = _make_track("search-id", "My Song", ("Artist A",))
        spotify = FakeSpotify(search_results=[hit])
        resolved, unresolved = resolve_rows(spotify, [row])
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].track_id, "search-id")
        self.assertEqual(resolved[0].source, "search")
        self.assertIn("My Song", resolved[0].label)
        self.assertEqual(len(spotify.searched), 1)

    def test_no_search_flag_leaves_name_only_unresolved(self):
        row = CsvRow(line=1, track_id="", spotify_url="", track_name="Song", artists="A")
        spotify = FakeSpotify(search_results=[_make_track()])
        resolved, unresolved = resolve_rows(spotify, [row], search=False)
        self.assertEqual(resolved, [])
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(spotify.searched, [])

    def test_unresolvable_row(self):
        row = CsvRow(line=1, track_id="", spotify_url="", track_name="", artists="")
        spotify = FakeSpotify()
        resolved, unresolved = resolve_rows(spotify, [row])
        self.assertEqual(resolved, [])
        self.assertEqual(len(unresolved), 1)

    def test_on_search_callback_called(self):
        row = CsvRow(line=2, track_id="", spotify_url="", track_name="Song", artists="A")
        hit = _make_track()
        spotify = FakeSpotify(search_results=[hit])
        called_with = []
        resolve_rows(spotify, [row], on_search=lambda r: called_with.append(r))
        self.assertEqual(called_with, [row])


# ── CLI integration ────────────────────────────────────────────────────────────

class TestImportCsvCli(unittest.TestCase):
    def _run(self, csv_content, extra_args, spotify=None):
        p = _write_csv(csv_content)
        if spotify is None:
            spotify = FakeSpotify()
        out = io.StringIO()
        err = io.StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            result = cli.cmd_import_csv(spotify, _make_args(str(p), extra_args))
        return result, out.getvalue(), err.getvalue(), spotify

    def test_dry_run_default_no_mutations(self):
        code, out, err, spotify = self._run(
            "track_id\nid1\n",
            {"title": "New PL", "playlist_id": None, "apply": False, "no_search": False, "description": "Desc", "public": False},
        )
        self.assertEqual(code, 0)
        self.assertIn("Dry run", out)
        self.assertEqual(spotify.created_playlists, [])
        self.assertEqual(spotify.added_tracks, [])

    def test_apply_with_title_creates_and_adds(self):
        code, out, err, spotify = self._run(
            "track_id\nid1\nid2\n",
            {"title": "My PL", "playlist_id": None, "apply": True, "no_search": False, "description": "Desc", "public": False},
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(spotify.created_playlists), 1)
        self.assertEqual(spotify.created_playlists[0]["name"], "My PL")
        self.assertEqual(spotify.created_playlists[0]["public"], False)
        self.assertEqual(spotify.added_tracks[0][1], ["id1", "id2"])

    def test_apply_with_playlist_id_no_create(self):
        code, out, err, spotify = self._run(
            "track_id\nid1\n",
            {"title": None, "playlist_id": "existing-pl", "apply": True, "no_search": False, "description": "", "public": False},
        )
        self.assertEqual(code, 0)
        self.assertEqual(spotify.created_playlists, [])
        self.assertEqual(spotify.ensured_writable, ["existing-pl"])
        self.assertEqual(spotify.added_tracks[0][0], "existing-pl")

    def test_public_flag(self):
        code, out, err, spotify = self._run(
            "track_id\nid1\n",
            {"title": "PL", "playlist_id": None, "apply": True, "no_search": False, "description": "", "public": True},
        )
        self.assertEqual(spotify.created_playlists[0]["public"], True)

    def test_empty_csv_returns_error(self):
        code, out, err, spotify = self._run(
            "track_id\n",
            {"title": "PL", "playlist_id": None, "apply": False, "no_search": False, "description": "", "public": False},
        )
        self.assertEqual(code, 1)
        self.assertIn("No data rows", err)

    def test_search_guess_label_flagged(self):
        hit = _make_track("s1", "My Song", ("Artist",))
        spotify = FakeSpotify(search_results=[hit])
        code, out, err, _ = self._run(
            "track_name,artists\nMy Song,Artist\n",
            {"title": "PL", "playlist_id": None, "apply": False, "no_search": False, "description": "", "public": False},
            spotify=spotify,
        )
        self.assertIn("search guess", out)


def _make_args(csv_path, overrides):
    import argparse
    ns = argparse.Namespace(
        csv=Path(csv_path),
        title=overrides.get("title"),
        playlist_id=overrides.get("playlist_id"),
        apply=overrides.get("apply", False),
        no_search=overrides.get("no_search", False),
        description=overrides.get("description", ""),
        public=overrides.get("public", False),
    )
    return ns


# ── SpotifyClient.create_playlist 403 error ───────────────────────────────────

class TestCreatePlaylist403(unittest.TestCase):
    def test_403_raises_friendly_spotify_error(self):
        from unittest.mock import patch as _patch
        from spotify_playlister.spotify import SpotifyClient, SpotifyApiError, SpotifyError

        client = SpotifyClient.__new__(SpotifyClient)
        client.token_path = Path("/tmp/fake-token.json")

        def fake_json_request(*a, **kw):
            raise SpotifyApiError(403, "Forbidden")

        with _patch("spotify_playlister.spotify._json_request", fake_json_request), \
             _patch.object(client, "current_user_id", return_value="uid"), \
             _patch.object(client, "access_token", return_value="fake"):
            with self.assertRaises(SpotifyError) as ctx:
                client.create_playlist("Test")
            self.assertIn("playlist-modify", str(ctx.exception))


# ── SpotifyClient.add_tracks 400 error ────────────────────────────────────────

class TestAddTracks400(unittest.TestCase):
    def test_400_raises_friendly_spotify_error(self):
        from unittest.mock import patch as _patch
        from spotify_playlister.spotify import SpotifyClient, SpotifyApiError, SpotifyError

        client = SpotifyClient.__new__(SpotifyClient)
        client.token_path = Path("/tmp/fake-token.json")

        def fake_json_request(*a, **kw):
            raise SpotifyApiError(400, "Bad request")

        with _patch("spotify_playlister.spotify._json_request", fake_json_request), \
             _patch.object(client, "access_token", return_value="fake"):
            with self.assertRaises(SpotifyError) as ctx:
                client.add_tracks("pl", ["not-a-real-id"])
            message = str(ctx.exception)
            self.assertIn("HTTP 400", message)
            self.assertIn("not-a-real-id", message)


# ── argparse mutual exclusivity of --playlist-id / --title ────────────────────

class TestImportCsvArgParsing(unittest.TestCase):
    def test_both_targets_is_error(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.build_parser().parse_args(["import-csv", "x.csv", "--playlist-id", "A", "--title", "B"])
        self.assertEqual(ctx.exception.code, 2)

    def test_neither_target_is_error(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.build_parser().parse_args(["import-csv", "x.csv"])
        self.assertEqual(ctx.exception.code, 2)

    def test_playlist_id_alone_ok(self):
        args = cli.build_parser().parse_args(["import-csv", "x.csv", "--playlist-id", "A"])
        self.assertEqual(args.playlist_id, "A")
        self.assertIsNone(args.title)

    def test_title_alone_ok(self):
        args = cli.build_parser().parse_args(["import-csv", "x.csv", "--title", "B"])
        self.assertEqual(args.title, "B")
        self.assertIsNone(args.playlist_id)


if __name__ == "__main__":
    unittest.main()
