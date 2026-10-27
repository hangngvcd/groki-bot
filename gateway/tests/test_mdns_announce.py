"""Unit tests for opt-in gateway mDNS announce (ticket 35).

No real multicast: Zeroconf / ServiceInfo are stubbed.
"""

from __future__ import annotations

import socket

import pytest

from stackchan_mcp import mdns_announce as mdns


class FakeServiceInfo:
    def __init__(self, type_: str, name: str, **kwargs):
        self.type = type_
        self.name = name
        self.kwargs = kwargs
        self.port = kwargs.get("port")
        self.properties = kwargs.get("properties")
        self.addresses = kwargs.get("addresses")


class FakeZeroconf:
    def __init__(self):
        self.registered: list[FakeServiceInfo] = []
        self.unregistered: list[FakeServiceInfo] = []
        self.closed = False

    def register_service(self, info):
        self.registered.append(info)

    def unregister_service(self, info):
        self.unregistered.append(info)

    def close(self):
        self.closed = True


def test_mdns_default_off(monkeypatch):
    monkeypatch.delenv("STACKCHAN_MDNS", raising=False)
    assert mdns.mdns_enabled() is False
    monkeypatch.setenv("STACKCHAN_MDNS", "0")
    assert mdns.mdns_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_mdns_opt_in(monkeypatch, value):
    monkeypatch.setenv("STACKCHAN_MDNS", value)
    assert mdns.mdns_enabled() is True


def test_service_properties_contract():
    assert mdns.service_properties() == {
        "path": "/",
        "proto": "ws",
        "txtvers": "1",
    }


def test_pick_advertise_ip_prefers_explicit_host(monkeypatch):
    monkeypatch.delenv("VISION_HOST", raising=False)
    assert mdns.pick_advertise_ip("192.0.2.10") == "192.0.2.10"


def test_pick_advertise_ip_uses_vision_host(monkeypatch):
    monkeypatch.setenv("VISION_HOST", "192.0.2.20")
    assert mdns.pick_advertise_ip("0.0.0.0") == "192.0.2.20"


def test_build_service_info_shape(monkeypatch):
    monkeypatch.setenv("VISION_HOST", "192.0.2.30")
    info = mdns.build_service_info(
        port=8765,
        host="0.0.0.0",
        service_info_cls=FakeServiceInfo,
    )
    assert info is not None
    assert info.type == mdns.SERVICE_TYPE
    assert info.name.startswith("xiaoya-gateway.")
    assert info.port == 8765
    assert info.properties["proto"] == "ws"
    assert info.properties["path"] == "/"
    assert info.addresses == [socket.inet_aton("192.0.2.30")]


def test_announcer_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("STACKCHAN_MDNS", "0")
    zc = FakeZeroconf()
    announcer = mdns.GatewayMdnsAnnouncer(
        zeroconf_factory=lambda: zc,
        service_info_cls=FakeServiceInfo,
    )
    assert announcer.start(host="0.0.0.0", port=8765) is False
    assert announcer.active is False
    assert zc.registered == []
    announcer.stop()
    assert zc.closed is False


def test_announcer_start_stop(monkeypatch):
    monkeypatch.setenv("STACKCHAN_MDNS", "1")
    monkeypatch.setenv("VISION_HOST", "192.0.2.40")
    zc = FakeZeroconf()
    announcer = mdns.GatewayMdnsAnnouncer(
        zeroconf_factory=lambda: zc,
        service_info_cls=FakeServiceInfo,
    )
    assert announcer.start(host="0.0.0.0", port=8765) is True
    assert announcer.active is True
    assert len(zc.registered) == 1
    assert zc.registered[0].port == 8765
    assert zc.registered[0].properties["txtvers"] == "1"

    announcer.stop()
    assert announcer.active is False
    assert zc.unregistered == zc.registered
    assert zc.closed is True


def test_announcer_register_failure_does_not_raise(monkeypatch):
    monkeypatch.setenv("STACKCHAN_MDNS", "1")
    monkeypatch.setenv("VISION_HOST", "192.0.2.50")

    class BoomZeroconf(FakeZeroconf):
        def register_service(self, info):
            raise OSError("multicast blocked")

    announcer = mdns.GatewayMdnsAnnouncer(
        zeroconf_factory=BoomZeroconf,
        service_info_cls=FakeServiceInfo,
    )
    assert announcer.start(host="0.0.0.0", port=8765) is False
    assert announcer.active is False


@pytest.mark.asyncio
async def test_gateway_wires_mdns_start_stop(monkeypatch):
    """Gateway.start/stop drive the announcer when STACKCHAN_MDNS=1."""
    from stackchan_mcp.gateway import Gateway

    monkeypatch.setenv("STACKCHAN_MDNS", "1")
    monkeypatch.setenv("WS_PORT", "0")
    monkeypatch.setenv("CAPTURE_PORT", "0")
    monkeypatch.setenv("VISION_HOST", "192.0.2.60")
    monkeypatch.setenv("STACKCHAN_FACE_TRACKER_AUTOSTART", "0")

    zc = FakeZeroconf()
    gw = Gateway()
    gw.mdns_announcer = mdns.GatewayMdnsAnnouncer(
        zeroconf_factory=lambda: zc,
        service_info_cls=FakeServiceInfo,
    )
    await gw.start()
    assert gw.mdns_announcer.active is True
    assert len(zc.registered) == 1
    await gw.stop()
    assert gw.mdns_announcer.active is False
    assert zc.closed is True
