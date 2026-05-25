import tempfile
import unittest
from pathlib import Path

from spotify_playlister.models import PlaylistTrack
from spotify_playlister.sync import MatchStore, sync_playlist, sync_spotify_from_youtube, track_key
from spotify_playlister.youtube import YouTubeMatch, YouTubePlaylistItem, YouTubeVideo


class SyncTests(unittest.TestCase):
    def test_track_key_prefers_spotify_track_id(self):
        self.assertEqual(track_key(_track(track_id="spotify-id", isrc="isrc")), "spotify:spotify-id")

    def test_match_store_round_trip(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = MatchStore(Path(tempdir) / "sync.sqlite")
            track = _track()
            store.set(YouTubeMatch(track, "video-id", "Video", "Channel", "https://www.youtube.com/watch?v=video-id"))

            match = store.get(track)
            store.close()

        self.assertIsNotNone(match)
        self.assertEqual(match.video_id, "video-id")
        self.assertEqual(match.track.track_name, "Song 1")

    def test_match_store_reverse_lookup(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = MatchStore(Path(tempdir) / "sync.sqlite")
            track = _track(track_id="spotify-id")
            store.set(YouTubeMatch(track, "video-id", "Video", "Channel", "https://www.youtube.com/watch?v=video-id"))

            match = store.get_by_youtube_video_id("video-id")
            store.close()

        self.assertIsNotNone(match)
        self.assertEqual(match.track.track_id, "spotify-id")
        self.assertEqual(match.video_id, "video-id")

    def test_sync_playlist_uses_cached_matches_and_adds_missing(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = MatchStore(Path(tempdir) / "sync.sqlite")
            client = FakeYouTubeClient(existing_video_ids=set(), search_results=[])
            track = _track()
            store.set(YouTubeMatch(track, "video-id", "Video", "Channel", "https://www.youtube.com/watch?v=video-id"))

            result = sync_playlist(client, [track], "playlist-id", store, dry_run=False, delay_seconds=0)
            store.close()

        self.assertEqual(client.searches, [])
        self.assertEqual(client.added, [("playlist-id", "video-id")])
        self.assertEqual(len(result.added), 1)

    def test_sync_playlist_skips_existing_videos(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = MatchStore(Path(tempdir) / "sync.sqlite")
            client = FakeYouTubeClient(existing_video_ids={"video-id"}, search_results=[])
            track = _track()
            store.set(YouTubeMatch(track, "video-id", "Video", "Channel", "https://www.youtube.com/watch?v=video-id"))

            result = sync_playlist(client, [track], "playlist-id", store, dry_run=False, delay_seconds=0)
            store.close()

        self.assertEqual(client.added, [])
        self.assertEqual(len(result.already_present), 1)

    def test_sync_playlist_searches_uncached_tracks_and_stores_match(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = MatchStore(Path(tempdir) / "sync.sqlite")
            client = FakeYouTubeClient(
                existing_video_ids=set(),
                search_results=[YouTubeVideo("video-id", "Video", "Channel", "https://www.youtube.com/watch?v=video-id")],
            )
            track = _track()

            result = sync_playlist(client, [track], "playlist-id", store, dry_run=True, delay_seconds=0)
            cached = store.get(track)
            store.close()

        self.assertEqual(client.searches, [track.youtube_query])
        self.assertEqual(client.added, [])
        self.assertEqual(len(result.added), 1)
        self.assertIsNotNone(cached)
        self.assertEqual(cached.video_id, "video-id")

    def test_sync_playlist_dry_run_does_not_plan_duplicate_videos(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = MatchStore(Path(tempdir) / "sync.sqlite")
            client = FakeYouTubeClient(existing_video_ids=set(), search_results=[])
            first = _track(position=1, track_id="one")
            second = _track(position=2, track_id="two")
            store.set(YouTubeMatch(first, "video-id", "Video", "Channel", "https://www.youtube.com/watch?v=video-id"))
            store.set(YouTubeMatch(second, "video-id", "Video", "Channel", "https://www.youtube.com/watch?v=video-id"))

            result = sync_playlist(client, [first, second], "playlist-id", store, dry_run=True, delay_seconds=0)
            store.close()

        self.assertEqual(len(result.added), 1)
        self.assertEqual(len(result.already_present), 1)

    def test_sync_spotify_from_youtube_uses_cached_match(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = MatchStore(Path(tempdir) / "sync.sqlite")
            spotify = FakeSpotifyClient(existing_tracks=[])
            youtube = FakeYouTubePlaylistClient([YouTubePlaylistItem("item-id", "video-id", "Video title")])
            track = _track(track_id="spotify-id")
            store.set(YouTubeMatch(track, "video-id", "Video title", "Channel", "https://www.youtube.com/watch?v=video-id"))

            result = sync_spotify_from_youtube(spotify, youtube, "spotify-playlist", "youtube-playlist", store, dry_run=False)
            store.close()

        self.assertEqual(spotify.searches, [])
        self.assertEqual(spotify.added, [("spotify-playlist", ["spotify-id"])])
        self.assertEqual(len(result.added), 1)

    def test_sync_spotify_from_youtube_skips_uncached_item_by_default(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = MatchStore(Path(tempdir) / "sync.sqlite")
            spotify = FakeSpotifyClient(existing_tracks=[], search_results=[_track(track_id="spotify-id")])
            youtube = FakeYouTubePlaylistClient([YouTubePlaylistItem("item-id", "video-id", "Video title")])

            result = sync_spotify_from_youtube(spotify, youtube, "spotify-playlist", "youtube-playlist", store, dry_run=True)
            cached = store.get_by_youtube_video_id("video-id")
            store.close()

        self.assertEqual(spotify.searches, [])
        self.assertEqual(spotify.added, [])
        self.assertEqual(len(result.added), 0)
        self.assertEqual(len(result.missing), 1)
        self.assertIsNone(cached)

    def test_sync_spotify_from_youtube_searches_uncached_item_when_enabled(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = MatchStore(Path(tempdir) / "sync.sqlite")
            spotify = FakeSpotifyClient(existing_tracks=[], search_results=[_track(track_id="spotify-id")])
            youtube = FakeYouTubePlaylistClient([YouTubePlaylistItem("item-id", "video-id", "Video title")])

            result = sync_spotify_from_youtube(
                spotify,
                youtube,
                "spotify-playlist",
                "youtube-playlist",
                store,
                dry_run=True,
                allow_spotify_search=True,
            )
            cached = store.get_by_youtube_video_id("video-id")
            store.close()

        self.assertEqual(spotify.searches, ["Video title"])
        self.assertEqual(spotify.added, [])
        self.assertEqual(len(result.added), 1)
        self.assertIsNotNone(cached)
        self.assertEqual(cached.track.track_id, "spotify-id")

    def test_sync_spotify_from_youtube_skips_existing_spotify_tracks(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = MatchStore(Path(tempdir) / "sync.sqlite")
            spotify = FakeSpotifyClient(existing_tracks=[_track(track_id="spotify-id")])
            youtube = FakeYouTubePlaylistClient([YouTubePlaylistItem("item-id", "video-id", "Video title")])
            store.set(YouTubeMatch(_track(track_id="spotify-id"), "video-id", "Video title", "Channel", "https://www.youtube.com/watch?v=video-id"))

            result = sync_spotify_from_youtube(spotify, youtube, "spotify-playlist", "youtube-playlist", store, dry_run=False)
            store.close()

        self.assertEqual(spotify.added, [])
        self.assertEqual(len(result.already_present), 1)


class FakeYouTubeClient:
    def __init__(self, existing_video_ids, search_results):
        self.existing_video_ids = existing_video_ids
        self.search_results = list(search_results)
        self.searches = []
        self.added = []

    def playlist_video_ids(self, playlist_id):
        return set(self.existing_video_ids)

    def search_video(self, query):
        self.searches.append(query)
        return self.search_results.pop(0)

    def add_video(self, playlist_id, video_id):
        self.added.append((playlist_id, video_id))


class FakeYouTubePlaylistClient:
    def __init__(self, playlist_items):
        self._playlist_items = playlist_items

    def playlist_items(self, playlist_id):
        return list(self._playlist_items)


class FakeSpotifyClient:
    def __init__(self, existing_tracks, search_results=None):
        self.existing_tracks = existing_tracks
        self.search_results = list(search_results or [])
        self.searches = []
        self.added = []

    def playlist_tracks(self, playlist_id):
        return list(self.existing_tracks)

    def search_tracks(self, query, limit=1):
        self.searches.append(query)
        return [self.search_results.pop(0)] if self.search_results else []

    def add_tracks(self, playlist_id, track_ids):
        self.added.append((playlist_id, track_ids))


def _track(position=1, track_id="track-id", isrc=""):
    return PlaylistTrack(
        position=position,
        added_at="",
        is_local=False,
        track_id=track_id,
        track_name=f"Song {position}",
        artists=("Artist",),
        album_name="Album",
        duration_ms=1000,
        explicit=False,
        release_date="2026",
        isrc=isrc,
        spotify_url="",
    )


if __name__ == "__main__":
    unittest.main()
