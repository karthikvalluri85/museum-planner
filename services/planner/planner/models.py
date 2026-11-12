"""Pydantic models: the store documents the planner reads, and the plan request it accepts.

Store models mirror contracts/firestore/*.schema.json. They are this service's own copies:
no code is shared with the ingestion job.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    model_validator,
)

MUSEUM_ID_PATTERN = r"^[a-z][a-z0-9-]{1,30}$"

SizeClass = Literal["small", "medium", "large", "monumental"]
EdgeKind = Literal["level", "ramp", "elevator", "stairs", "escalator"]


class StoreDoc(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MuseumSource(StoreDoc):
    api_name: str
    api_base_url: str
    license: str


class Facet(StoreDoc):
    tag: str = Field(min_length=1)
    label: str = Field(min_length=1)
    count: int = Field(ge=1)


class Museum(StoreDoc):
    id: str
    name: str = Field(min_length=1)
    city: str
    source: MuseumSource
    data_notice: str = Field(min_length=20)
    object_count: int = Field(ge=0)
    skipped_no_gallery_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    facets: list[Facet]
    last_ingested_at: str
    ingest_run_id: str


class MuseumObject(StoreDoc):
    id: str
    museum_id: str
    source_id: str
    title: str = Field(min_length=1)
    artist: str | None
    date: str | None
    tags: list[str]
    department: str | None = None
    medium: str | None = None
    description: str | None = None
    gallery: str = Field(min_length=1)
    image_url: str | None
    source_url: str | None = None
    dwell_minutes: int = Field(ge=1, le=20)
    size_class: SizeClass
    is_highlight: bool


class ObjectChunk(StoreDoc):
    museum_id: str
    chunk_index: int = Field(ge=0)
    ingest_run_id: str
    objects: list[MuseumObject]


class Verification(StoreDoc):
    status: Literal["unverified", "partially_verified", "verified"]
    notes: str | None
    verified_on: str | None = None


class Node(StoreDoc):
    id: str
    name: str = Field(min_length=1)
    kind: Literal["gallery", "hub", "entrance"]
    floor: str
    wing: str = Field(min_length=1)
    galleries: list[str]
    sensory: list[Literal["loud", "bright", "crowded"]]
    has_seating: bool
    near_restroom: bool
    near_cafe: bool


class Edge(StoreDoc):
    from_: str = Field(alias="from")
    to: str
    walk_minutes: float = Field(gt=0, le=30)
    step_free: bool
    kind: EdgeKind
    name: str | None

    @model_validator(mode="after")
    def _honest_access(self) -> Edge:
        # A graph that calls stairs step-free, or leaves stairs unnamed, would let the
        # planner make a false step-free claim or an unnamed one. Reject it on load.
        if self.kind in ("stairs", "escalator") and self.step_free:
            raise ValueError(f"{self.kind} edge {self.from_}-{self.to} is marked step_free")
        if not self.step_free and (self.name is None or len(self.name.strip()) < 3):
            raise ValueError(f"non-step-free edge {self.from_}-{self.to} has no name")
        return self


class FloorGraph(StoreDoc):
    museum_id: str
    version: str
    source: str = Field(min_length=1)
    verification: Verification
    entrance_node_id: str
    nodes: list[Node] = Field(min_length=2)
    edges: list[Edge] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> FloorGraph:
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate node ids")
        if self.entrance_node_id not in ids:
            raise ValueError(f"entrance node {self.entrance_node_id} does not exist")
        known = set(ids)
        for edge in self.edges:
            if edge.from_ not in known or edge.to not in known:
                raise ValueError(f"edge {edge.from_}-{edge.to} has an unknown endpoint")
        galleries = [g for n in self.nodes for g in n.galleries]
        if len(galleries) != len(set(galleries)):
            raise ValueError("a gallery number appears in more than one node")
        return self


# ---- request ----------------------------------------------------------------

Interest = Annotated[str, StringConstraints(min_length=1, max_length=60)]


class Constraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_free: StrictBool = False
    limited_stamina: StrictBool = False
    max_walking_minutes: Annotated[StrictInt, Field(ge=5, le=120)] | None = None
    with_young_child: StrictBool = False
    low_sensory: StrictBool = False
    short_visit: StrictBool = False


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    museum_id: Annotated[str, StringConstraints(pattern=MUSEUM_ID_PATTERN)]
    interests: list[Interest] = Field(default_factory=list, max_length=12)
    time_budget_minutes: Annotated[StrictInt, Field(ge=15, le=240)]
    constraints: Constraints = Field(default_factory=Constraints)
    current_gallery: str | None = None
    exclude_object_ids: list[str] = Field(default_factory=list, max_length=300)
    skip_wings: list[str] = Field(default_factory=list, max_length=30)
