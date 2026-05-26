import tempfile
import unittest
from pathlib import Path

from spotify_playlister.models import PlaylistTrack
from spotify_playlister.sync import (
    MatchStore,
    SyncError,
    remove_spotify_tracks_not_in_youtube,
    remove_youtube_items_not_in_spotify,
    sync_playlist,
    sync_spotify_from_youtube,
    track_key,
)
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

    def test_match_store_playlist_round_trip(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = MatchStore(Path(tempdir) / "sync.sqlite")

            playlist = store.upsert_playlist(
                spotify_playlist_id="spotify-playlist",
                spotify_name="Spotify Name",
                youtube_playlist_id="youtube-playlist",
                youtube_title="YouTube Name",
                notes="paired",
            )
            fetched = store.get_playlist(playlist.id)
            playlists = store.playlists()
            store.close()

        self.assertEqual(fetched.spotify_playlist_id, "spotify-playlist")
        self.assertEqual(playlists[0].youtube_title, "YouTube Name")
        self.assertIsNone(playlists[0].last_synced_at)

    def test_match_store_playlist_upsert_updates_existing_pair(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = MatchStore(Path(tempdir) / "sync.sqlite")

            first = store.upsert_playlist(
                spotify_playlist_id="spotify-playlist",
                spotify_name="Old",
                youtube_playlist_id="youtube-playlist",
                youtube_title="Old",
            )
            second = store.upsert_playlist(
                spotify_playlist_id="spotify-playlist",
                spotify_name="New",
                youtube_playlist_id="youtube-playlist",
                youtube_title="New",
            )
            store.close()

        self.assertEqual(first.id, second.id)
        self.assertEqual(second.spotify_name, "New")

    def test_match_store_delete_match(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = MatchStore(Path(tempdir) / "sync.sqlite")
            track = _track(track_id="spotify-id")
            store.set(YouTubeMatch(track, "video-id", "Video", "Channel", "https://www.youtube.com/watch?v=video-id"))

            deleted = store.delete_match("spotify:spotify-id")
            match = store.get(track)
            store.close()

        self.assertTrue(deleted)
        self.assertIsNone(match)

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

    def test_remove_youtube_items_not_in_spotify_uses_cached_mappings(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = MatchStore(Path(tempdir) / "sync.sqlite")
            keep = _track(track_id="keep")
            store.set(YouTubeMatch(keep, "keep-video", "Keep Video", "Channel", "https://www.youtube.com/watch?v=keep-video"))
            client = FakeYouTubeClient(
                existing_video_ids=set(),
                search_results=[],
                playlist_items=[
                    YouTubePlaylistItem("keep-item", "keep-video", "Keep Video"),
                    YouTubePlaylistItem("old-item", "old-video", "Old Video"),
                ],
            )

            result = remove_youtube_items_not_in_spotify(client, [keep], "playlist-id", store, dry_run=False)
            store.close()

        self.assertEqual(client.removed, ["old-item"])
        self.assertEqual([item.video_id for item in result.removed], ["old-video"])
        self.assertEqual([item.video_id for item in result.kept], ["keep-video"])

    def test_remove_youtube_items_not_in_spotify_dry_run_does_not_delete(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = MatchStore(Path(tempdir) / "sync.sqlite")
            client = FakeYouTubeClient(
                existing_video_ids=set(),
                search_results=[],
                playlist_items=[YouTubePlaylistItem("old-item", "old-video", "Old Video")],
            )

            result = remove_youtube_items_not_in_spotify(client, [], "playlist-id", store, dry_run=True)
            store.close()

        self.assertEqual(client.removed, [])
        self.assertEqual(len(result.removed), 1)

    def test_remove_spotify_tracks_not_in_youtube_uses_cached_mappings(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = MatchStore(Path(tempdir) / "sync.sqlite")
            keep = _track(track_id="keep")
            old = _track(track_id="old")
            store.set(YouTubeMatch(keep, "keep-video", "Keep Video", "Channel", "https://www.youtube.com/watch?v=keep-video"))
            store.set(YouTubeMatch(old, "old-video", "Old Video", "Channel", "https://www.youtube.com/watch?v=old-video"))
            spotify = FakeSpotifyClient(existing_tracks=[keep, old])
            youtube = FakeYouTubePlaylistClient([YouTubePlaylistItem("keep-item", "keep-video", "Keep Video")])

            result = remove_spotify_tracks_not_in_youtube(spotify, youtube, "spotify-playlist", "youtube-playlist", store, dry_run=False)
            store.close()

        self.assertEqual(spotify.removed, [("spotify-playlist", ["old"])])
        self.assertEqual([match.track.track_id for match in result.removed], ["old"])

    def test_remove_youtube_items_not_in_spotify_requires_complete_cache(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = MatchStore(Path(tempdir) / "sync.sqlite")
            uncached = _track(track_id="uncached")
            client = FakeYouTubeClient(
                existing_video_ids=set(),
                search_results=[],
                playlist_items=[YouTubePlaylistItem("old-item", "old-video", "Old Video")],
            )

            with self.assertRaisesRegex(SyncError, "no cached YouTube mapping"):
                remove_youtube_items_not_in_spotify(client, [uncached], "playlist-id", store, dry_run=False)
            store.close()

        self.assertEqual(client.removed, [])


class FakeYouTubeClient:
    def __init__(self, existing_video_ids, search_results, playlist_items=None):
        self.existing_video_ids = existing_video_ids
        self.search_results = list(search_results)
        self.searches = []
        self.added = []
        self.removed = []
        self._playlist_items = playlist_items

    def playlist_video_ids(self, playlist_id):
        return set(self.existing_video_ids)

    def playlist_items(self, playlist_id):
        return list(self._playlist_items or [])

    def search_video(self, query):
        self.searches.append(query)
        return self.search_results.pop(0)

    def add_video(self, playlist_id, video_id):
        self.added.append((playlist_id, video_id))

    def remove_playlist_item(self, playlist_item_id):
        self.removed.append(playlist_item_id)


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
        self.removed = []

    def playlist_tracks(self, playlist_id):
        return list(self.existing_tracks)

    def search_tracks(self, query, limit=1):
        self.searches.append(query)
        return [self.search_results.pop(0)] if self.search_results else []

    def add_tracks(self, playlist_id, track_ids):
        self.added.append((playlist_id, track_ids))

    def remove_tracks(self, playlist_id, track_ids):
        self.removed.append((playlist_id, track_ids))


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
