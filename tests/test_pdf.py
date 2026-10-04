import pytest

from apps.documents import pdf


def test_font_css_embeds_fonts_without_network():
    css = pdf.font_css()
    assert "@font-face" in css and "data:font/woff2;base64," in css


def test_launch_falls_back_to_explicit_executable(monkeypatch):
    calls = []

    class FakeChromium:
        def launch(self, executable_path=None):
            calls.append(executable_path)
            if executable_path is None:
                raise RuntimeError("Executable doesn't exist at X")
            return "browser"

    class FakeP:
        chromium = FakeChromium()

    monkeypatch.setattr(pdf, "_browser_candidates", lambda: ["C:/a/chrome-headless-shell.exe"])
    assert pdf._launch(FakeP()) == "browser"
    assert calls == [None, "C:/a/chrome-headless-shell.exe"]

    monkeypatch.setattr(pdf, "_browser_candidates", list)
    with pytest.raises(pdf.PdfRenderError):
        pdf._launch(FakeP())
