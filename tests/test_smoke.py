"""picture_pokemon smoke: composer renders cells across every supported
size with mocked TCGdex responses, no network call; plus the three pick
modes, the image-less filter, held picks, the local image store, and the
friendly errors."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient
from PIL import Image

from plugins.picture_pokemon import server

API = f"{server.API_ROOT}/{server.LANG}"
ASSETS = "https://assets.tcgdex.net/en"


def _brief(card_id: str, name: str, image: bool = True) -> dict[str, str]:
    set_id, _, local = card_id.rpartition("-")
    out = {"id": card_id, "localId": local, "name": name}
    if image:
        out["image"] = f"{ASSETS}/x/{set_id}/{local}"
    return out


_ALL = [
    _brief("swsh4.5-14", "Gossifleur"),
    _brief("2024sv-1", "Charizard", image=False),
]
_PIKACHU = [
    _brief("base1-58", "Pikachu"),
    _brief("swsh4-44", "Pikachu VMAX"),
    _brief("mcd19-6", "Pikachu", image=False),
]
_SERIES = {
    "id": "swsh",
    "name": "Sword & Shield",
    "sets": [
        {"id": "swsh4.5", "name": "Shining Fates", "cardCount": {"total": 73}},
        {"id": "empty", "name": "Nothing", "cardCount": {"total": 0}},
    ],
}
_SET = {
    "id": "swsh4.5",
    "name": "Shining Fates",
    "cardCount": {"official": 72, "total": 73},
    "cards": [_brief("swsh4.5-14", "Gossifleur")],
}


def _detail(card_id: str, name: str) -> dict[str, Any]:
    return {
        "id": card_id,
        "name": name,
        "category": "Pokemon",
        "rarity": "Common",
        "illustrator": "Yukiko Baba",
        "hp": 60,
        "types": ["Grass"],
        "stage": "Basic",
        "set": {"id": "swsh4.5", "name": "Shining Fates", "cardCount": {"official": 72}},
    }


def _webp() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (600, 825), (240, 200, 40, 255)).save(buf, "WEBP")
    return buf.getvalue()


class _FakeResp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a) -> bool:
        return False


def _j(value: Any) -> bytes:
    return json.dumps(value).encode()


def _router(responses: dict[str, bytes | Exception], seen: list[str] | None = None):
    """Longest matching prefix wins, so a specific URL beats a catch-all."""

    def urlopen(req: Any, timeout: float | None = None) -> _FakeResp:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if seen is not None:
            seen.append(url)
        matches = [p for p in responses if url.startswith(p)]
        if not matches:
            raise AssertionError(f"unexpected request: {url}")
        body = responses[max(matches, key=len)]
        if isinstance(body, Exception):
            raise body
        return _FakeResp(body)

    return urlopen


_LIVE: dict[str, bytes | Exception] = {
    f"{API}/cards?": _j(_PIKACHU),
    f"{API}/cards/": _j(_detail("swsh4.5-14", "Gossifleur")),
    f"{API}/series/swsh": _j(_SERIES),
    f"{API}/sets/swsh4.5": _j(_SET),
    f"{ASSETS}/": _webp(),
}
_LIVE[f"{API}/cards"] = _j(_ALL)


def _fetch(
    tmp_path: Path,
    responses: dict[str, bytes | Exception],
    seen: list[str] | None = None,
    **options: Any,
) -> dict[str, Any]:
    with patch("urllib.request.urlopen", side_effect=_router(responses, seen)):
        return server.fetch(options, {}, ctx={"data_dir": str(tmp_path)})


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_pokemon_renders(client: FlaskClient, size: str) -> None:
    with patch("urllib.request.urlopen", side_effect=_router(_LIVE)):
        resp = client.get(f"/_test/render?plugin=picture_pokemon&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="picture_pokemon"' in body
    assert "Gossifleur" in body
    assert "/plugins/picture_pokemon/cards/" in body
    assert "Shining Fates" in body


def test_any_mode_skips_cards_without_a_picture(tmp_path: Path) -> None:
    for _ in range(10):
        data = _fetch(tmp_path, _LIVE)
        assert data["id"] == "swsh4.5-14"
    assert data["pool"] == ""
    assert data["pool_size"] == 1
    assert data["number"] == "14/72"
    assert data["types"] == ["Grass"]


def test_name_mode_matches_contains_and_exact(tmp_path: Path) -> None:
    seen: list[str] = []
    data = _fetch(tmp_path, _LIVE, seen, mode="name", character="  pikachu ")
    assert data["pool"] == "pikachu"
    assert data["pool_size"] == 2  # the image-less promo is dropped
    assert f"{API}/cards?name=pikachu" in seen
    exact = _fetch(tmp_path, _LIVE, mode="name", character="Pikachu", exact_name=True)
    assert exact["pool_size"] == 1
    assert exact["id"] == "base1-58"


def test_name_mode_with_no_match_says_so(tmp_path: Path) -> None:
    responses = {**_LIVE, f"{API}/cards?": _j([])}
    data = _fetch(tmp_path, responses, mode="name", character="Missingno")
    assert data["error"] == "No cards found for “Missingno”."
    assert _fetch(tmp_path, _LIVE, mode="name", character="")["error"].startswith("Type a")


def test_series_mode_picks_from_a_set_of_the_series(tmp_path: Path) -> None:
    seen: list[str] = []
    data = _fetch(tmp_path, _LIVE, seen, mode="series", series="swsh")
    assert data["id"] == "swsh4.5-14"
    assert data["pool"] == "Sword & Shield"
    assert data["pool_size"] == 73
    assert not any("/sets/empty" in url for url in seen)


def test_unknown_series_is_a_friendly_error(tmp_path: Path) -> None:
    import urllib.error

    gone = urllib.error.HTTPError(f"{API}/series/nope", 404, "Not Found", {}, None)  # type: ignore[arg-type]
    data = _fetch(tmp_path, {**_LIVE, f"{API}/series/nope": gone}, mode="series", series="nope")
    assert data["error"] == "TCGdex doesn't know that series."


def test_daily_pick_holds_the_same_card(tmp_path: Path) -> None:
    many = [_brief(f"s1-{i}", f"Mon {i}") for i in range(200)]
    responses = {**_LIVE, f"{API}/cards?": _j(many)}
    picks = {
        _fetch(tmp_path, responses, mode="name", character="mon", change="day")["id"]
        for _ in range(5)
    }
    assert len(picks) == 1


def test_image_is_stored_and_served_locally(client: FlaskClient, tmp_path: Path) -> None:
    data = _fetch(tmp_path, _LIVE)
    name = data["url"].rsplit("/", 1)[1]
    assert data["url"] == f"/plugins/picture_pokemon/cards/{name}"
    assert (tmp_path / "images" / name).is_file()

    plugin = client.application.config["PLUGIN_REGISTRY"].get("picture_pokemon")
    images = Path(plugin.data_dir) / "images"
    images.mkdir(parents=True, exist_ok=True)
    (images / name).write_bytes(_webp())
    resp = client.get(f"/plugins/picture_pokemon/cards/{name}")
    try:
        assert resp.status_code == 200
        assert resp.mimetype == "image/webp"
    finally:
        resp.close()
    assert client.get("/plugins/picture_pokemon/cards/..%2Fpools%2Fall.json").status_code == 404


def test_falls_back_to_remote_image_when_download_fails(tmp_path: Path) -> None:
    data = _fetch(tmp_path, {**_LIVE, f"{ASSETS}/": OSError("refused")})
    assert data["url"] == f"{ASSETS}/x/swsh4.5/14/high.png"


def test_missing_details_still_show_the_card(tmp_path: Path) -> None:
    data = _fetch(tmp_path, {**_LIVE, f"{API}/cards/": OSError("down")})
    assert data["name"] == "Gossifleur"
    assert data["number"] == "14"
    assert data["url"].startswith("/plugins/picture_pokemon/cards/")


def test_unreachable_api_is_a_friendly_error(tmp_path: Path) -> None:
    data = _fetch(tmp_path, {f"{API}/cards": OSError("no route")})
    assert data["error"] == "Couldn't reach TCGdex right now."


def test_pool_is_cached_and_stale_pool_used_offline(tmp_path: Path) -> None:
    first = _fetch(tmp_path, _LIVE)
    # Fresh cache: the pool, details and image need no request at all.
    assert _fetch(tmp_path, {})["id"] == first["id"]
    # Stale cache and TCGdex down: the old pool still answers.
    pool = tmp_path / "pools" / "all.json"
    old = pool.stat().st_mtime - server.POOL_TTL_S - 10
    import os

    os.utime(pool, (old, old))
    assert _fetch(tmp_path, {f"{API}/cards": OSError("down")})["id"] == first["id"]


def test_series_choices_fall_back_offline(client: FlaskClient) -> None:
    with client.application.app_context():
        with patch("urllib.request.urlopen", side_effect=OSError("down")):
            got = server.choices("series")
    assert {"value": "sv", "label": "Scarlet & Violet"} in got
    assert server.choices("other") == []
