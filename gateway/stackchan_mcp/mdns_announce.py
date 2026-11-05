"""Opt-in mDNS announce for the ESP32 WebSocket gateway port (ticket 35).

Default **off**. Set ``STACKCHAN_MDNS=1`` to advertise
``_xiaoya-gateway._tcp`` so firmware can browse instead of hard-coding a
rotating LAN IP. Uses pure-Python ``zeroconf`` (Bonjour-compatible on
macOS; Avahi-compatible on Linux). Missing ``zeroconf`` or announce
failures never block gateway start.
"""

from __future__ import annotations

import logging
import os
import socket
from typing import Any, Callable

logger = logging.getLogger(__name__)

# DNS-SD type without trailing ``.local.`` — zeroconf ServiceInfo adds it.
SERVICE_TYPE = "_xiaoya-gateway._tcp.local."
DEFAULT_INSTANCE = "xiaoya-gateway"
# Local gateway speaks XiaoZhi WS at the root of WS_PORT (not /xiaozhi/v1/).
DEFAULT_WS_PATH = "/"

ZeroconfFactory = Callable[[], Any]
ServiceInfoFactory = Callable[..., Any]


def mdns_enabled() -> bool:
    """Return whether gateway should advertise on mDNS (default off)."""
    value = os.getenv("STACKCHAN_MDNS", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def service_properties(*, path: str = DEFAULT_WS_PATH, proto: str = "ws") -> dict[str, str]:
    """TXT record contract for firmware browse/resolve."""
    return {
        "path": path or "/",
        "proto": proto,
        "txtvers": "1",
    }


def pick_advertise_ip(host: str = "0.0.0.0") -> str | None:
    """Choose an IPv4 address to put in the mDNS A/AAAA answer.

    Prefer an explicit non-wildcard ``HOST``, else ``VISION_HOST`` (already
    the LAN IP as seen from the board), else UDP-connect trick to a public
    DNS IP (no packets sent) to learn the default-route interface address.
    """
    for candidate in (host, os.getenv("VISION_HOST", "").strip()):
        if candidate and candidate not in {"0.0.0.0", "::", "[::]"}:
            try:
                socket.inet_aton(candidate)
                return candidate
            except OSError:
                pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError as exc:
        logger.warning("mDNS: could not discover LAN IPv4: %s", exc)
        return None


def build_service_info(
    *,
    port: int,
    host: str = "0.0.0.0",
    instance: str | None = None,
    path: str = DEFAULT_WS_PATH,
    addresses: list[bytes] | None = None,
    service_info_cls: ServiceInfoFactory | None = None,
) -> Any | None:
    """Build a ``ServiceInfo`` for the gateway WS port, or None if no IP."""
    if service_info_cls is None:
        try:
            from zeroconf import ServiceInfo as service_info_cls  # type: ignore[assignment]
        except ImportError:
            logger.warning(
                "STACKCHAN_MDNS=1 but zeroconf is not installed; "
                "install with: uv sync --extra mdns"
            )
            return None

    ip = None
    if addresses is None:
        ip = pick_advertise_ip(host)
        if ip is None:
            return None
        addresses = [socket.inet_aton(ip)]

    name = (instance or os.getenv("STACKCHAN_MDNS_INSTANCE", DEFAULT_INSTANCE)).strip()
    if not name:
        name = DEFAULT_INSTANCE
    full_name = f"{name}.{SERVICE_TYPE}"
    props = service_properties(path=path)
    return service_info_cls(
        SERVICE_TYPE,
        full_name,
        addresses=addresses,
        port=int(port),
        properties=props,
        server=f"{name}.local.",
    )


class GatewayMdnsAnnouncer:
    """Register / unregister ``_xiaoya-gateway._tcp`` around gateway lifetime.

    Inject ``zeroconf_factory`` / ``service_info_cls`` in tests to avoid a
    real multicast socket.
    """

    def __init__(
        self,
        *,
        zeroconf_factory: ZeroconfFactory | None = None,
        service_info_cls: ServiceInfoFactory | None = None,
    ) -> None:
        self._zeroconf_factory = zeroconf_factory
        self._service_info_cls = service_info_cls
        self._zc: Any | None = None
        self._info: Any | None = None

    @property
    def active(self) -> bool:
        return self._zc is not None and self._info is not None

    @property
    def info(self) -> Any | None:
        return self._info

    def start(self, *, host: str, port: int, path: str = DEFAULT_WS_PATH) -> bool:
        """Advertise the WS port. Returns True when registration succeeded."""
        if self.active:
            return True
        if not mdns_enabled():
            return False

        factory = self._zeroconf_factory
        if factory is None:
            try:
                from zeroconf import Zeroconf
            except ImportError:
                logger.warning(
                    "STACKCHAN_MDNS=1 but zeroconf is not installed; "
                    "install with: uv sync --extra mdns"
                )
                return False
            factory = Zeroconf

        info = build_service_info(
            port=port,
            host=host,
            path=path,
            service_info_cls=self._service_info_cls,
        )
        if info is None:
            return False

        try:
            zc = factory()
            zc.register_service(info)
        except Exception as exc:  # noqa: BLE001 — never block gateway start
            logger.warning("mDNS announce failed (gateway continues): %s", exc)
            return False

        self._zc = zc
        self._info = info
        props = service_properties(path=path)
        logger.info(
            "mDNS advertising %s port=%d txt=%s (STACKCHAN_MDNS=1)",
            SERVICE_TYPE,
            port,
            props,
        )
        return True

    def stop(self) -> None:
        """Unregister and close; safe to call when never started."""
        zc, info = self._zc, self._info
        self._zc = None
        self._info = None
        if zc is None:
            return
        try:
            if info is not None:
                zc.unregister_service(info)
        except Exception as exc:  # noqa: BLE001
            logger.debug("mDNS unregister: %s", exc)
        try:
            zc.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("mDNS close: %s", exc)
        logger.info("mDNS announce stopped")
