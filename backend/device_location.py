"""Bounded one-shot device location contract; never acquire server location."""

from __future__ import annotations

import math
from typing import Any


class DeviceLocationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self), "capability": "device.location", "operation": "current"}


def normalize_location_options(value: Any) -> dict[str, int]:
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {"timeout", "maximumAge"}:
        raise DeviceLocationError(
            "device_location_request_invalid", "Location options support only timeout and maximumAge"
        )
    result = {"timeout": value.get("timeout", 10000), "maximumAge": value.get("maximumAge", 0)}
    if type(result["timeout"]) is not int or not 1000 <= result["timeout"] <= 30000:
        raise DeviceLocationError(
            "device_location_request_invalid", "Location timeout must be an integer from 1000 to 30000 ms"
        )
    if type(result["maximumAge"]) is not int or not 0 <= result["maximumAge"] <= 300000:
        raise DeviceLocationError(
            "device_location_request_invalid", "Location maximumAge must be an integer from 0 to 300000 ms"
        )
    return result


def normalize_location_result(value: Any) -> dict[str, float | int]:
    fields = {"latitude", "longitude", "accuracy", "timestamp"}
    if not isinstance(value, dict) or set(value) != fields:
        raise DeviceLocationError("device_location_invalid", "Device returned an invalid location result")
    if any(type(item) not in {int, float} or not math.isfinite(item) for item in value.values()):
        raise DeviceLocationError("device_location_invalid", "Device location values must be finite numbers")
    if not -90 <= value["latitude"] <= 90 or not -180 <= value["longitude"] <= 180:
        raise DeviceLocationError(
            "device_location_invalid", "Device location coordinates are outside their valid range"
        )
    if value["accuracy"] < 0 or value["timestamp"] < 0:
        raise DeviceLocationError(
            "device_location_invalid", "Device location accuracy and timestamp cannot be negative"
        )
    return dict(value)
