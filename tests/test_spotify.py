import unittest
from unittest.mock import patch

from spotify_playlister.spotify import (
    SpotifyApiError,
    SpotifyClient,
    SpotifyError,
    extract_playlist_id,
    extract_track_id,
    parse_playlist_item,
    parse_spotify_track,
)


class SpotifyTests(unittest.TestCase):
    def test_extract_playlist_id_from_url(self):
        self.assertEqual(extract_playlist_id("https://open.spotify.com/playlist/abc123?si=xyz"), "abc123")

    def test_extract_playlist_id_from_uri(self):
        self.assertEqual(extract_playlist_id("spotify:playlist:abc123"), "abc123")

    def test_extract_track_id_from_url(self):
        self.assertEqual(extract_track_id("https://open.spotify.com/track/track123?si=xyz"), "track123")

    def test_extract_track_id_from_uri(self):
        self.assertEqual(extract_track_id("spotify:track:track123"), "track123")

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

    def test_ensure_playlist_writable_allows_owned_playlist(self):
        client = SpotifyClient("client-id")

        with patch.object(client, "playlist", return_value={"owner": {"id": "me"}, "collaborative": False}), patch.object(
            client, "current_user_id", return_value="me"
        ):
            client.ensure_playlist_writable("playlist-id")

    def test_ensure_playlist_writable_allows_collaborative_playlist(self):
        client = SpotifyClient("client-id")

        with patch.object(client, "playlist", return_value={"owner": {"id": "someone-else"}, "collaborative": True}), patch.object(
            client, "current_user_id", return_value="me"
        ):
            client.ensure_playlist_writable("playlist-id")

    def test_ensure_playlist_writable_rejects_unmodifiable_playlist(self):
        client = SpotifyClient("client-id")

        with patch.object(
            client,
            "playlist",
            return_value={"name": "Their Playlist", "owner": {"id": "someone-else", "display_name": "Other User"}, "collaborative": False},
        ), patch.object(client, "current_user_id", return_value="me"):
            with self.assertRaisesRegex(SpotifyError, "owned by Other User"):
                client.ensure_playlist_writable("playlist-id")

    def test_add_tracks_wraps_forbidden_error(self):
        client = SpotifyClient("client-id")

        with patch.object(client, "access_token", return_value="token"), patch("spotify_playlister.spotify._json_request") as request:
            request.side_effect = SpotifyApiError(403, '{"error": {"status": 403}}')

            with self.assertRaisesRegex(SpotifyError, "Spotify refused to add tracks"):
                client.add_tracks("playlist-id", ["track-id"])

    def test_add_tracks_uses_current_playlist_items_endpoint(self):
        client = SpotifyClient("client-id")

        with patch.object(client, "access_token", return_value="token"), patch("spotify_playlister.spotify._json_request") as request:
            client.add_tracks("playlist-id", ["track-id"])

        request.assert_called_once()
        self.assertEqual(request.call_args.args[0], "https://api.spotify.com/v1/playlists/playlist-id/items")
        self.assertEqual(request.call_args.kwargs["json_data"], {"uris": ["spotify:track:track-id"]})


if __name__ == "__main__":
    unittest.main()
