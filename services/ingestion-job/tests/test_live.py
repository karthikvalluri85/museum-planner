"""Live adapters against httpx.MockTransport: no network."""

import json

import httpx
import pytest

from ingestion.adapters.aic import AicAdapter
from ingestion.adapters.met import MetAdapter
from ingestion.http_client import USER_AGENT, HttpClient, HttpStatusError


def client(handler, sleeps=None):
    return HttpClient(
        transport=httpx.MockTransport(handler),
        requests_per_second=0,
        sleep=(sleeps.append if sleeps is not None else (lambda seconds: None)),
    )


def met_handler(calls, *, highlights, on_view, gallery="100", ignore_limit=False):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        params = request.url.params
        if path == "/public/collection/v1/departments":
            departments = [{"departmentId": d, "displayName": f"Dept {d}"} for d in sorted(on_view)]
            return httpx.Response(200, json={"departments": departments})
        if path == "/public/collection/v1.1/search":
            department = int(params["departmentId"])
            ids = highlights[department] if params.get("isHighlight") == "true" else on_view[department]
            return httpx.Response(200, json={"total": len(ids), "objectIDs": ids if ignore_limit else ids[: int(params["limit"])]})
        object_id = int(path.rsplit("/", 1)[1])
        return httpx.Response(
            200, json={"objectID": object_id, "title": f"Object {object_id}", "GalleryNumber": gallery}
        )

    return handler


def test_met_search_is_spread_across_departments_with_highlights_first():
    calls = []
    handler = met_handler(
        calls,
        highlights={1: [11, 12], 2: [21]},
        on_view={1: [11, 12, 13, 14], 2: [21, 22, 23]},
        ignore_limit=True,
    )
    records = MetAdapter().fetch_live(client(handler), max_objects=3)

    assert [r["objectID"] for r in records] == [11, 21, 12, 13, 22, 14, 23]
    searches = [c for c in calls if c.url.path == "/public/collection/v1.1/search"]
    assert dict(searches[0].url.params) == {
        "isOnView": "true", "departmentId": "1", "limit": "3", "offset": "0", "isHighlight": "true",
    }
    assert "isHighlight" not in searches[1].url.params
    assert all(c.headers["user-agent"] == USER_AGENT for c in calls)
    assert all(c.method == "GET" for c in calls)


def test_met_stops_fetching_once_enough_objects_with_a_gallery_are_in_hand():
    calls = []
    ids = list(range(1, 101))
    handler = met_handler(calls, highlights={1: ids}, on_view={1: ids}, ignore_limit=True)
    records = MetAdapter().fetch_live(client(handler), max_objects=10)
    object_calls = [c for c in calls if "/objects/" in c.url.path]
    assert len(records) == len(object_calls) == 20  # one batch of 20 already holds 10 on-view objects


def test_met_never_exceeds_four_requests_per_requested_object():
    calls = []
    many = list(range(1000, 1050))
    handler = met_handler(calls, highlights={1: [], 2: []}, on_view={1: many, 2: many}, gallery="", ignore_limit=True)
    records = MetAdapter().fetch_live(client(handler), max_objects=3)
    assert len(calls) == 4 * 3
    assert 0 < len(records) < len(calls)


def test_met_404_for_one_object_is_skipped():
    def handler(request):
        path = request.url.path
        if path == "/public/collection/v1/departments":
            return httpx.Response(200, json={"departments": [{"departmentId": 1, "displayName": "A"}]})
        if path == "/public/collection/v1.1/search":
            return httpx.Response(200, json={"total": 2, "objectIDs": [1, 2]})
        if path.endswith("/1"):
            return httpx.Response(404, json={"message": "ObjectID not found"})
        return httpx.Response(200, json={"objectID": 2, "title": "Two", "GalleryNumber": "7"})

    records = MetAdapter().fetch_live(client(handler), max_objects=2)
    assert [r["objectID"] for r in records] == [2]


def aic_handler(calls, *, rows_per_page, total_pages):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        page = int(request.url.params["page"])
        rows = [{"id": page * 1000 + i, "title": f"Work {page}-{i}", "gallery_title": "Gallery 1"} for i in range(rows_per_page)]
        return httpx.Response(200, json={"pagination": {"total_pages": total_pages, "current_page": page}, "data": rows})

    return handler


def test_aic_request_shape_and_pagination(contracts_dir):
    calls = []
    records = AicAdapter().fetch_live(client(aic_handler(calls, rows_per_page=2, total_pages=3)), max_objects=5)

    assert len(calls) == 3 and len(records) == 6  # 2 + 2 + 2 rows; stops after reaching 5 usable works
    first = calls[0]
    assert first.url.path == "/api/v1/artworks/search"
    assert first.headers["AIC-User-Agent"] == USER_AGENT
    params = first.url.params
    assert params["query[term][is_on_view]"] == "true"
    assert params["limit"] == "100" and params["page"] == "1"
    upstream = json.loads((contracts_dir / "upstream" / "aic-artwork.schema.json").read_text(encoding="utf-8"))
    assert params["fields"].split(",") == list(upstream["properties"])
    assert [int(c.url.params["page"]) for c in calls] == [1, 2, 3]


def test_aic_stops_at_the_last_page():
    calls = []
    AicAdapter().fetch_live(client(aic_handler(calls, rows_per_page=1, total_pages=2)), max_objects=100)
    assert len(calls) == 2


def test_aic_never_asks_past_the_1000_result_window():
    calls = []
    AicAdapter().fetch_live(client(aic_handler(calls, rows_per_page=1, total_pages=50)), max_objects=5000)
    assert len(calls) == 10  # page 10 x 100 results is the last page the API allows


def test_retry_after_429_honours_retry_after():
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"ok": True})

    assert client(handler, sleeps).get_json("https://example.test/a") == {"ok": True}
    assert len(calls) == 2 and sleeps == [2.0]


def test_server_errors_back_off_exponentially_then_succeed():
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, json=[1])

    assert client(handler, sleeps).get_json("https://example.test/a") == [1]
    assert sleeps == [0.5, 1.0]


def test_gives_up_after_the_retry_limit():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    with pytest.raises(HttpStatusError) as info:
        client(handler).get_json("https://example.test/a")
    assert info.value.status == 500 and len(calls) == 5


def test_client_errors_are_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(403, text="blocked")

    with pytest.raises(HttpStatusError) as info:
        client(handler).get_json("https://example.test/a")
    assert info.value.status == 403 and len(calls) == 1


def blocking_met_handler(calls, *, allow_objects):
    """Serves searches normally, then answers 403 to every object request after the first `allow_objects`."""
    served = []
    inner = met_handler(calls, highlights={1: list(range(1, 41))}, on_view={1: list(range(1, 41))})

    def handler(request: httpx.Request) -> httpx.Response:
        if "/objects/" in request.url.path:
            if len(served) >= allow_objects:
                calls.append(request)
                return httpx.Response(403, text="blocked")
            served.append(request)
        return inner(request)

    return handler


def test_met_keeps_what_it_fetched_when_the_api_starts_refusing():
    calls = []
    records = MetAdapter().fetch_live(client(blocking_met_handler(calls, allow_objects=7)), max_objects=30)
    assert len(records) == 7
    object_calls = [c for c in calls if "/objects/" in c.url.path]
    # It stops at the end of the batch that hit the wall; it does not keep knocking for all 30.
    assert len(object_calls) <= 20


def test_met_fails_when_every_object_request_is_refused():
    from ingestion.http_client import UpstreamError

    with pytest.raises(UpstreamError, match="refused every object request"):
        MetAdapter().fetch_live(client(blocking_met_handler([], allow_objects=0)), max_objects=30)
