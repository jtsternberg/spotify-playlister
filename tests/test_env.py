import os
import tempfile
import unittest
from pathlib import Path

from spotify_playlister.env import load_env


class EnvTests(unittest.TestCase):
    def test_load_env_sets_missing_values(self):
        with tempfile.TemporaryDirectory() as tempdir:
            env_path = Path(tempdir) / ".env"
            env_path.write_text(
                """
# comment
SPOTIFY_CLIENT_ID="client-id"
export SPOTIFY_REDIRECT_URI='http://127.0.0.1:8765/callback'
""",
                encoding="utf-8",
            )

            old_client_id = os.environ.pop("SPOTIFY_CLIENT_ID", None)
            old_redirect_uri = os.environ.pop("SPOTIFY_REDIRECT_URI", None)
            try:
                load_env(env_path)
                self.assertEqual(os.environ["SPOTIFY_CLIENT_ID"], "client-id")
                self.assertEqual(os.environ["SPOTIFY_REDIRECT_URI"], "http://127.0.0.1:8765/callback")
            finally:
                os.environ.pop("SPOTIFY_CLIENT_ID", None)
                os.environ.pop("SPOTIFY_REDIRECT_URI", None)
                if old_client_id is not None:
                    os.environ["SPOTIFY_CLIENT_ID"] = old_client_id
                if old_redirect_uri is not None:
                    os.environ["SPOTIFY_REDIRECT_URI"] = old_redirect_uri

    def test_load_env_does_not_override_environment(self):
        with tempfile.TemporaryDirectory() as tempdir:
            env_path = Path(tempdir) / ".env"
            env_path.write_text("SPOTIFY_CLIENT_ID=from-file\n", encoding="utf-8")

            old_client_id = os.environ.get("SPOTIFY_CLIENT_ID")
            os.environ["SPOTIFY_CLIENT_ID"] = "from-shell"
            try:
                load_env(env_path)
                self.assertEqual(os.environ["SPOTIFY_CLIENT_ID"], "from-shell")
            finally:
                if old_client_id is None:
                    os.environ.pop("SPOTIFY_CLIENT_ID", None)
                else:
                    os.environ["SPOTIFY_CLIENT_ID"] = old_client_id


if __name__ == "__main__":
    unittest.main()
