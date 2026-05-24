import os
import unittest

from spotify_playlister.youtube import YouTubeError, _client_config_from_env


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


if __name__ == "__main__":
    unittest.main()
