import os
import unittest

from spotify_playlister.models import PlaylistTrack
from spotify_playlister.youtube import YouTubeError, YouTubeRateLimitError, YouTubeVideo, _client_config_from_env, _youtube_redirect, match_tracks


class YouTubeTests(unittest.TestCase):
    def test_client_config_from_env(self):
        old_client_id = os.environ.get("YOUTUBE_CLIENT_ID")
        old_client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
        os.environ["YOUTUBE_CLIENT_ID"] = "client-id"
        os.environ["YOUTUBE_CLIENT_SECRET"] = "client-secret"
        try:
            config = _client_config_from_env()
        finally:
            if old_client_id is None:
                os.environ.pop("YOUTUBE_CLIENT_ID", None)
            else:
                os.environ["YOUTUBE_CLIENT_ID"] = old_client_id
            if old_client_secret is None:
                os.environ.pop("YOUTUBE_CLIENT_SECRET", None)
            else:
                os.environ["YOUTUBE_CLIENT_SECRET"] = old_client_secret

        self.assertEqual(config["installed"]["client_id"], "client-id")
        self.assertEqual(config["installed"]["client_secret"], "client-secret")
        self.assertEqual(config["installed"]["token_uri"], "https://oauth2.googleapis.com/token")
        self.assertEqual(config["installed"]["redirect_uris"], ["http://localhost:8766/"])

    def test_client_config_from_env_requires_values(self):
        old_client_id = os.environ.pop("YOUTUBE_CLIENT_ID", None)
        old_client_secret = os.environ.pop("YOUTUBE_CLIENT_SECRET", None)
        try:
            with self.assertRaises(YouTubeError):
                _client_config_from_env()
        finally:
            if old_client_id is not None:
                os.environ["YOUTUBE_CLIENT_ID"] = old_client_id
            if old_client_secret is not None:
                os.environ["YOUTUBE_CLIENT_SECRET"] = old_client_secret

    def test_youtube_redirect_rejects_non_localhost(self):
        old_redirect_uri = os.environ.get("YOUTUBE_REDIRECT_URI")
        os.environ["YOUTUBE_REDIRECT_URI"] = "https://example.com/callback"
        try:
            with self.assertRaises(YouTubeError):
                _youtube_redirect()
        finally:
            if old_redirect_uri is None:
                os.environ.pop("YOUTUBE_REDIRECT_URI", None)
            else:
                os.environ["YOUTUBE_REDIRECT_URI"] = old_redirect_uri

    def test_match_tracks_retries_once_after_rate_limit(self):
        client = FakeYouTubeClient(
            [
                YouTubeRateLimitError("rate limited"),
                YouTubeVideo("video-id", "Video", "Channel", "https://www.youtube.com/watch?v=video-id"),
            ]
        )
        sleeps = []

        matches = match_tracks(
            client,
            [_track()],
            delay_seconds=0,
            rate_limit_retry_seconds=65,
            sleep=sleeps.append,
        )

        self.assertEqual(sleeps, [65])
        self.assertEqual(matches[0].video_id, "video-id")

    def test_match_tracks_delays_between_searches(self):
        client = FakeYouTubeClient(
            [
                YouTubeVideo("one", "One", "Channel", "https://www.youtube.com/watch?v=one"),
                YouTubeVideo("two", "Two", "Channel", "https://www.youtube.com/watch?v=two"),
            ]
        )
        sleeps = []

        match_tracks(client, [_track(1), _track(2)], delay_seconds=2, sleep=sleeps.append)

        self.assertEqual(sleeps, [2])


class FakeYouTubeClient:
    def __init__(self, responses):
        self.responses = list(responses)

    def search_video(self, query):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _track(position=1):
    return PlaylistTrack(
        position=position,
        added_at="",
        is_local=False,
        track_id=f"track-{position}",
        track_name=f"Song {position}",
        artists=("Artist",),
        album_name="Album",
        duration_ms=1000,
        explicit=False,
        release_date="2026",
        isrc="",
        spotify_url="",
    )


if __name__ == "__main__":
    unittest.main()
