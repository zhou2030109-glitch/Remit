"""赛题 PDF 解析回归测试。"""

from __future__ import annotations

import unittest

import pymupdf

from app.utils.pdf_parser import PdfParseError, parse_problem_pdf_bytes


class ProblemPdfParserTests(unittest.TestCase):
    @staticmethod
    def _pdf_bytes(*page_texts: str) -> bytes:
        document = pymupdf.open()
        for text in page_texts:
            page = document.new_page()
            if text:
                page.insert_text((72, 72), text)
        content = document.tobytes()
        document.close()
        return content

    @staticmethod
    def _structured_pdf_bytes() -> bytes:
        """Create the table-and-fraction layout that previously became flat text."""
        document = pymupdf.open()
        page = document.new_page()

        columns = (54, 100, 260, 306, 540)
        rows = (72, 98, 124)
        for x in columns:
            page.draw_line((x, rows[0]), (x, rows[-1]))
        for y in rows:
            page.draw_line((columns[0], y), (columns[-1], y))

        cells = (
            ("Column", "Description", "Column", "Description"),
            ("A", "Sample ID", "Q", "Chromosome 13 Z score"),
        )
        for row_index, row in enumerate(cells):
            baseline = rows[row_index] + 18
            for column_index, value in enumerate(row):
                page.insert_text(
                    (columns[column_index] + 4, baseline), value, fontsize=9
                )

        page.insert_text((230, 174), "Z =", fontsize=11)
        page.insert_text((268, 164), "X - mu", fontsize=10)
        page.draw_line((266, 168), (310, 168))
        page.insert_text((277, 181), "sigma", fontsize=10)

        content = document.tobytes()
        document.close()
        return content

    def test_extracts_text_from_every_page(self) -> None:
        parsed = parse_problem_pdf_bytes(
            self._pdf_bytes("Problem background", "Problem 1: optimize cost")
        )

        self.assertEqual(parsed.page_count, 2)
        self.assertIn("Problem background", parsed.text)
        self.assertIn("Problem 1: optimize cost", parsed.text)
        self.assertEqual(parsed.char_count, len(parsed.text))

    def test_preserves_table_rows_and_fraction_structure_as_markdown(self) -> None:
        parsed = parse_problem_pdf_bytes(self._structured_pdf_bytes())

        self.assertIn("| Column | Description | Column | Description |", parsed.text)
        self.assertIn("| A | Sample ID | Q | Chromosome 13 Z score |", parsed.text)
        self.assertIn(r"$$Z=\frac{X-\mu}{\sigma}$$", parsed.text)

    def test_rejects_non_pdf_content(self) -> None:
        with self.assertRaisesRegex(PdfParseError, "不是有效的 PDF"):
            parse_problem_pdf_bytes(b"plain text pretending to be a pdf")

    def test_rejects_pdf_without_extractable_text(self) -> None:
        with self.assertRaisesRegex(PdfParseError, "OCR"):
            parse_problem_pdf_bytes(self._pdf_bytes(""))


if __name__ == "__main__":
    unittest.main()
