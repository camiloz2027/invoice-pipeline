import matplotlib
import pytest
from PIL import Image

from invoice_pipeline.pick_boxes import BoxPicker, to_box
from invoice_pipeline.vendors import load_boxes

matplotlib.use("Agg")  # no window during the tests


def test_to_box_turns_pixels_into_fractions():
    assert to_box((100, 300, 50, 150), 400, 200) == (0.25, 0.25, 0.75, 0.75)


def test_to_box_keeps_the_box_inside_the_page():
    assert to_box((-10, 500, -5, 250), 400, 200) == (0, 0, 1, 1)


@pytest.fixture
def picker(tmp_path):
    page = tmp_path / "images" / "invoice" / "page_001.png"
    page.parent.mkdir(parents=True)
    Image.new("RGB", (400, 200), "white").save(page)
    return BoxPicker("vendor", tmp_path / "vendors", [page])


def test_save_writes_the_drawn_box(picker, tmp_path):
    picker.selector.extents = (100, 300, 50, 150)
    picker.has_selection = True

    picker.save("body_box")

    assert load_boxes(tmp_path / "vendors", "vendor") == {"body_box": (0.25, 0.25, 0.75, 0.75)}


def test_save_without_a_box_saves_nothing(picker, tmp_path):
    picker.save("id_box")

    assert load_boxes(tmp_path / "vendors", "vendor") == {}
