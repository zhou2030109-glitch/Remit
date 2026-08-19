"""Regression tests for Bookshelf VLSI attachment support."""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
FRONTEND_STEPPER = PROJECT_ROOT / "frontend" / "src" / "components" / "UserStepper.vue"
FRONTEND_DATA_VIEW = (
    PROJECT_ROOT
    / "frontend"
    / "src"
    / "pages"
    / "task"
    / "components"
    / "ProjectDataView.vue"
)
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.data_scout import build_data_profile, summarize_data_profile  # noqa: E402
from app.utils.common_utils import get_current_files  # noqa: E402
from app.utils.file_types import is_sandbox_upload_file  # noqa: E402


class BookshelfFrontendTests(unittest.TestCase):
    def test_file_picker_accepts_all_bookshelf_attachments(self) -> None:
        source = FRONTEND_STEPPER.read_text(encoding="utf-8")
        accept_match = re.search(r'ACCEPTED_DATA_EXTENSIONS\s*=\s*"([^"]+)"', source)
        self.assertIsNotNone(accept_match)
        accepted_suffixes = {
            suffix.strip().lower() for suffix in accept_match.group(1).split(",")
        }
        self.assertTrue({".blocks", ".nets", ".pl"} <= accepted_suffixes)

    def test_project_data_view_lists_bookshelf_attachments(self) -> None:
        source = FRONTEND_DATA_VIEW.read_text(encoding="utf-8")
        for suffix in ("blocks", "nets", "pl"):
            self.assertIn(f'"{suffix}"', source)


class BookshelfBackendTests(unittest.TestCase):
    def test_discovery_and_sandbox_upload_include_bookshelf_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            folder = Path(temp_directory)
            for filename in ("n100.blocks", "n100.nets", "n100.pl", "ignore.pdf"):
                (folder / filename).touch()

            discovered = set(get_current_files(str(folder), "data"))

        self.assertEqual(discovered, {"n100.blocks", "n100.nets", "n100.pl"})
        for filename in discovered:
            self.assertTrue(is_sandbox_upload_file(filename))

    def test_data_scout_recognizes_bookshelf_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            folder = Path(temp_directory)
            (folder / "n2.blocks").write_text(
                """UCLA blocks 1.0
NumHardBlocks : 2
NumTerminals : 1

b0 block 4 (0, 0) (0, 10) (20, 10) (20, 0)
b1 hardrectilinear 4 (0, 0) (0, 5) (8, 5) (8, 0)
p1 terminal
""",
                encoding="utf-8",
            )
            (folder / "n2.nets").write_text(
                """UCLA nets 1.0
NumNets : 2
NumPins : 5
NetDegree : 2
p1
b0
NetDegree : 3
b0
b1
p1
""",
                encoding="utf-8",
            )
            (folder / "n2.pl").write_text(
                """UCLA pl 1.0
p1 0 0 : N /FIXED
b0 10 20 : N
b1 40 50 : S
""",
                encoding="utf-8",
            )

            profile = build_data_profile(folder)

        self.assertEqual(profile["status"], "completed")
        by_format = {item["format"]: item for item in profile["files"]}
        self.assertEqual(
            set(by_format),
            {"bookshelf_blocks", "bookshelf_nets", "bookshelf_placement"},
        )

        blocks = by_format["bookshelf_blocks"]
        self.assertEqual(blocks["statistics"]["parsed_hard_blocks"], 2)
        self.assertEqual(blocks["statistics"]["parsed_terminals"], 1)
        self.assertEqual(blocks["sample_rows"][0]["area"], 200.0)

        nets = by_format["bookshelf_nets"]
        self.assertEqual(nets["statistics"]["parsed_nets"], 2)
        self.assertEqual(nets["statistics"]["parsed_pins"], 5)

        placement = by_format["bookshelf_placement"]
        self.assertEqual(placement["statistics"]["parsed_placements"], 3)
        self.assertEqual(placement["statistics"]["fixed_placements"], 1)
        self.assertEqual(
            placement["coordinate_bounds"],
            {"min_x": 0.0, "max_x": 40.0, "min_y": 0.0, "max_y": 50.0},
        )

        summary = summarize_data_profile(profile)
        self.assertIn("Bookshelf 块定义", summary)
        self.assertIn("Bookshelf 网络定义", summary)
        self.assertIn("Bookshelf 布局坐标", summary)

    def test_data_scout_profiles_all_nine_companion_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            folder = Path(temp_directory)
            for size in (100, 200, 300):
                (folder / f"n{size}.blocks").write_text(
                    "NumHardBlocks : 1\nNumTerminals : 0\n"
                    "b0 block 4 (0, 0) (0, 1) (1, 1) (1, 0)\n",
                    encoding="utf-8",
                )
                (folder / f"n{size}.nets").write_text(
                    "NumNets : 1\nNumPins : 2\nNetDegree : 2\nb0\nb1\n",
                    encoding="utf-8",
                )
                (folder / f"n{size}.pl").write_text(
                    "b0 0 0\n",
                    encoding="utf-8",
                )

            profile = build_data_profile(folder)

        self.assertEqual(len(profile["discovered_files"]), 9)
        self.assertEqual(len(profile["files"]), 9)
        self.assertEqual(profile["notes"], [])


if __name__ == "__main__":
    unittest.main()
