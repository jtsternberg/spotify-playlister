import os
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from spotify_playlister.models import PlaylistTrack
from spotify_playlister.youtube import (
    YouTubeClient,
    YouTubeError,
    YouTubeQuotaExceededError,
    YouTubeRateLimitError,
    YouTubeVideo,
    _client_config_from_env,
    _youtube_redirect,
    extract_playlist_id,
    extract_video_id,
    match_tracks,
    next_quota_reset,
    playlist_url,
    quota_reset_description,
)


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

    def test_extract_video_id_from_watch_url(self):
        self.assertEqual(extract_video_id("https://www.youtube.com/watch?v=abc123&list=playlist"), "abc123")

    def test_extract_video_id_from_short_url(self):
        self.assertEqual(extract_video_id("https://youtu.be/abc123"), "abc123")

    def test_extract_video_id_from_shorts_url(self):
        self.assertEqual(extract_video_id("https://www.youtube.com/shorts/abc123"), "abc123")

    def test_extract_playlist_id_from_playlist_url(self):
        self.assertEqual(extract_playlist_id("https://www.youtube.com/playlist?list=PL123"), "PL123")

    def test_playlist_url(self):
        self.assertEqual(playlist_url("https://www.youtube.com/playlist?list=PL123"), "https://www.youtube.com/playlist?list=PL123")

    def test_set_playlist_privacy_updates_status(self):
        service = FakeYouTubeService()

        privacy = YouTubeClient(service).set_playlist_privacy("playlist-id", "unlisted")

        self.assertEqual(privacy, "unlisted")
        self.assertEqual(service.list_call["part"], "snippet,status")
        self.assertEqual(service.list_call["id"], "playlist-id")
        self.assertEqual(service.update_call["part"], "snippet,status")
        self.assertEqual(
            service.update_call["body"],
            {
                "id": "playlist-id",
                "snippet": {"title": "Existing title", "description": "Existing description"},
                "status": {"privacyStatus": "unlisted"},
            },
        )

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

    def test_search_video_handles_daily_quota_error(self):
        service = FakeSearchService(FakeHttpError(403, "quotaExceeded"))

        with self.assertRaisesRegex(YouTubeQuotaExceededError, r"Daily quota resets in .* at .*"):
            YouTubeClient(service).search_video("Song Artist")

    def test_search_video_handles_per_minute_rate_limit_error(self):
        service = FakeSearchService(FakeHttpError(429, "rateLimitExceeded"))

        with self.assertRaises(YouTubeRateLimitError):
            YouTubeClient(service).search_video("Song Artist")

    def test_playlist_items_handles_daily_quota_error(self):
        service = FakePlaylistItemsService(FakeHttpError(403, "quotaExceeded"))

        with self.assertRaisesRegex(YouTubeQuotaExceededError, r"Daily quota resets in .* at .*"):
            YouTubeClient(service).playlist_items("playlist-id")

    def test_next_quota_reset_uses_midnight_pacific_in_local_timezone(self):
        now = datetime(2026, 5, 24, 19, 8, tzinfo=ZoneInfo("America/New_York"))

        reset = next_quota_reset(now)

        self.assertEqual(reset, datetime(2026, 5, 25, 3, 0, tzinfo=ZoneInfo("America/New_York")))

    def test_quota_reset_description_uses_relative_duration_and_local_time(self):
        now = datetime(2026, 5, 24, 19, 8, tzinfo=ZoneInfo("America/New_York"))

        self.assertEqual(quota_reset_description(now), "in 7hr 52min at 3:00 AM")


class FakeYouTubeClient:
    def __init__(self, responses):
        self.responses = list(responses)

    def search_video(self, query):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeYouTubeService:
    def __init__(self):
        self.list_call = None
        self.update_call = None

    def playlists(self):
        return self

    def list(self, **kwargs):
        self.list_call = kwargs
        return self

    def update(self, **kwargs):
        self.update_call = kwargs
        return self

    def execute(self):
        if self.update_call is None:
            return {
                "items": [
                    {
                        "id": self.list_call["id"],
                        "snippet": {"title": "Existing title", "description": "Existing description"},
                        "status": {"privacyStatus": "private"},
                    }
                ]
            }
        return {"status": {"privacyStatus": self.update_call["body"]["status"]["privacyStatus"]}}


class FakeSearchService:
    def __init__(self, error):
        self.error = error

    def search(self):
        return self

    def list(self, **kwargs):
        return self

    def execute(self):
        raise self.error


class FakePlaylistItemsService:
    def __init__(self, error):
        self.error = error

    def playlistItems(self):
        return self

    def list(self, **kwargs):
        return self

    def execute(self):
        raise self.error


class FakeResponse:
    def __init__(self, status):
        self.status = status


class FakeHttpError(Exception):
    def __init__(self, status, details):
        super().__init__(details)
        self.resp = FakeResponse(status)


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
