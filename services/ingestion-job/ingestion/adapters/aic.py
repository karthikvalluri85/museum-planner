"""Art Institute of Chicago, via https://api.artic.edu/docs/.

GET /artworks/search accepts Elasticsearch-style `query[term][...]` parameters. The API refuses any
page that reaches past the 1000th result (HTTP 403 'Invalid number of results'), so one query can
return at most 1000 works."""

import re
from typing import Any

from pydantic import ValidationError

from ingestion.adapters.base import REQUEST_BUDGET_FACTOR, MuseumInfo
from ingestion.http_client import USER_AGENT, HttpClient, RequestBudgetExceeded
from ingestion.models import AicArtwork, NormalizedObject
from ingestion.normalize import Skip, blank_to_none, build_object, largest_cm_in_text, strip_html

API_BASE_URL = "https://api.artic.edu/api/v1"
SEARCH_URL = f"{API_BASE_URL}/artworks/search"
PAGE_SIZE = 100
MAX_RESULT_WINDOW = 1000

# Exactly the fields in contracts/upstream/aic-artwork.schema.json.
FIELDS = (
    "id,title,artist_display,artist_title,date_display,medium_display,dimensions,is_on_view,"
    "gallery_id,gallery_title,image_id,department_title,classification_title,style_title,"
    "subject_titles,term_titles,short_description,is_boosted"
)

_GALLERY_TITLE = re.compile(r"Gallery ([A-Za-z0-9]+)")


def _gallery(raw: AicArtwork) -> str:
    """The number in 'Gallery 240'. Empty for anything else: not on view, or a named space."""
    if not raw.is_on_view:
        return ""
    match = _GALLERY_TITLE.fullmatch(raw.gallery_title or "")
    return match.group(1) if match else ""


def _artist(raw: AicArtwork) -> str | None:
    return blank_to_none(raw.artist_title) or blank_to_none((raw.artist_display or "").split("\n", 1)[0])


class AicAdapter:
    museum_id = "aic"
    fixture_file = "artworks.json"
    info = MuseumInfo(
        name="Art Institute of Chicago",
        city="Chicago",
        api_name="Art Institute of Chicago API",
        api_base_url=API_BASE_URL,
        license="CC0 1.0 for most data; see https://api.artic.edu/docs/",
    )

    def normalize(self, record: dict[str, Any]) -> NormalizedObject | Skip:
        try:
            raw = AicArtwork.model_validate(record)
        except ValidationError:
            return Skip.INVALID
        return build_object(
            museum_id=self.museum_id,
            source_id=str(raw.id),
            title=raw.title,
            gallery=_gallery(raw),
            artist=_artist(raw),
            date=raw.date_display,
            tags=[
                *(raw.subject_titles or []),
                raw.classification_title,
                raw.style_title,
                raw.department_title,
                *(raw.term_titles or []),
            ],
            department=raw.department_title,
            medium=raw.medium_display,
            description=strip_html(raw.short_description),
            image_url=(
                f"https://www.artic.edu/iiif/2/{raw.image_id}/full/843,/0/default.jpg" if raw.image_id else None
            ),
            source_url=f"https://www.artic.edu/artworks/{raw.id}",
            is_highlight=bool(raw.is_boosted),
            max_cm=largest_cm_in_text(raw.dimensions),
        )

    def fetch_live(self, http: HttpClient, max_objects: int) -> list[dict[str, Any]]:
        http.reset_budget(REQUEST_BUDGET_FACTOR * max_objects)
        records: list[dict[str, Any]] = []
        on_view = 0
        page = 1
        while on_view < max_objects and page * PAGE_SIZE <= MAX_RESULT_WINDOW:
            try:
                payload = http.get_json(
                    SEARCH_URL,
                    params={
                        "query[term][is_on_view]": "true",
                        "fields": FIELDS,
                        "limit": PAGE_SIZE,
                        "page": page,
                    },
                    headers={"AIC-User-Agent": USER_AGENT},
                )
            except RequestBudgetExceeded:
                break
            rows = payload.get("data") or []
            records.extend(rows)
            on_view += sum(1 for row in rows if row.get("gallery_title"))
            total_pages = (payload.get("pagination") or {}).get("total_pages", page)
            if not rows or page >= total_pages:
                break
            page += 1
        return records
