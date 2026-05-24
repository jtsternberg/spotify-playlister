from io import StringIO
import unittest

from spotify_playlister.csv_export import write_tracks_csv
from spotify_playlister.models import PlaylistTrack


class CsvExportTests(unittest.TestCase):
    def test_write_tracks_csv(self):
        output = StringIO()
        write_tracks_csv(
            [
                PlaylistTrack(
                    position=1,
                    added_at="2026-05-24T00:00:00Z",
                    is_local=False,
                    track_id="abc",
                    track_name="Song",
                    artists=("Artist",),
                    album_name="Album",
                    duration_ms=61000,
                    explicit=False,
                    release_date="2026",
                    isrc="ISRC",
                    spotify_url="https://open.spotify.com/track/abc",
                )
            ],
            output,
        )

        self.assertIn("track_name,artists", output.getvalue())
        self.assertIn("Song,Artist", output.getvalue())
        self.assertIn("1:01", output.getvalue())


if __name__ == "__main__":
    unittest.main()
