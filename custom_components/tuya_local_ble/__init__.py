"""The Tuya BLE integration."""
from __future__ import annotations

import logging
import time
from typing import Any

from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS as BLEAK_EXCEPTIONS, get_device

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.match import ADDRESS, BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .tuya_ble import SERVICE_UUID, TuyaBLEDevice

from .keyman import HASSTuyaBLEDeviceManager
from .const import DOMAIN, SERVICE_GET_DYNAMIC_PASSWORD
from .devices import TuyaBLECoordinator, TuyaBLEData, get_device_product_info
from .tuya_cloud import async_generate_and_dispatch_dynamic_password

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.LOCK,
]

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Tuya Local BLE component services."""
    async def async_handle_request_status(call) -> None:
        """Handle request_status service call."""
        _LOGGER.debug("Request status service called")
        for entry_id, data in list(hass.data.get(DOMAIN, {}).items()):
            if isinstance(data, TuyaBLEData):
                _LOGGER.info("%s: Service requesting status update", data.device.address)
                hass.async_create_task(data.device.update())

    if not hass.services.has_service(DOMAIN, "request_status"):
        hass.services.async_register(
            DOMAIN,
            "request_status",
            async_handle_request_status,
        )

    async def async_handle_get_dynamic_password(call) -> dict[str, Any]:
        """Handle get_dynamic_password service call."""
        _LOGGER.debug("Get dynamic password service called with data: %s", call.data)
        target_device_id = call.data.get("device_id")
        notify = call.data.get("notify", True)

        resolved_device_id: str | None = None
        device_name: str = "Smart Lock"

        # 1. If target_device_id was provided, resolve it
        if target_device_id:
            target_str = str(target_device_id).strip()

            # Check if target_str is a Home Assistant device registry ID
            dev_reg = dr.async_get(hass)
            ha_device = dev_reg.async_get(target_str)
            if ha_device:
                if ha_device.name:
                    device_name = ha_device.name
                for domain_id, address in ha_device.identifiers:
                    if domain_id == DOMAIN:
                        for data in hass.data.get(DOMAIN, {}).values():
                            if (
                                isinstance(data, TuyaBLEData)
                                and data.device.address.upper() == address.upper()
                            ):
                                resolved_device_id = data.device.device_id
                                break
                    if resolved_device_id:
                        break

            # If not resolved from HA device registry, check if target_str matches device_id or address
            if not resolved_device_id:
                for data in hass.data.get(DOMAIN, {}).values():
                    if isinstance(data, TuyaBLEData):
                        if (
                            data.device.device_id == target_str
                            or data.device.address.upper() == target_str.upper()
                        ):
                            resolved_device_id = data.device.device_id
                            device_name = data.device.name or device_name
                            break

            # Or target_str might directly be the raw Tuya device ID
            if not resolved_device_id:
                resolved_device_id = target_str

        # 2. If no target_device_id or not yet resolved, find configured lock or first device
        if not resolved_device_id:
            for data in hass.data.get(DOMAIN, {}).values():
                if isinstance(data, TuyaBLEData):
                    if (
                        data.device.category == "jtmspro"
                        or data.device.product_id == "rppmvevx"
                    ):
                        if data.device.device_id:
                            resolved_device_id = data.device.device_id
                            device_name = data.device.name or device_name
                            break

        if not resolved_device_id:
            for data in hass.data.get(DOMAIN, {}).values():
                if isinstance(data, TuyaBLEData) and data.device.device_id:
                    resolved_device_id = data.device.device_id
                    device_name = data.device.name or device_name
                    break

        # Fallback to reading devices.json
        if not resolved_device_id:
            try:
                import json
                import os
                from .const import CONF_CRED_FILE

                devicedata_path = os.path.join(hass.config.config_dir, CONF_CRED_FILE)
                if os.path.exists(devicedata_path):
                    with open(devicedata_path) as f:
                        dev_data = json.load(f)
                    for addr_info in dev_data.values():
                        if addr_info.get("device_id"):
                            resolved_device_id = addr_info.get("device_id")
                            device_name = addr_info.get("device_name", device_name)
                            break
            except Exception:
                pass

        if not resolved_device_id:
            raise HomeAssistantError("Could not determine Tuya device_id for smart lock.")

        return await async_generate_and_dispatch_dynamic_password(
            hass,
            device_id=resolved_device_id,
            device_name=device_name,
            notify=notify,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_GET_DYNAMIC_PASSWORD):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_DYNAMIC_PASSWORD,
            async_handle_get_dynamic_password,
            supports_response=SupportsResponse.OPTIONAL,
        )

    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tuya BLE from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address.upper(), True
    ) or await get_device(address)
    if not ble_device:
        raise ConfigEntryNotReady(
            f"Could not find Tuya BLE device with address {address}"
        )
    manager = HASSTuyaBLEDeviceManager(hass, entry.options.copy())
    device = TuyaBLEDevice(manager, ble_device)
    await device.initialize()
    product_info = get_device_product_info(device)

    coordinator = TuyaBLECoordinator(hass, device)

    '''
    try:
        await device.update()
    except BLEAK_EXCEPTIONS as ex:
        raise ConfigEntryNotReady(
            f"Could not communicate with Tuya BLE device with address {address}"
        ) from ex
    '''
    #hass.async_create_task(device.update())

    try:
        await device.update()
        last_battery_advertisement_update = time.monotonic()
    except BLEAK_EXCEPTIONS:
        _LOGGER.warning(
            "%s: Could not communicate with Tuya BLE device during setup (device may be asleep); will connect on demand or on next advertisement",
            address,
        )

    last_battery_advertisement_update = 0.0
    last_advertisement_data: tuple[bytes | None, bytes | None] | None = None

    async def _async_update_lock_from_advertisement() -> None:
        """Best-effort lock state refresh after a BLE advertisement."""
        try:
            await device.update()
        except BLEAK_EXCEPTIONS:
            _LOGGER.debug(
                "%s: Advertisement-triggered update failed",
                address,
                exc_info=True,
            )
        except Exception:
            _LOGGER.debug(
                "%s: Advertisement-triggered update failed unexpectedly",
                address,
                exc_info=True,
            )

    @callback
    def _async_update_ble(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Update from a ble callback."""
        nonlocal last_battery_advertisement_update, last_advertisement_data
        device.set_ble_device_and_advertisement_data(
            service_info.device, service_info.advertisement
        )
        if not device.keep_connected:
            now = time.monotonic()
            mfr_data = (
                service_info.advertisement.manufacturer_data.get(0x07D0)
                or service_info.advertisement.manufacturer_data.get(2000)
                or (next(iter(service_info.advertisement.manufacturer_data.values()), None) if service_info.advertisement.manufacturer_data else None)
            )
            srv_data = (
                service_info.advertisement.service_data.get(SERVICE_UUID)
                or service_info.advertisement.service_data.get("0000a201-0000-1000-8000-00805f9b34fb")
                or (next(iter(service_info.advertisement.service_data.values()), None) if service_info.advertisement.service_data else None)
            )
            adv_payload = (mfr_data, srv_data)

            should_update = False
            if last_advertisement_data is None:
                last_advertisement_data = adv_payload
                if last_battery_advertisement_update == 0.0 and (mfr_data or srv_data):
                    _LOGGER.debug(
                        "%s: Initial advertisement detected after setup, triggering initial sync",
                        address,
                    )
                    should_update = True
            else:
                adv_changed = bool((mfr_data or srv_data) and adv_payload != last_advertisement_data)
                if adv_changed:
                    _LOGGER.debug(
                        "%s: Lock advertisement event detected (mfr: %s, srv: %s), triggering on-demand sync",
                        address,
                        mfr_data.hex() if isinstance(mfr_data, (bytes, bytearray)) else mfr_data,
                        srv_data.hex() if isinstance(srv_data, (bytes, bytearray)) else srv_data,
                    )
                    last_advertisement_data = adv_payload
                    if now - last_battery_advertisement_update >= 3:
                        should_update = True
                elif now - last_battery_advertisement_update >= 300:
                    _LOGGER.debug(
                        "%s: Periodic lock advertisement sync (300s elapsed)",
                        address,
                    )
                    last_advertisement_data = adv_payload
                    should_update = True

            if should_update:
                last_battery_advertisement_update = now
                if not device.is_connected and not device.is_connecting:
                    hass.async_create_task(_async_update_lock_from_advertisement())

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _async_update_ble,
            BluetoothCallbackMatcher({ADDRESS: address}),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = TuyaBLEData(
        entry.title,
        device,
        product_info,
        manager,
        coordinator,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    async def _async_stop(event: Event) -> None:
        """Close the connection."""
        await device.stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options (e.g. keep_connected) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        data: TuyaBLEData = hass.data[DOMAIN].pop(entry.entry_id)
        await data.device.stop()

    return unload_ok
