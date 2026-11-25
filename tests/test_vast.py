import pytest

from vast_hls_mcp import vast
from vast_hls_mcp.fetch import FetchError, FetchResult


def test_valid_vast4_inline(fixture_text):
    r = vast.analyze(fixture_text("vast4_inline.xml"))
    assert r["status"] == "valid"
    assert r["version"] == "4.2"
    assert r["errors"] == [] and r["warnings"] == []
    ad = r["ads"][0]
    assert ad["kind"] == "inline"
    assert ad["ad_system"] == "ExampleAdServer"
    creative = ad["creatives"][0]
    assert creative["duration_seconds"] == 30.0
    assert creative["universal_ad_id"]["value"] == "ABCD1234000H"
    assert len(creative["media_files"]) == 2
    assert {"start", "complete"} <= set(creative["tracking_events"])


def test_namespaced_vast_is_supported(fixture_text):
    # vast4_inline.xml declares xmlns="http://www.iab.com/VAST"
    assert vast.analyze(fixture_text("vast4_inline.xml"))["ad_count"] == 1


def test_broken_vast_reports_every_problem(fixture_text):
    r = vast.analyze(fixture_text("vast_broken.xml"))
    assert r["status"] == "invalid"
    errors = " | ".join(r["errors"])
    warnings = " | ".join(r["warnings"])
    assert "missing <AdSystem>" in errors
    assert "no <Impression>" in errors
    assert "not HH:MM:SS" in errors
    assert "requires <AdServingId>" in warnings
    assert "requires <UniversalAdId>" in warnings
    assert "http://" in warnings
    assert "no MP4 or HLS MediaFile" in warnings


def test_no_fill_without_error_url(fixture_text):
    r = vast.analyze(fixture_text("vast_nofill.xml"))
    assert r["status"] == "no_fill"
    assert any("no root <Error>" in w for w in r["warnings"])


def test_wrapper_is_parsed(fixture_text):
    r = vast.analyze(fixture_text("vast_wrapper.xml"))
    ad = r["ads"][0]
    assert ad["kind"] == "wrapper"
    assert ad["vast_ad_tag_uri"] == "https://dsp.example.com/vast?id=77"


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ("", "empty document"),
        ("<VAST", "XML parse error"),
        ("<html><body/></html>", "expected <VAST>"),
        (
            '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
            "<VAST>&e;</VAST>",
            "unsafe XML",
        ),
    ],
)
def test_bad_documents_raise(doc, message):
    with pytest.raises(vast.VastError, match=message):
        vast.analyze(doc)


def test_duration_parsing():
    assert vast.duration_to_seconds("00:00:15") == 15.0
    assert vast.duration_to_seconds("00:01:02.500") == 62.5
    assert vast.duration_to_seconds("15") is None
    assert vast.duration_to_seconds("00:61:00") is None


def _fake_fetcher(responses: dict[str, str | Exception]):
    def fetch(url: str) -> FetchResult:
        value = responses[url]
        if isinstance(value, Exception):
            raise value
        return FetchResult(url, url, 200, "application/xml", value, 42)

    return fetch


def _wrapper(next_url: str, system: str = "SSP") -> str:
    return (
        f'<VAST version="3.0"><Ad><Wrapper><AdSystem>{system}</AdSystem>'
        f"<VASTAdTagURI>{next_url}</VASTAdTagURI>"
        "<Impression>https://imp.example.com</Impression></Wrapper></Ad></VAST>"
    )


def test_unwrap_follows_chain_to_inline(fixture_text):
    fetch = _fake_fetcher(
        {
            "https://a.example/tag": _wrapper("https://b.example/tag", "SSP-A"),
            "https://b.example/tag": _wrapper("https://c.example/tag", "SSP-B"),
            "https://c.example/tag": fixture_text("vast4_inline.xml"),
        }
    )
    r = vast.unwrap("https://a.example/tag", fetcher=fetch)
    assert r["outcome"] == "inline"
    assert r["depth"] == 2
    assert [h["ad_system"] for h in r["hops"]] == ["SSP-A", "SSP-B", "ExampleAdServer"]
    assert r["total_impression_urls"] == 3
    assert r["total_elapsed_ms"] == 126


def test_unwrap_detects_loop():
    fetch = _fake_fetcher(
        {
            "https://a.example/tag": _wrapper("https://b.example/tag"),
            "https://b.example/tag": _wrapper("https://a.example/tag"),
        }
    )
    r = vast.unwrap("https://a.example/tag", fetcher=fetch)
    assert r["outcome"] == "loop"


def test_unwrap_too_deep():
    chain = {f"https://h{i}.example/": _wrapper(f"https://h{i + 1}.example/") for i in range(10)}
    r = vast.unwrap("https://h0.example/", fetcher=_fake_fetcher(chain), max_depth=3)
    assert r["outcome"] == "too_deep"
    assert len(r["hops"]) == 4


def test_unwrap_no_fill_and_fetch_error(fixture_text):
    nofill = _fake_fetcher(
        {
            "https://a.example/": _wrapper("https://b.example/"),
            "https://b.example/": fixture_text("vast_nofill.xml"),
        }
    )
    assert vast.unwrap("https://a.example/", fetcher=nofill)["outcome"] == "no_fill"
    broken = _fake_fetcher({"https://a.example/": FetchError("timed out")})
    r = vast.unwrap("https://a.example/", fetcher=broken)
    assert r["outcome"] == "fetch_error"
    assert "timed out" in r["reason"]
