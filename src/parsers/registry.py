from src.core.config import Settings
from src.parsers.aggregators.getmatch.parser import create_adapter as getmatch_adapter
from src.parsers.aggregators.hh.parser import create_adapter as hh_adapter
from src.parsers.interfaces import SourceAdapter


def build_sources(settings: Settings) -> dict[str, SourceAdapter]:
    sources = (hh_adapter(settings.sources.hh_ru), getmatch_adapter(settings.sources.getmatch))
    return {source.id: source for source in sources}
