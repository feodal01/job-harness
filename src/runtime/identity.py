from importlib.metadata import PackageNotFoundError, version

PROTOCOL_VERSION = 1


def application_version() -> str:
    try:
        return version("hh-search-prototype")
    except PackageNotFoundError:
        return "0.4.0"
