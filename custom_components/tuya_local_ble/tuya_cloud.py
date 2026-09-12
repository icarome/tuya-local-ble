"""Tuya Cloud API helper for Tuya Local BLE."""
from __future__ import annotations

from datetime import timedelta
import functools
import hashlib
import hmac
import logging
import time
from typing import Any

import requests

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    EVENT_DYNAMIC_PASSWORD_GENERATED,
    SIGNAL_DYNAMIC_PASSWORD_UPDATE,
)

_LOGGER = logging.getLogger(__name__)


def calc_sign(msg: str, secret: str) -> str:
    """Calculate HMAC-SHA256 signature for Tuya OpenAPI."""
    return hmac.new(
        secret.encode("utf-8"),
        msg.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()


def get_tuya_credentials(hass: HomeAssistant) -> tuple[str, str, str] | None:
    """Retrieve Tuya Cloud credentials from LocalTuya or Tuya Local BLE entries."""
    # 1. Search LocalTuya config entries
    for entry in hass.config_entries.async_entries("localtuya"):
        data = entry.data
        client_id = data.get("client_id")
        secret = data.get("client_secret")
        region = data.get("region", "us")
        if client_id and secret:
            return client_id, secret, region

    # 2. Search Tuya Local BLE entries or options
    for entry in hass.config_entries.async_entries(DOMAIN):
        for source in (entry.options, entry.data):
            client_id = source.get("client_id")
            secret = source.get("client_secret")
            region = source.get("region", "us")
            if client_id and secret:
                return client_id, secret, region

    # 3. Check LocalTuya loaded data
    lt_data = hass.data.get("localtuya")
    if isinstance(lt_data, dict):
        cloud = lt_data.get("DATA_CLOUD") or lt_data.get("cloud_data")
        if cloud and hasattr(cloud, "_client_id") and hasattr(cloud, "_secret"):
            return cloud._client_id, cloud._secret, getattr(cloud, "_region_code", "us")

    return None


class TuyaCloudClient:
    """HTTP client for communicating with Tuya Cloud OpenAPI."""

    def __init__(
        self,
        hass: HomeAssistant,
        client_id: str,
        client_secret: str,
        region: str,
    ) -> None:
        self._hass = hass
        self._client_id = client_id
        self._client_secret = client_secret
        self._region = region
        self._base_url = f"https://openapi.tuya{region}.com"
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    def _generate_payload(
        self,
        method: str,
        timestamp: str,
        url: str,
        access_token: str = "",
    ) -> str:
        payload = self._client_id + access_token + timestamp + method + "\n"
        payload += hashlib.sha256(b"").hexdigest() + "\n\n" + url
        return payload

    def _sync_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        full_url = self._base_url + url
        resp = requests.request(method, full_url, headers=headers, timeout=10)
        return resp.json()

    async def _async_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        return await self._hass.async_add_executor_job(
            functools.partial(self._sync_request, method, url, headers)
        )

    async def async_get_token(self, force_refresh: bool = False) -> str:
        """Obtain a valid access token."""
        now = time.time()
        if not force_refresh and self._access_token and now < (self._token_expires_at - 60):
            return self._access_token

        t = str(int(now * 1000))
        url = "/v1.0/token?grant_type=1"
        payload = self._generate_payload("GET", t, url)
        sign = calc_sign(payload, self._client_secret)
        headers = {
            "client_id": self._client_id,
            "sign": sign,
            "t": t,
            "sign_method": "HMAC-SHA256",
        }

        data = await self._async_request("GET", url, headers)
        if not data.get("success"):
            code = data.get("code", "unknown")
            msg = data.get("msg", "Failed to obtain Tuya access token")
            raise HomeAssistantError(f"Tuya Cloud token error ({code}): {msg}")

        result = data.get("result", {})
        token = result.get("access_token")
        if not token:
            raise HomeAssistantError(f"Tuya Cloud returned no access token: {data}")

        self._access_token = token
        expire_time = result.get("expire_time", 7200)
        self._token_expires_at = now + expire_time
        return self._access_token

    async def async_get_dynamic_password(self, device_id: str) -> str:
        """Request a 5-minute dynamic password for the given Tuya device ID."""
        token = await self.async_get_token()
        now = time.time()
        t = str(int(now * 1000))
        url = f"/v1.0/devices/{device_id}/door-lock/dynamic-password"
        payload = self._generate_payload("GET", t, url, access_token=token)
        sign = calc_sign(payload, self._client_secret)
        headers = {
            "client_id": self._client_id,
            "access_token": token,
            "sign": sign,
            "t": t,
            "sign_method": "HMAC-SHA256",
        }

        data = await self._async_request("GET", url, headers)

        # Retry once if token expired or invalid
        if not data.get("success") and data.get("code") in (1010, 1011, 1012):
            _LOGGER.debug("Tuya access token expired, refreshing and retrying...")
            token = await self.async_get_token(force_refresh=True)
            t = str(int(time.time() * 1000))
            payload = self._generate_payload("GET", t, url, access_token=token)
            sign = calc_sign(payload, self._client_secret)
            headers["access_token"] = token
            headers["sign"] = sign
            headers["t"] = t
            data = await self._async_request("GET", url, headers)

        if not data.get("success"):
            code = data.get("code", "unknown")
            msg = data.get("msg", "Failed to fetch dynamic password")
            raise HomeAssistantError(f"Tuya Cloud dynamic password error ({code}): {msg}")

        result = data.get("result", {})
        pwd = result.get("dynamic_password")
        if not pwd:
            raise HomeAssistantError(f"No dynamic_password in Tuya response: {data}")

        return str(pwd)


async def async_get_cloud_client(hass: HomeAssistant) -> TuyaCloudClient:
    """Get or create the cached TuyaCloudClient."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    client = domain_data.get("cloud_client")
    if client is not None:
        return client

    credentials = get_tuya_credentials(hass)
    if not credentials:
        raise HomeAssistantError(
            "Tuya Cloud credentials not found. Please ensure LocalTuya is configured "
            "with your Tuya IoT API credentials (client_id, client_secret, region)."
        )

    client_id, client_secret, region = credentials
    client = TuyaCloudClient(hass, client_id, client_secret, region)
    domain_data["cloud_client"] = client
    return client


async def async_generate_and_dispatch_dynamic_password(
    hass: HomeAssistant,
    device_id: str,
    device_name: str = "Smart Lock",
    notify: bool = True,
) -> dict[str, Any]:
    """Generate dynamic password, notify listeners, and optionally create persistent notification."""
    client = await async_get_cloud_client(hass)
    raw_pwd = await client.async_get_dynamic_password(device_id)

    now = dt_util.now()
    expires_at = now + timedelta(minutes=5)
    expires_str = expires_at.strftime("%H:%M:%S")

    data = {
        "dynamic_password": raw_pwd,
        "expires_at": expires_at.isoformat(),
        "expires_time": expires_str,
        "valid_for_minutes": 5,
        "keypad_entry": f"{raw_pwd}#",
        "device_id": device_id,
    }

    # Dispatch to sensor entities
    async_dispatcher_send(
        hass,
        f"{SIGNAL_DYNAMIC_PASSWORD_UPDATE}_{device_id}",
        data,
    )

    # Fire HA event
    hass.bus.async_fire(EVENT_DYNAMIC_PASSWORD_GENERATED, data)

    # Display persistent notification if requested
    if notify:
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": f"{device_name} - One-Time Password",
                "message": (
                    f"### One-Time Password Generated\n\n"
                    f"- **Passcode:** `{raw_pwd}`\n"
                    f"- **Keypad Entry:** `{raw_pwd}#`\n"
                    f"- **Valid until:** {expires_str} (5 minutes)\n\n"
                    f"Enter **{raw_pwd}#** on the lock keypad to unlock."
                ),
                "notification_id": f"tuya_ble_otp_{device_id}",
            },
        )

    return data
