from __future__ import annotations

import unittest

from dpl_export.xlsx_reader import Cell, Sheet, row_marker


def colored_row(*, yellow: int = 0, orange_columns: tuple[int, ...] = ()) -> Sheet:
    rows = {
        5: {
            column: Cell(
                value="x",
                formula=None,
                style_id=0,
                fill_rgb="FF9900" if column in orange_columns else ("FFFF00" if column < yellow else None),
            )
            for column in range(58)
        }
    }
    return Sheet(name="PWK_Analogwerte", rows=rows)


class RowMarkerTest(unittest.TestCase):
    def test_yellow_row_with_one_orange_cell_is_special(self) -> None:
        self.assertEqual("yellow_with_orange", row_marker(colored_row(yellow=58, orange_columns=(8,)), 5))

    def test_single_orange_cell_is_included_even_without_yellow_row(self) -> None:
        self.assertEqual("orange_changed", row_marker(colored_row(orange_columns=(8,)), 5))

    def test_unmarked_row_is_ignored(self) -> None:
        self.assertIsNone(row_marker(colored_row(), 5))


if __name__ == "__main__":
    unittest.main()
