# -*- coding: utf-8 -*-
"""Tests for the page-campaign-deck skill and its CivicSpace topic skills.

The skill ships two scripts that do real work before any design happens:
`campaign_pull.py` (credential resolution, Graph URL assembly, date handling)
and `circle_crop.py` (square-crop + circular alpha mask for PPTX). Both are
exercised here for real — no network, stdlib + pytest + Pillow only.
"""
import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
DECK_DIR = REPO / "skills" / "social-media" / "page-campaign-deck"
TOPIC_DIRS = (
    REPO / "skills" / "civicspace" / "roiet-youth",
    REPO / "skills" / "civicspace" / "funeral-norms",
)


def _load(stem):
    path = DECK_DIR / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"_pcd_{stem}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pull():
    return _load("campaign_pull")


@pytest.fixture(scope="module")
def crop():
    pytest.importorskip("PIL")
    return _load("circle_crop")


def _frontmatter(skill_dir):
    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert content.startswith("---")
    end = content.index("\n---", 3)
    return yaml.safe_load(content[3:end])


# --------------------------------------------------------------------------
# frontmatter contracts
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "skill_dir", (DECK_DIR,) + TOPIC_DIRS, ids=lambda p: p.name
)
def test_frontmatter_name_matches_directory(skill_dir):
    assert _frontmatter(skill_dir)["name"] == skill_dir.name


@pytest.mark.parametrize(
    "skill_dir", (DECK_DIR,) + TOPIC_DIRS, ids=lambda p: p.name
)
def test_author_credits_the_human_first(skill_dir):
    author = _frontmatter(skill_dir)["author"]
    assert not author.startswith("Hermes Agent")
    assert "cabindev" in author


def test_topic_skills_do_not_duplicate_the_mechanism():
    """Topic skills hold knowledge only; the scripts live in the deck skill."""
    for d in TOPIC_DIRS:
        assert not list(d.glob("*.py")), f"{d.name} must call the deck skill's scripts"


# --------------------------------------------------------------------------
# campaign_pull: date window
# --------------------------------------------------------------------------
def test_since_stamp_resolves_relative_window(pull):
    assert pull.since_stamp("30d") == (date.today() - timedelta(days=30)).isoformat()


def test_since_stamp_passes_absolute_dates_through(pull):
    assert pull.since_stamp("2026-05-01") == "2026-05-01"


def test_thai_month_table_covers_the_year(pull):
    assert sorted(pull.TH_MON) == list(range(1, 13))


# --------------------------------------------------------------------------
# campaign_pull: credential resolution
# --------------------------------------------------------------------------
def _clear_creds(monkeypatch):
    monkeypatch.delenv("FACEBOOK_PAGE_ID", raising=False)
    monkeypatch.delenv("FACEBOOK_PAGE_TOKEN", raising=False)


def test_credentials_prefer_the_environment(pull, monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("FACEBOOK_PAGE_ID=from-file\nFACEBOOK_PAGE_TOKEN=tok-file\n")
    monkeypatch.setattr(pull, "ENV_FILES", [str(env_file)])
    monkeypatch.setenv("FACEBOOK_PAGE_ID", "from-env")
    monkeypatch.setenv("FACEBOOK_PAGE_TOKEN", "tok-env")
    assert pull.credentials() == ("from-env", "tok-env")


def test_credentials_fall_back_to_env_file(pull, monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment line\n"
        "\n"
        "UNRELATED=x\n"
        'FACEBOOK_PAGE_ID="728353493701593"\n'
        "FACEBOOK_PAGE_TOKEN='tok-quoted'\n"
    )
    monkeypatch.setattr(pull, "ENV_FILES", [str(env_file)])
    _clear_creds(monkeypatch)
    assert pull.credentials() == ("728353493701593", "tok-quoted")


def test_credentials_use_the_first_file_that_has_them(pull, monkeypatch, tmp_path):
    first, second = tmp_path / "a.env", tmp_path / "b.env"
    first.write_text("FACEBOOK_PAGE_ID=winner\nFACEBOOK_PAGE_TOKEN=tok-a\n")
    second.write_text("FACEBOOK_PAGE_ID=loser\nFACEBOOK_PAGE_TOKEN=tok-b\n")
    monkeypatch.setattr(pull, "ENV_FILES", [str(first), str(second)])
    _clear_creds(monkeypatch)
    assert pull.credentials() == ("winner", "tok-a")


def test_credentials_exit_when_nothing_is_configured(pull, monkeypatch, tmp_path):
    monkeypatch.setattr(pull, "ENV_FILES", [str(tmp_path / "absent.env")])
    _clear_creds(monkeypatch)
    with pytest.raises(SystemExit):
        pull.credentials()


# --------------------------------------------------------------------------
# campaign_pull: Graph URL assembly
# --------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


@pytest.fixture
def seen_urls(pull, monkeypatch):
    urls = []

    def fake_urlopen(url, timeout=None):
        urls.append(url)
        return _FakeResponse({"data": []})

    monkeypatch.setattr(pull.urllib.request, "urlopen", fake_urlopen)
    return urls


def test_graph_builds_an_edge_url_with_the_token(pull, seen_urls):
    data, err = pull.graph("PAGE/posts", {"limit": 100}, "secret-token")
    assert (data, err) == ({"data": []}, None)
    url = seen_urls[0]
    assert url.startswith(pull.GRAPH + "PAGE/posts?")
    assert "limit=100" in url
    assert "access_token=secret-token" in url


def test_graph_does_not_double_append_the_token_on_paging_urls(pull, seen_urls):
    nxt = "https://graph.facebook.com/v21.0/PAGE/posts?after=CUR&access_token=secret"
    pull.graph(nxt, {}, "secret")
    assert seen_urls[0] == nxt
    assert seen_urls[0].count("access_token=") == 1


def test_graph_appends_the_token_to_a_paging_url_that_lacks_it(pull, seen_urls):
    pull.graph("https://graph.facebook.com/v21.0/PAGE/posts?after=CUR", {}, "secret")
    url = seen_urls[0]
    assert "?after=CUR&access_token=secret" in url
    assert url.count("?") == 1


# --------------------------------------------------------------------------
# circle_crop: the mask PPTX needs
# --------------------------------------------------------------------------
def _band_image(tmp_path, name="src.jpg"):
    """100x300: red / green / blue horizontal thirds."""
    from PIL import Image

    im = Image.new("RGB", (100, 300))
    for y, colour in ((0, (255, 0, 0)), (100, (0, 255, 0)), (200, (0, 0, 255))):
        im.paste(colour, (0, y, 100, y + 100))
    path = tmp_path / name
    im.save(path, "JPEG", quality=95)
    return path


def test_crop_one_writes_a_transparent_circle(crop, tmp_path):
    from PIL import Image

    dst = tmp_path / "out.png"
    crop.crop_one(_band_image(tmp_path), dst, size=128, focus=0.5)
    out = Image.open(dst)
    assert out.mode == "RGBA"
    assert out.size == (128, 128)
    assert out.getpixel((0, 0))[3] == 0, "corner must be transparent"
    assert out.getpixel((127, 127))[3] == 0, "corner must be transparent"
    assert out.getpixel((64, 64))[3] == 255, "centre must be opaque"


@pytest.mark.parametrize(
    "focus,expected",
    [(0.0, "red"), (0.5, "green"), (1.0, "blue")],
)
def test_focus_slides_the_crop_window_down_the_image(crop, tmp_path, focus, expected):
    from PIL import Image

    dst = tmp_path / f"out-{expected}.png"
    crop.crop_one(_band_image(tmp_path), dst, size=64, focus=focus)
    r, g, b, a = Image.open(dst).getpixel((32, 32))
    dominant = {"red": r, "green": g, "blue": b}
    assert max(dominant, key=dominant.get) == expected
    assert a == 255


def test_crop_one_keeps_a_square_source_square(crop, tmp_path):
    from PIL import Image

    src = tmp_path / "square.jpg"
    Image.new("RGB", (200, 200), (10, 20, 30)).save(src, "JPEG")
    dst = tmp_path / "square.png"
    crop.crop_one(src, dst, size=96, focus=0.9)
    assert Image.open(dst).size == (96, 96)
