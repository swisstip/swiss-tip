import os
import unittest
from unittest.mock import patch

from swisstip.admin_console.app import main


class MainTests(unittest.TestCase):
    def test_a_missing_packs_directory_is_a_usage_error(self):
        # The checkout holds no pack: the packs repository is named by --packs-dir or SWISSTIP_PACKS.
        with patch.dict(os.environ, {"SWISSTIP_PACKS": ""}):
            self.assertEqual(main(["--read-only"]), 2)


if __name__ == "__main__":
    unittest.main()
