from pypdf import PdfReader

from bgconvertor import profilepdf


def test_profile_digital_pdf(ab_pdf):
    reader = PdfReader(ab_pdf)
    p1 = profilepdf.profile_page(reader, 1)
    assert p1["page"] == 1
    assert p1["has_text_layer"]
    assert "ROMANIA" in p1["sample"]
    assert p1["width_pts"] > 0


def test_render_page(ab_pdf):
    img = profilepdf.render_page(ab_pdf, 1, scale=0.5)
    assert img.size[0] > 100


def test_summarize():
    profiles = [
        {"has_text_layer": True},
        {"has_text_layer": False},
        {"has_text_layer": False},
    ]
    s = profilepdf.summarize(profiles)
    assert s == {
        "pages": 3,
        "pages_with_text_layer": 1,
        "pages_scanned": 2,
        "mostly_scanned": True,
    }
