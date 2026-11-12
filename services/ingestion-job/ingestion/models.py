"""Pydantic mirrors of the JSON Schemas in /contracts. Written documents use extra="forbid" like the
schemas do; upstream records (which we do not own) use extra="ignore"."""

import datetime as dt
import re
from collections import deque
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

MUSEUM_ID_PATTERN = r"^[a-z][a-z0-9-]{1,30}$"
GALLERY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 .-]{0,15}$"
GALLERY_RE = re.compile(GALLERY_PATTERN)

MuseumId = Annotated[str, StringConstraints(pattern=MUSEUM_ID_PATTERN)]
Tag = Annotated[str, StringConstraints(min_length=1, pattern=r"^[^A-Z]+$")]
SizeClass = Literal["small", "medium", "large", "monumental"]


def to_doc(model: BaseModel) -> dict[str, Any]:
    """JSON-ready dict in the exact shape of the contract (aliases applied)."""
    return model.model_dump(mode="json", by_alias=True)


class _Doc(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _Upstream(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ---- object.schema.json / object-chunk.schema.json ----


class NormalizedObject(_Doc):
    id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{1,30}:[A-Za-z0-9._-]+$")]
    museum_id: MuseumId
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    artist: str | None
    date: str | None
    tags: list[Tag]
    department: str | None = None
    medium: str | None = None
    description: str | None = None
    gallery: Annotated[str, StringConstraints(min_length=1, pattern=GALLERY_PATTERN)]
    image_url: str | None
    source_url: str | None = None
    dwell_minutes: int = Field(ge=1, le=20)
    size_class: SizeClass
    is_highlight: bool

    @field_validator("tags")
    @classmethod
    def _tags_unique(cls, tags: list[str]) -> list[str]:
        if len(set(tags)) != len(tags):
            raise ValueError("tags must be unique")
        return tags

    @model_validator(mode="after")
    def _id_matches(self) -> "NormalizedObject":
        if self.id != f"{self.museum_id}:{self.source_id}":
            raise ValueError("id must be museum_id:source_id")
        return self


class ObjectChunk(_Doc):
    museum_id: MuseumId
    chunk_index: int = Field(ge=0)
    ingest_run_id: str = Field(min_length=1)
    objects: list[NormalizedObject] = Field(min_length=1, max_length=300)


# ---- museum.schema.json ----


class MuseumSource(_Doc):
    api_name: str = Field(min_length=1)
    api_base_url: str = Field(min_length=1)
    license: str = Field(min_length=1)


class Facet(_Doc):
    tag: Tag
    label: str = Field(min_length=1)
    count: int = Field(ge=1)


class Museum(_Doc):
    id: MuseumId
    name: str = Field(min_length=1)
    city: str
    source: MuseumSource
    data_notice: str = Field(min_length=20)
    object_count: int = Field(ge=0)
    skipped_no_gallery_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    facets: list[Facet] = Field(max_length=60)
    last_ingested_at: str
    ingest_run_id: str = Field(min_length=1)


# ---- floor-graph.schema.json ----

_Kind = Literal["level", "ramp", "elevator", "stairs", "escalator"]


class Verification(_Doc):
    status: Literal["unverified", "partially_verified", "verified"]
    notes: str | None
    verified_on: dt.date | None = None


class GraphNode(_Doc):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1)
    kind: Literal["gallery", "hub", "entrance"]
    floor: str = Field(min_length=1, max_length=4)
    wing: str = Field(min_length=1)
    galleries: list[Annotated[str, StringConstraints(min_length=1)]]
    sensory: list[Literal["loud", "bright", "crowded"]]
    has_seating: bool
    near_restroom: bool
    near_cafe: bool

    @field_validator("galleries", "sensory")
    @classmethod
    def _unique(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("items must be unique")
        return values


class GraphEdge(_Doc):
    from_: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    walk_minutes: float = Field(gt=0, le=30)
    step_free: bool
    kind: _Kind
    name: str | None

    @model_validator(mode="after")
    def _conditional_rules(self) -> "GraphEdge":
        named = self.name is not None and len(self.name) >= 3
        if not self.step_free and not named:
            raise ValueError("an edge that is not step-free needs a name of 3+ characters")
        if self.kind in ("stairs", "escalator") and self.step_free:
            raise ValueError("stairs and escalators cannot be step-free")
        if self.kind == "elevator" and not named:
            raise ValueError("an elevator edge needs a name of 3+ characters")
        return self


class FloorGraph(_Doc):
    museum_id: MuseumId
    version: str = Field(min_length=1)
    source: str = Field(min_length=1)
    verification: Verification
    entrance_node_id: str = Field(min_length=1)
    nodes: list[GraphNode] = Field(min_length=2)
    edges: list[GraphEdge] = Field(min_length=1)

    @model_validator(mode="after")
    def _cross_field_rules(self) -> "FloorGraph":
        ids = [n.id for n in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError("node ids must be unique")
        if self.entrance_node_id not in ids:
            raise ValueError("entrance_node_id does not name a node")
        all_galleries = [g for n in self.nodes for g in n.galleries]
        if len(set(all_galleries)) != len(all_galleries):
            raise ValueError("a gallery number may appear in at most one node")
        neighbours: dict[str, list[str]] = {i: [] for i in ids}
        for e in self.edges:
            if e.from_ not in neighbours or e.to not in neighbours:
                raise ValueError(f"edge {e.from_} -> {e.to} names an unknown node")
            neighbours[e.from_].append(e.to)
            neighbours[e.to].append(e.from_)
        reached = {self.entrance_node_id}
        queue = deque([self.entrance_node_id])
        while queue:
            for nxt in neighbours[queue.popleft()]:
                if nxt not in reached:
                    reached.add(nxt)
                    queue.append(nxt)
        if len(reached) != len(ids):
            raise ValueError("the floor graph is not connected")
        return self

    def gallery_numbers(self) -> set[str]:
        return {g for n in self.nodes for g in n.galleries}


# ---- ingest-museum.schema.json ----


class IngestMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    museum_id: MuseumId
    reason: Literal["add", "refresh"] = "add"
    # Defaults are not validated, so an absent key is None but an explicit null is rejected,
    # exactly as the JSON Schema does.
    max_objects: int = Field(default=None, ge=1, le=5000)
    requested_at: dt.datetime = None  # type: ignore[assignment]


def parse_ingest_message(data: bytes) -> IngestMessage:
    """Raises ValueError (pydantic's ValidationError is one) for anything that is not a valid message."""
    return IngestMessage.model_validate_json(data.decode("utf-8"))


# ---- upstream subsets (met-object.schema.json, aic-artwork.schema.json) ----


class MetMeasurement(_Upstream):
    elementName: str | None = None
    elementMeasurements: dict[str, Any] | None = None


class MetTag(_Upstream):
    term: str


class MetObject(_Upstream):
    objectID: int
    title: str
    GalleryNumber: str
    isHighlight: bool | None = None
    primaryImage: str | None = None
    primaryImageSmall: str | None = None
    department: str | None = None
    objectName: str | None = None
    culture: str | None = None
    period: str | None = None
    artistDisplayName: str | None = None
    objectDate: str | None = None
    medium: str | None = None
    dimensions: str | None = None
    measurements: list[MetMeasurement] | None = None
    classification: str | None = None
    objectURL: str | None = None
    tags: list[MetTag] | None = None


class AicArtwork(_Upstream):
    id: int
    title: str
    artist_display: str | None = None
    artist_title: str | None = None
    date_display: str | None = None
    medium_display: str | None = None
    dimensions: str | None = None
    is_on_view: bool | None = None
    gallery_id: int | None = None
    gallery_title: str | None = None
    image_id: str | None = None
    department_title: str | None = None
    classification_title: str | None = None
    style_title: str | None = None
    subject_titles: list[str] | None = None
    term_titles: list[str] | None = None
    short_description: str | None = None
    is_boosted: bool | None = None
