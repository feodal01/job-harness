from src.core.config import Settings
from src.geography.catalog import GeographyCatalog
from src.parsers.aggregators.getmatch.parser import create_adapter as getmatch_adapter
from src.parsers.aggregators.hh.parser import create_adapter as hh_adapter
from src.parsers.interfaces import SourceAdapter


def build_sources(settings: Settings, catalog: GeographyCatalog | None = None) -> dict[str, SourceAdapter]:
    """Adapters of the registry; `catalog` is the application's single geography catalog."""
    sources = (hh_adapter(settings.sources.hh_ru, catalog), getmatch_adapter(settings.sources.getmatch))
    return {source.id: source for source in sources}
