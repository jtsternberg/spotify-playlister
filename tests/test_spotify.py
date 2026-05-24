import unittest

from spotify_playlister.spotify import extract_playlist_id, parse_playlist_item


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


if __name__ == "__main__":
    unittest.main()
