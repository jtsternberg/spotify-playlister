import unittest

from spotify_playlister.spotify import SpotifyClient, extract_playlist_id, parse_playlist_item, parse_spotify_track


class SpotifyTests(unittest.TestCase):
    def test_extract_playlist_id_from_url(self):
        self.assertEqual(extract_playlist_id("https://open.spotify.com/playlist/abc123?si=xyz"), "abc123")

    def test_extract_playlist_id_from_uri(self):
        self.assertEqual(extract_playlist_id("spotify:playlist:abc123"), "abc123")

    def test_parse_playlist_item(self):
        track = parse_playlist_item(
            {
                "added_at": "2026-05-24T00:00:00Z",
                "is_local": False,
                "track": {
                    "type": "track",
                    "id": "track-id",
                    "name": "Song",
                    "duration_ms": 185000,
                    "explicit": True,
                    "external_urls": {"spotify": "https://open.spotify.com/track/track-id"},
                    "external_ids": {"isrc": "US123"},
                    "artists": [{"name": "Artist A"}, {"name": "Artist B"}],
                    "album": {"name": "Album", "release_date": "2026-01-01"},
                },
            },
            1,
        )

        self.assertIsNotNone(track)
        self.assertEqual(track.position, 1)
        self.assertEqual(track.artists_text, "Artist A; Artist B")
        self.assertEqual(track.duration, "3:05")
        self.assertEqual(track.youtube_query, "Song Artist A; Artist B Album")

    def test_parse_spotify_track(self):
        track = parse_spotify_track(
            {
                "type": "track",
                "id": "track-id",
                "name": "Song",
                "duration_ms": 61000,
                "external_urls": {"spotify": "https://open.spotify.com/track/track-id"},
                "external_ids": {"isrc": "US123"},
                "artists": [{"name": "Artist"}],
                "album": {"name": "Album", "release_date": "2026"},
            },
            1,
        )

        self.assertIsNotNone(track)
        self.assertEqual(track.track_id, "track-id")
        self.assertEqual(track.spotify_url, "https://open.spotify.com/track/track-id")

    def test_has_required_scopes(self):
        client = SpotifyClient("client-id", scopes="playlist-read-private playlist-modify-private")

        self.assertTrue(client._has_required_scopes({"scope": "playlist-read-private playlist-modify-private"}))
        self.assertFalse(client._has_required_scopes({"scope": "playlist-read-private"}))


if __name__ == "__main__":
    unittest.main()
