import unittest
from io import StringIO
from unittest.mock import patch

from spotify_playlister import cli


class CliTests(unittest.TestCase):
    def test_main_handles_keyboard_interrupt(self):
        with patch.object(cli, "load_env", side_effect=KeyboardInterrupt):
            self.assertEqual(cli.main(["playlists"]), 130)

    def test_list_playlists_includes_spotify_url(self):
        spotify = FakeSpotifyClient(
            [
                {
                    "id": "playlist-id",
                    "name": "Playlist",
                    "owner": {"display_name": "Owner"},
                    "external_urls": {"spotify": "https://open.spotify.com/playlist/playlist-id"},
                }
            ]
        )
        output = StringIO()

        with patch("sys.stdout", output):
            self.assertEqual(cli.list_playlists(spotify), 0)

        self.assertEqual(output.getvalue(), "playlist-id\tPlaylist\tOwner\thttps://open.spotify.com/playlist/playlist-id\n")


class FakeSpotifyClient:
    def __init__(self, playlists):
        self._playlists = playlists

    def list_playlists(self):
        return self._playlists


if __name__ == "__main__":
    unittest.main()
