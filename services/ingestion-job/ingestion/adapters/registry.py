from ingestion.adapters.aic import AicAdapter
from ingestion.adapters.base import Adapter
from ingestion.adapters.met import MetAdapter

ADAPTERS: dict[str, Adapter] = {adapter.museum_id: adapter for adapter in (MetAdapter(), AicAdapter())}
