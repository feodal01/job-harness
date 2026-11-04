"""Connection data for one employer board, without employer-specific parsing rules."""

from dataclasses import dataclass, field
from ipaddress import ip_address
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Board:
    url: str
    company: str
    native_filters: dict[str, str | list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        address = urlsplit(self.url)
        if address.scheme != "https" or not address.hostname or address.username or address.password:
            raise ValueError("An ATS board requires a public HTTPS URL without credentials")
        hostname = address.hostname
        if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
            raise ValueError("An ATS board requires a public hostname")
        try:
            ip = ip_address(hostname)
        except ValueError:
            pass
        else:
            if not ip.is_global:
                raise ValueError("An ATS board requires a public address")
        if not self.company.strip():
            raise ValueError("An ATS board requires a company name")
        for name, value in self.native_filters.items():
            values = value if isinstance(value, list) else [value]
            if (
                not isinstance(name, str)
                or not name.strip()
                or not values
                or any(not isinstance(item, str) or not item.strip() for item in values)
            ):
                raise ValueError("Native filters require named, nonempty string values")
