import unittest
from unittest.mock import patch

from spotify_playlister import cli


class CliTests(unittest.TestCase):
    def test_main_handles_keyboard_interrupt(self):
        with patch.object(cli, "load_env", side_effect=KeyboardInterrupt):
            self.assertEqual(cli.main(["playlists"]), 130)


if __name__ == "__main__":
    unittest.main()
