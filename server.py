"""picture_pokemon, a random Pokémon Trading Card Game card.

Cards come from TCGdex (https://tcgdex.dev/rest), a free, keyless REST
API. TCGdex has a ``/random/card`` endpoint, but about one card in thirteen
has no scanned image, and a random pick that lands on one of those would
leave the cell blank. So the widget draws from a *pool* instead, a list of
card briefs (``{id, localId, name, image}``) with every image-less card
dropped, and picks from that:

  any     ``GET /cards``, every card (~24k briefs, ~2.4 MB), one request.
  series  ``GET /series/<id>`` lists the series' sets with their card
          counts. A set is picked weighted by its count, so every card in
          the series is about equally likely, then ``GET /sets/<id>``
          gives that set's cards. Fetching every set of a big series on
          each render would be dozens of requests; this is two.
  name    ``GET /cards?name=<q>``, TCGdex's contains match ("pikachu"
          also finds "Ash's Pikachu" and "Pikachu VMAX"). The *exact name*
          option narrows that to names equal to the query.

Pools are cached for a day in data_dir (sets for a week, they don't
change once released); a stale pool is used when TCGdex can't be reached,
so an offline host keeps turning up cards it has already seen.

The picked card's details (set, rarity, HP, illustrator) come from
``GET /cards/<id>``, best effort. Its ``high.webp`` scan (600x825, ~60 kB)
is downloaded once and served from this plugin's ``/cards/`` route, so the
renderer reads it over loopback instead of waiting on a remote image.

The *new card* option picks a fresh card on every render, or holds one
card for the hour or the day: the pick is seeded from the pool and the
time bucket, so the editor preview and the panel push agree without any
state being kept.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Blueprint, abort, current_app, send_from_directory

from app.plugin_http import decode_body

PLUGIN_ID = "picture_pokemon"
API_ROOT = "https://api.tcgdex.net/v2"
LANG = "en"
USER_AGENT = f"tesserae/0.1 (+{PLUGIN_ID})"
HTTP_TIMEOUT_S = 6
MAX_BODY_BYTES = 8 * 1024 * 1024
POOL_TTL_S = 24 * 3600
SET_TTL_S = 7 * 24 * 3600
SERIES_LIST_TTL_S = 7 * 24 * 3600
KEEP_IMAGES = 40
KEEP_DETAILS = 200
# Sets in a series to try before giving up (a few promo sets have no scans).
SET_ATTEMPTS = 4

_IMAGE_NAME_RE = re.compile(r"^[a-z0-9-]+\.(webp|png)$")

# The editor's series dropdown when TCGdex can't be reached at edit time.
FALLBACK_SERIES: tuple[tuple[str, str], ...] = (
    ("base", "Base"),
    ("gym", "Gym"),
    ("neo", "Neo"),
    ("ecard", "E-Card"),
    ("ex", "EX"),
    ("dp", "Diamond & Pearl"),
    ("pl", "Platinum"),
    ("hgss", "HeartGold & SoulSilver"),
    ("bw", "Black & White"),
    ("xy", "XY"),
    ("sm", "Sun & Moon"),
    ("swsh", "Sword & Shield"),
    ("sv", "Scarlet & Violet"),
)


class NotFound(Exception):
    """The pool came back empty: nothing to pick from."""


# ----------------------------------------------------------------------
# Upstream
# ----------------------------------------------------------------------


def _get(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        body = decode_body(resp)
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("response too large")
    return body


def _api(path: str, query: dict[str, str] | None = None) -> Any:
    url = f"{API_ROOT}/{LANG}/{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    return json.loads(_get(url))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-") or "x"


def _key(text: str) -> str:
    """A path-safe, collision-free file stem for an id or query."""
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"{_slug(text)[:40]}-{digest}"


# ----------------------------------------------------------------------
# Disk cache
# ----------------------------------------------------------------------


def _read_json(path: Path, ttl_s: float | None) -> Any:
    """The cached value, or None when missing, unreadable, or older than
    ``ttl_s`` (None means any age will do)."""
    try:
        if ttl_s is not None and time.time() - path.stat().st_mtime >= ttl_s:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, value: Any) -> None:
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value), encoding="utf-8")
        tmp.replace(path)


def _cached(path: Path, ttl_s: float, load: Any) -> Any:
    """Fresh cache, else ``load()`` (and keep it), else stale cache, else
    re-raise what ``load()`` raised."""
    fresh = _read_json(path, ttl_s)
    if fresh is not None:
        return fresh
    try:
        value = load()
    except Exception:
        stale = _read_json(path, None)
        if stale is not None:
            return stale
        raise
    _write_json(path, value)
    return value


def _prune(folder: Path, pattern: str, keep: int) -> None:
    files = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        with contextlib.suppress(OSError):
            old.unlink()


# ----------------------------------------------------------------------
# Pools
# ----------------------------------------------------------------------


def _briefs(cards: Any) -> list[dict[str, str]]:
    """Card briefs with a scan, trimmed to what a pick needs."""
    out = []
    for c in cards if isinstance(cards, list) else []:
        if isinstance(c, dict) and c.get("id") and c.get("image"):
            out.append(
                {
                    "id": str(c["id"]),
                    "localId": str(c.get("localId") or ""),
                    "name": str(c.get("name") or ""),
                    "image": str(c["image"]),
                }
            )
    return out


def _all_pool(data_dir: Path) -> list[dict[str, str]]:
    return _cached(data_dir / "pools" / "all.json", POOL_TTL_S, lambda: _briefs(_api("cards")))


def _name_pool(data_dir: Path, name: str, exact: bool) -> list[dict[str, str]]:
    pool = _cached(
        data_dir / "pools" / f"name-{_key(name.casefold())}.json",
        POOL_TTL_S,
        lambda: _briefs(_api("cards", {"name": name})),
    )
    if exact:
        want = name.casefold()
        pool = [c for c in pool if c["name"].casefold() == want]
    return pool


def _series_sets(data_dir: Path, series_id: str) -> dict[str, Any]:
    def load() -> dict[str, Any]:
        raw = _api(f"series/{urllib.parse.quote(series_id, safe='')}")
        sets = []
        for s in raw.get("sets") or []:
            total = int((s.get("cardCount") or {}).get("total") or 0)
            if s.get("id") and total > 0:
                sets.append({"id": str(s["id"]), "name": str(s.get("name") or ""), "total": total})
        return {"name": str(raw.get("name") or series_id), "sets": sets}

    return _cached(data_dir / "pools" / f"series-{_key(series_id)}.json", POOL_TTL_S, load)


def _set_pool(data_dir: Path, set_id: str) -> dict[str, Any]:
    def load() -> dict[str, Any]:
        raw = _api(f"sets/{urllib.parse.quote(set_id, safe='')}")
        count = raw.get("cardCount") or {}
        return {
            "name": str(raw.get("name") or set_id),
            "official": int(count.get("official") or 0),
            "cards": _briefs(raw.get("cards")),
        }

    return _cached(data_dir / "pools" / f"set-{_key(set_id)}.json", SET_TTL_S, load)


# ----------------------------------------------------------------------
# Picking
# ----------------------------------------------------------------------


def _bucket(change: str, now: float) -> str | None:
    """The time bucket a held card belongs to, None for a fresh pick."""
    local = datetime.fromtimestamp(now)
    if change == "hour":
        return local.strftime("%Y-%m-%d %H")
    if change == "day":
        return local.strftime("%Y-%m-%d")
    return None


def _rng(pool_key: str, change: str, now: float) -> random.Random:
    bucket = _bucket(change, now)
    if bucket is None:
        return random.Random()
    seed = hashlib.sha256(f"{pool_key}|{bucket}".encode()).hexdigest()
    return random.Random(seed)


def _pick(
    options: dict[str, Any], data_dir: Path, now: float
) -> tuple[dict[str, str], str, int]:
    """(card brief, pool label, pool size). Raises NotFound or network errors."""
    mode = str(options.get("mode") or "any")
    change = str(options.get("change") or "refresh")

    if mode == "name":
        name = " ".join(str(options.get("character") or "").split())
        if not name:
            raise NotFound("Type a Pokémon name in the widget's options.")
        exact = options.get("exact_name") in (True, "true", "on", "1", 1)
        pool = _name_pool(data_dir, name, exact)
        if not pool:
            raise NotFound(f"No cards found for “{name}”.")
        rng = _rng(f"name|{name.casefold()}|{exact}", change, now)
        return rng.choice(pool), name, len(pool)

    if mode == "series":
        series_id = str(options.get("series") or "sv")
        series = _series_sets(data_dir, series_id)
        sets = list(series["sets"])
        if not sets:
            raise NotFound("That series has no cards.")
        rng = _rng(f"series|{series_id}", change, now)
        size = sum(s["total"] for s in sets)
        for _ in range(SET_ATTEMPTS):
            chosen = rng.choices(sets, weights=[s["total"] for s in sets])[0]
            cards = _set_pool(data_dir, chosen["id"])["cards"]
            if cards:
                return rng.choice(cards), series["name"], size
            sets.remove(chosen)
            if not sets:
                break
        raise NotFound("Couldn't find a card with a picture in that series.")

    pool = _all_pool(data_dir)
    if not pool:
        raise NotFound("TCGdex returned no cards.")
    return _rng("all", change, now).choice(pool), "", len(pool)


# ----------------------------------------------------------------------
# Card details and image
# ----------------------------------------------------------------------


def _details(data_dir: Path, card_id: str) -> dict[str, Any]:
    """The card's display fields, cached for good (cards don't change)."""
    path = data_dir / "cards" / f"{_key(card_id)}.json"
    cached = _read_json(path, None)
    if isinstance(cached, dict):
        return cached
    raw = _api(f"cards/{urllib.parse.quote(card_id, safe='')}")
    card_set = raw.get("set") or {}
    details = {
        "name": raw.get("name") or "",
        "category": raw.get("category") or "",
        "rarity": raw.get("rarity") or "",
        "illustrator": raw.get("illustrator") or "",
        "hp": raw.get("hp"),
        "types": [str(t) for t in raw.get("types") or []],
        "stage": raw.get("stage") or "",
        "set_id": card_set.get("id") or "",
        "set_name": card_set.get("name") or "",
        "set_official": int((card_set.get("cardCount") or {}).get("official") or 0),
    }
    _write_json(path, details)
    _prune(path.parent, "*.json", KEEP_DETAILS)
    return details


def _images_dir(data_dir: Path) -> Path:
    path = data_dir / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _store_image(image_base: str, card_id: str, data_dir: Path) -> str:
    """Download the card's scan once, return the stored file name."""
    from PIL import Image

    folder = _images_dir(data_dir)
    for ext in ("webp", "png"):
        name = f"{_key(card_id)}.{ext}"
        if (folder / name).is_file():
            return name

    last: Exception | None = None
    for ext in ("webp", "png"):
        try:
            body = _get(f"{image_base}/high.{ext}")
            with Image.open(io.BytesIO(body)) as img:
                img.verify()
        except Exception as err:  # try the next format
            last = err
            continue
        name = f"{_key(card_id)}.{ext}"
        tmp = folder / f"{name}.tmp"
        tmp.write_bytes(body)
        tmp.replace(folder / name)
        _prune(folder, "*.webp", KEEP_IMAGES)
        _prune(folder, "*.png", KEEP_IMAGES)
        return name
    raise last or ValueError("no image")


def _image_route(name: str) -> str:
    return f"/plugins/{PLUGIN_ID}/cards/{name}"


# ----------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)

    try:
        brief, pool_label, pool_size = _pick(options, data_dir, time.time())
    except NotFound as err:
        return {"error": str(err)}
    except urllib.error.HTTPError as err:
        with contextlib.suppress(Exception):
            err.close()
        if err.code == 404:
            return {"error": "TCGdex doesn't know that series."}
        return {"error": f"TCGdex answered {err.code}. Try again later."}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"error": "Couldn't reach TCGdex right now."}
    except (ValueError, KeyError, TypeError):
        return {"error": "TCGdex's answer couldn't be read."}
    except Exception:
        return {"error": "Couldn't load a Pokémon card right now."}

    details: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        details = _details(data_dir, brief["id"])

    url = f"{brief['image']}/high.png"
    with contextlib.suppress(Exception):
        url = _image_route(_store_image(brief["image"], brief["id"], data_dir))

    local_id = brief["localId"]
    official = details.get("set_official") or 0
    return {
        "url": url,
        "id": brief["id"],
        "name": details.get("name") or brief["name"],
        "number": f"{local_id}/{official}" if local_id and official else local_id,
        "set_name": details.get("set_name") or "",
        "rarity": details.get("rarity") or "",
        "illustrator": details.get("illustrator") or "",
        "category": details.get("category") or "",
        "stage": details.get("stage") or "",
        "hp": details.get("hp"),
        "types": details.get("types") or [],
        "pool": pool_label,
        "pool_size": pool_size,
        "fetched_at": int(time.time()),
    }


def choices(name: str) -> list[dict[str, str]]:
    """The editor's series dropdown, from TCGdex when it answers."""
    if name != "series":
        return []
    fallback = [{"value": v, "label": label} for v, label in FALLBACK_SERIES]
    try:
        plugin = current_app.config["PLUGIN_REGISTRY"].get(PLUGIN_ID)
        data_dir = Path(plugin.data_dir)
    except Exception:
        return fallback
    try:
        raw = _cached(
            data_dir / "pools" / "series-list.json", SERIES_LIST_TTL_S, lambda: _api("series")
        )
    except Exception:
        return fallback
    out = [
        {"value": str(s["id"]), "label": str(s.get("name") or s["id"])}
        for s in raw
        if isinstance(s, dict) and s.get("id")
    ]
    # Newest first: TCGdex lists them oldest first.
    return list(reversed(out)) or fallback


# ----------------------------------------------------------------------
# Image serving
# ----------------------------------------------------------------------

# Read-only, declared safe for a loopback render with no session (see
# app/auth.py): the widget's <img src> points here. Nothing that writes,
# deletes, or reaches the network belongs in this list.
RENDER_SAFE_ENDPOINTS: tuple[str, ...] = (f"{PLUGIN_ID}_cards.serve_card",)


def blueprint() -> Blueprint:
    bp = Blueprint(f"{PLUGIN_ID}_cards", __name__)

    @bp.get("/cards/<name>")
    def serve_card(name: str) -> Any:
        if not _IMAGE_NAME_RE.match(name):
            abort(404)
        plugin = current_app.config["PLUGIN_REGISTRY"].get(PLUGIN_ID)
        if plugin is None:
            abort(404)
        response = send_from_directory(_images_dir(Path(plugin.data_dir)), name)
        # A file name is derived from the card id, so its bytes never change.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    return bp
