"""46d: fw battery reports are logged on change (or every 10 min)."""
import logging

from stackchan_mcp import esp32_client


def test_battery_event_logged_on_change_only(caplog):
    esp32_client._battery_log_state.update(key=None, at=0.0)
    with caplog.at_level(logging.INFO, logger=esp32_client.logger.name):
        esp32_client._log_battery_event({"percent": 80, "raw": 2400, "mv": 1950})
        esp32_client._log_battery_event({"percent": 80, "raw": 2401, "mv": 1951})
        esp32_client._log_battery_event({"percent": None, "raw": 900, "mv": 700, "reason": "adc_out_of_range"})
    lines = [r.getMessage() for r in caplog.records if "ESP32 battery" in r.getMessage()]
    assert len(lines) == 2
    assert "percent=80 raw=2400 mv=1950" in lines[0]
    assert "reason=adc_out_of_range" in lines[1]
