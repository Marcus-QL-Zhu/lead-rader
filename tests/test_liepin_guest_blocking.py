import pytest

import ht_lead_radar.liepin_guest as liepin_guest


class _BlockedResponse:
    headers = type("Headers", (), {"get_content_charset": lambda self: "utf-8"})()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return b"<title>Liepin safety center</title>"

    def geturl(self):
        return "https://safe.liepin.com/page/liepin/captchaPage_ip_PC"


class _BlockedOpener:
    def open(self, request, timeout):
        return _BlockedResponse()


def test_fetch_rejects_captcha_page_instead_of_returning_empty_success(monkeypatch):
    monkeypatch.setattr(liepin_guest, "build_opener", lambda *args: _BlockedOpener())
    with pytest.raises(liepin_guest.LiepinAccessBlocked):
        liepin_guest.fetch_public_page("https://m.liepin.com/company/1/")
