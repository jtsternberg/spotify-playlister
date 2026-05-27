import unittest

from spotify_playlister.sync import SyncedPlaylist
from spotify_playlister.web import INDEX_HTML, _playlist_payload


class WebTests(unittest.TestCase):
    def test_playlist_payload_includes_service_urls(self):
        playlist = SyncedPlaylist(
            id=1,
            spotify_playlist_id="spotify-id",
            spotify_name="Spotify Name",
            youtube_playlist_id="youtube-id",
            youtube_title="YouTube Title",
            notes="",
            last_synced_at=None,
            created_at=1,
            updated_at=1,
        )

        payload = _playlist_payload(playlist)

        self.assertEqual(payload["spotify_url"], "https://open.spotify.com/playlist/spotify-id")
        self.assertEqual(payload["youtube_url"], "https://www.youtube.com/playlist?list=youtube-id")

    def test_playlist_render_links_service_urls(self):
        self.assertIn("spotifyPlaylistLink(p)", INDEX_HTML)
        self.assertIn("youtubePlaylistLink(p)", INDEX_HTML)
        self.assertIn("playlist.spotify_url", INDEX_HTML)
        self.assertIn("playlist.youtube_url", INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
