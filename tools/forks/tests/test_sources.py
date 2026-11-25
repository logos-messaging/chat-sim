import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]


class Sources(unittest.TestCase):
    def test_no_source_file_holds_a_raw_non_ascii_character(self):
        # A character a viewer cannot show, or breaks a line at, is written as an escape.
        for source in sorted({*TOOL.rglob("*.py"), TOOL / "README.md"}):
            with self.subTest(source=str(source.relative_to(TOOL))):
                self.assertTrue(source.read_bytes().isascii())


if __name__ == "__main__":
    unittest.main()
