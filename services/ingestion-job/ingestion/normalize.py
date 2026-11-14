"""Pure functions shared by the museum adapters. The rules are written down in the task spec and
the planner is built against them, so change them only together."""

import html
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from enum import Enum

from pydantic import ValidationError

from ingestion.models import GALLERY_RE, Facet, NormalizedObject, SizeClass

MAX_TAGS = 25
MAX_FACETS = 40
CHUNK_SIZE = 250

_BASE_DWELL = {"small": 2, "medium": 3, "large": 4, "monumental": 6}
_HIGHLIGHT_BONUS = 2
_SMALL_WORDS = {"a", "an", "and", "for", "in", "of", "on", "or", "the", "to"}

_CM_GROUP = re.compile(r"\d+(?:\.\d+)?(?:\s*[×xX]\s*\d+(?:\.\d+)?)*\s*cm\b")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_HTML_TAG = re.compile(r"<[^>]+>")


class Skip(Enum):
    NO_GALLERY = "no_gallery"
    INVALID = "invalid"


def blank_to_none(value: str | None) -> str | None:
    return (value or "").strip() or None


def strip_html(value: str | None) -> str | None:
    if not value:
        return None
    return blank_to_none(" ".join(html.unescape(_HTML_TAG.sub("", value)).split()))


def clean_tags(candidates: Iterable[str | None], limit: int = MAX_TAGS) -> list[str]:
    """Lowercase, trim, drop empties, de-duplicate keeping first occurrence, cap at `limit`."""
    seen: dict[str, None] = {}
    for candidate in candidates:
        tag = (candidate or "").strip().lower()
        if tag:
            seen.setdefault(tag)
        if len(seen) >= limit:
            break
    return list(seen)


def largest_cm_in_text(text: str | None) -> float | None:
    """Largest number written before 'cm', e.g. '207.5 x 308.1 cm'. Anything after 'Framed' is the frame."""
    if not text:
        return None
    head = re.split(r"framed", text, maxsplit=1, flags=re.IGNORECASE)[0] or text
    values = [float(n) for group in _CM_GROUP.findall(head) for n in _NUMBER.findall(group)]
    return max(values) if values else None


def size_class(max_cm: float | None) -> SizeClass:
    if max_cm is None:
        return "medium"
    if max_cm < 30:
        return "small"
    if max_cm < 100:
        return "medium"
    return "large" if max_cm < 250 else "monumental"


def dwell_minutes(size: str, is_highlight: bool) -> int:
    minutes = _BASE_DWELL[size] + (_HIGHLIGHT_BONUS if is_highlight else 0)
    return max(1, min(20, minutes))


def build_object(
    *,
    museum_id: str,
    source_id: str,
    title: str,
    gallery: str,
    artist: str | None = None,
    date: str | None = None,
    tags: Iterable[str | None] = (),
    interest_tags: Iterable[str | None] = (),
    department: str | None = None,
    medium: str | None = None,
    description: str | None = None,
    image_url: str | None = None,
    source_url: str | None = None,
    is_highlight: bool = False,
    max_cm: float | None = None,
) -> NormalizedObject | Skip:
    """Applies the shared rules. `gallery` must already be extracted from the source record."""
    if not gallery or not GALLERY_RE.fullmatch(gallery):
        return Skip.NO_GALLERY
    title = title.strip()
    if not title:
        return Skip.INVALID
    size = size_class(max_cm)
    cleaned = clean_tags(tags)
    kept = set(cleaned)
    try:
        return NormalizedObject(
            id=f"{museum_id}:{source_id}",
            museum_id=museum_id,
            source_id=source_id,
            title=title,
            artist=blank_to_none(artist),
            date=blank_to_none(date),
            tags=cleaned,
            # Tags are capped; an interest tag that fell off the end is not a tag of this object.
            interest_tags=[t for t in clean_tags(interest_tags, limit=len(cleaned) or 1) if t in kept],
            department=blank_to_none(department),
            medium=blank_to_none(medium),
            description=description,
            gallery=gallery,
            image_url=blank_to_none(image_url),
            source_url=blank_to_none(source_url),
            dwell_minutes=dwell_minutes(size, is_highlight),
            size_class=size,
            is_highlight=is_highlight,
        )
    except ValidationError:
        return Skip.INVALID


def _numeric_id(source_id: str) -> float:
    return int(source_id) if source_id.isascii() and source_id.isdigit() else float("inf")


def order_objects(objects: Iterable[NormalizedObject]) -> list[NormalizedObject]:
    """Highlights first, then ascending numeric source id."""
    return sorted(objects, key=lambda o: (not o.is_highlight, _numeric_id(o.source_id), o.source_id))


def chunk_objects(objects: Sequence[NormalizedObject], size: int = CHUNK_SIZE) -> list[list[NormalizedObject]]:
    return [list(objects[i : i + size]) for i in range(0, len(objects), size)]


def tag_label(tag: str) -> str:
    words = tag.split(" ")
    return " ".join(
        word if i and word in _SMALL_WORDS else word[:1].upper() + word[1:] for i, word in enumerate(words)
    )


def build_facets(objects: Iterable[NormalizedObject], *, interest_only: bool = True) -> list[Facet]:
    """The 40 most common tags; tags seen only once are left out when at least 10 tags repeat.

    Facets become the interest chips a visitor taps, so by default only each object's
    `interest_tags` are counted: subjects, styles, and collection areas, not materials and
    techniques such as "oil on canvas". Interest tags are a subset of the object's tags, so
    tapping a chip always matches something.
    """
    counts = Counter(tag for obj in objects for tag in (obj.interest_tags if interest_only else obj.tags))
    minimum = 2 if sum(1 for c in counts.values() if c >= 2) >= 10 else 1
    chosen = sorted(((t, c) for t, c in counts.items() if c >= minimum), key=lambda tc: (-tc[1], tc[0]))
    return [Facet(tag=t, label=tag_label(t), count=c) for t, c in chosen[:MAX_FACETS]]
