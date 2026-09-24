import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swisstip.admin_console.app import main


class MainTests(unittest.TestCase):
    def test_a_missing_packs_directory_is_a_usage_error(self):
        # The checkout holds no pack: the packs repository is named by --packs-dir or SWISSTIP_PACKS.
        with patch.dict(os.environ, {"SWISSTIP_PACKS": ""}):
            self.assertEqual(main(["--read-only"]), 2)

    def test_a_non_loopback_bind_requires_authentication(self):
        with tempfile.TemporaryDirectory() as directory, patch("uvicorn.run") as serve:
            self.assertEqual(main(["--packs-dir", directory, "--host", "0.0.0.0"]), 2)
            serve.assert_not_called()
            auth = Path(directory) / "users.txt"
            auth.write_text("anna:" + "0" * 64 + ":editor\n", encoding="utf-8")
            self.assertEqual(main(["--packs-dir", directory, "--host", "0.0.0.0",
                                   "--auth-file", str(auth)]), 0)
            serve.assert_called_once()


if __name__ == "__main__":
    unittest.main()
