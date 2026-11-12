"""The Metropolitan Museum of Art, via The Met Collection API (https://metmuseum.github.io/).

There is no bulk on-view endpoint. /v1.1/search (the /v1 search was retired on 2026-10-01) lists
on-view object ids; each object is then fetched with GET /objects/{id}."""

from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
from typing import Any

from pydantic import ValidationError

from ingestion.adapters.base import REQUEST_BUDGET_FACTOR, MuseumInfo
from ingestion.http_client import HttpClient, HttpStatusError, RequestBudgetExceeded
from ingestion.models import MetObject, NormalizedObject
from ingestion.normalize import Skip, build_object, largest_cm_in_text

API_BASE_URL = "https://collectionapi.metmuseum.org/public/collection/v1"
SEARCH_URL = "https://collectionapi.metmuseum.org/public/collection/v1.1/search"
MAX_SEARCH_LIMIT = 500
CONCURRENCY = 4
FETCH_BATCH = 20


def _max_cm(raw: MetObject) -> float | None:
    measured = [e for e in raw.measurements or [] if e.elementMeasurements]
    unframed = [e for e in measured if "frame" not in (e.elementName or "").lower()]
    values = [
        float(v)
        for element in unframed or measured
        for v in (element.elementMeasurements or {}).values()
        if isinstance(v, int | float) and not isinstance(v, bool) and v > 0
    ]
    return max(values) if values else largest_cm_in_text(raw.dimensions)


def _round_robin(lists: list[list[int]]) -> list[int]:
    return [i for group in zip_longest(*lists) for i in group if i is not None]


class MetAdapter:
    museum_id = "met"
    fixture_file = "objects.json"
    info = MuseumInfo(
        name="The Metropolitan Museum of Art",
        city="New York",
        api_name="The Met Collection API",
        api_base_url=API_BASE_URL,
        license="CC0 1.0 (Met Open Access)",
    )

    def normalize(self, record: dict[str, Any]) -> NormalizedObject | Skip:
        try:
            raw = MetObject.model_validate(record)
        except ValidationError:
            return Skip.INVALID
        return build_object(
            museum_id=self.museum_id,
            source_id=str(raw.objectID),
            title=raw.title,
            gallery=raw.GalleryNumber.strip(),
            artist=raw.artistDisplayName,
            date=raw.objectDate,
            tags=[t.term for t in raw.tags or []]
            + [raw.department, raw.classification, raw.objectName, raw.culture, raw.period],
            department=raw.department,
            medium=raw.medium,
            description=None,
            image_url=(raw.primaryImageSmall or "").strip() or raw.primaryImage,
            source_url=raw.objectURL,
            is_highlight=bool(raw.isHighlight),
            max_cm=_max_cm(raw),
        )

    def fetch_live(self, http: HttpClient, max_objects: int) -> list[dict[str, Any]]:
        http.reset_budget(REQUEST_BUDGET_FACTOR * max_objects)
        candidates = self._candidate_ids(http, max_objects)
        records: list[dict[str, Any]] = []
        on_view = 0
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            for start in range(0, len(candidates), FETCH_BATCH):
                if on_view >= max_objects or http.remaining == 0:
                    break
                batch = candidates[start : start + FETCH_BATCH]
                for record in pool.map(lambda object_id: self._fetch_object(http, object_id), batch):
                    if record is None:
                        continue
                    records.append(record)
                    if str(record.get("GalleryNumber") or "").strip():
                        on_view += 1
        return records

    def _candidate_ids(self, http: HttpClient, max_objects: int) -> list[int]:
        """On-view ids, highlights first, taken round-robin across departments so that no single
        department uses up the cap."""
        limit = min(MAX_SEARCH_LIMIT, max_objects)
        highlights: list[list[int]] = []
        others: list[list[int]] = []
        try:
            departments = http.get_json(f"{API_BASE_URL}/departments").get("departments") or []
            for department in departments:
                department_id = department["departmentId"]
                top = self._search(http, department_id, limit, highlights_only=True)
                top_set = set(top)
                rest = [i for i in self._search(http, department_id, limit, highlights_only=False) if i not in top_set]
                highlights.append(top)
                others.append(rest)
        except RequestBudgetExceeded:
            pass
        return list(dict.fromkeys(_round_robin(highlights) + _round_robin(others)))

    def _search(self, http: HttpClient, department_id: int, limit: int, *, highlights_only: bool) -> list[int]:
        params: dict[str, str | int] = {
            "isOnView": "true",
            "departmentId": department_id,
            "limit": limit,
            "offset": 0,
        }
        if highlights_only:
            params["isHighlight"] = "true"
        payload = http.get_json(SEARCH_URL, params=params)
        return [int(i) for i in payload.get("objectIDs") or []]

    def _fetch_object(self, http: HttpClient, object_id: int) -> dict[str, Any] | None:
        try:
            record = http.get_json(f"{API_BASE_URL}/objects/{object_id}")
        except HttpStatusError as exc:
            if exc.status == 404:
                return None
            raise
        except RequestBudgetExceeded:
            return None
        return record if isinstance(record, dict) else None
