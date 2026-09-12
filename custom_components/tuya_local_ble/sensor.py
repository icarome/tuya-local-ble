"""The Tuya BLE integration."""
from __future__ import annotations

from dataclasses import dataclass, field

from datetime import timedelta
import logging
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    #TEMP_CELSIUS,
    #UnitOfTemperature.CELSIUS,
    #VOLUME_MILLILITERS,
    #UnitOfVolume.MILLILITERS,
    UnitOfVolume,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfElectricPotential,
    UnitOfRatio,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    BATTERY_STATE_HIGH,
    BATTERY_STATE_LOW,
    BATTERY_STATE_NORMAL,
    BATTERY_CHARGED,
    BATTERY_CHARGING,
    BATTERY_NOT_CHARGING,
    CO2_LEVEL_ALARM,
    CO2_LEVEL_NORMAL,
    DOMAIN,
    SIGNAL_DYNAMIC_PASSWORD_UPDATE,
)
from .devices import TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo, get_device_info
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice

_LOGGER = logging.getLogger(__name__)

SIGNAL_STRENGTH_DP_ID = -1


TuyaBLESensorIsAvailable = Callable[["TuyaBLESensor", TuyaBLEProductInfo], bool] | None


@dataclass
class TuyaBLESensorMapping:
    dp_id: int
    description: SensorEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None
    getter: Callable[[TuyaBLESensor], None] | None = None
    coefficient: float = 1.0
    icons: list[str] | None = None
    is_available: TuyaBLESensorIsAvailable = None


@dataclass
class TuyaBLEBatteryMapping(TuyaBLESensorMapping):
    description: SensorEntityDescription = field(
        default_factory=lambda: SensorEntityDescription(
            key="battery",
            device_class=SensorDeviceClass.BATTERY,
            native_unit_of_measurement=PERCENTAGE,
            entity_category=EntityCategory.DIAGNOSTIC,
            state_class=SensorStateClass.MEASUREMENT,
        )
    )


@dataclass
class TuyaBLETemperatureMapping(TuyaBLESensorMapping):
    description: SensorEntityDescription = field(
        default_factory=lambda: SensorEntityDescription(
            key="temperature",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
        )
    )


def is_co2_alarm_enabled(self: TuyaBLESensor, product: TuyaBLEProductInfo) -> bool:
    result: bool = True
    datapoint = self._device.datapoints[13]
    if datapoint:
        result = bool(datapoint.value)
    return result


def battery_enum_getter(self: TuyaBLESensor) -> None:
    datapoint = self._device.datapoints[104]
    if datapoint:
        self._attr_native_value = datapoint.value * 20.0


def lock_last_unlocked_user_getter(self: TuyaBLESensor) -> None:
    """Get the latest unlocked user ID across fingerprint (12), keypad (13), and NFC (15)."""
    latest_dp = None
    method = None
    for dp_id, m in ((12, "fingerprint"), (13, "keypad"), (15, "nfc")):
        dp = self._device.datapoints[dp_id]
        if dp and dp.value is not None:
            if latest_dp is None or dp.timestamp > latest_dp.timestamp:
                latest_dp = dp
                method = m
    if latest_dp is not None:
        self._attr_native_value = latest_dp.value
        self._attr_extra_state_attributes = {
            "unlock_method": method,
            "dp_id": latest_dp.id,
        }


@dataclass
class TuyaBLECategorySensorMapping:
    products: dict[str, list[TuyaBLESensorMapping]] | None = None
    mapping: list[TuyaBLESensorMapping] | None = None


mapping: dict[str, TuyaBLECategorySensorMapping] = {
    "co2bj": TuyaBLECategorySensorMapping(
        products={
            "59s19z5m": [  # CO2 Detector
                TuyaBLESensorMapping(
                    dp_id=1,
                    description=SensorEntityDescription(
                        key="carbon_dioxide_alarm",
                        icon="mdi:molecule-co2",
                        device_class=SensorDeviceClass.ENUM,
                        options=[
                            CO2_LEVEL_ALARM,
                            CO2_LEVEL_NORMAL,
                        ],
                    ),
                    is_available=is_co2_alarm_enabled,
                ),
                TuyaBLESensorMapping(
                    dp_id=2,
                    description=SensorEntityDescription(
                        key="carbon_dioxide",
                        device_class=SensorDeviceClass.CO2,
                        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLEBatteryMapping(dp_id=15),
                TuyaBLETemperatureMapping(dp_id=18),
                TuyaBLESensorMapping(
                    dp_id=19,
                    description=SensorEntityDescription(
                        key="humidity",
                        device_class=SensorDeviceClass.HUMIDITY,
                        native_unit_of_measurement=PERCENTAGE,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
            ]
        }
    ),
    "ldcg": TuyaBLECategorySensorMapping(
        products={
            "poaanotz": [  # Briidea RV CO And Propane Gas Alarm
                TuyaBLESensorMapping(
                    dp_id=2,
                    description=SensorEntityDescription(
                        key="propane",
                        icon="mdi:gas-cylinder",
                        native_unit_of_measurement="%LEL",
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=8,
                    description=SensorEntityDescription(
                        key="carbon_monoxide",
                        device_class=SensorDeviceClass.CO,
                        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=4,
                    coefficient=10.0,
                    description=SensorEntityDescription(
                        key="supply_voltage",
                        device_class=SensorDeviceClass.VOLTAGE,
                        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
                        state_class=SensorStateClass.MEASUREMENT,
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                ),
            ]
        }
    ),
    "ms": TuyaBLECategorySensorMapping(
        products={
            **dict.fromkeys(
                ["ludzroix", "isk2p555"], # Smart Lock
                [
                    TuyaBLESensorMapping(
                        dp_id=21,
                        description=SensorEntityDescription(
                            key="alarm_lock",
                            device_class=SensorDeviceClass.ENUM,
                            options=[
                                "wrong_finger",
                                "wrong_password",
                                "low_battery",
                            ],
                        ),
                    ),
                    TuyaBLEBatteryMapping(dp_id=8),
                ],
            ),
        }
    ),
    "jtmspro": TuyaBLECategorySensorMapping(
        products={
            "rlyxv7pe":  # Smart Lock
            [
                TuyaBLESensorMapping(
                    dp_id=9,
                    description=SensorEntityDescription(
                        key="battery_state",
                        icon="mdi:battery",
                        device_class=SensorDeviceClass.ENUM,
                        options=[
                            BATTERY_STATE_HIGH,
                            BATTERY_STATE_NORMAL,
                            BATTERY_STATE_LOW,
                            BATTERY_STATE_LOW,
                        ],
                    ),
                    icons=[
                        "mdi:battery-check",
                        "mdi:battery-50",
                        "mdi:battery-alert",
                        "mdi:battery-alert",
                    ],
                ),
            ],
            "y2yaegze":  # CTL20H SmartLock, TuyaOS FD50
            [
                TuyaBLEBatteryMapping(
                    # DP8 is a 4-byte Tuya VALUE containing battery percentage.
                    dp_id=8,
                ),
            ],
            "rppmvevx":  # Smart Lock (rppmvevx)
            [
                TuyaBLEBatteryMapping(
                    dp_id=8,
                ),
                TuyaBLESensorMapping(
                    dp_id=12,
                    description=SensorEntityDescription(
                        key="last_unlocked_user_id",
                        name="Last Unlocked User ID",
                        icon="mdi:account-key",
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                    getter=lock_last_unlocked_user_getter,
                ),
                TuyaBLESensorMapping(
                    dp_id=12,
                    description=SensorEntityDescription(
                        key="unlock_fingerprint",
                        name="Fingerprint Unlock User ID",
                        icon="mdi:fingerprint",
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=13,
                    description=SensorEntityDescription(
                        key="unlock_keypad",
                        name="Keypad Unlock User ID",
                        icon="mdi:dialpad",
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=15,
                    description=SensorEntityDescription(
                        key="unlock_nfc",
                        name="NFC Unlock User ID",
                        icon="mdi:nfc",
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=19,
                    description=SensorEntityDescription(
                        key="alarm_lock",
                        icon="mdi:shield-alert",
                        device_class=SensorDeviceClass.ENUM,
                        options=[
                            "normal",
                            "wrong_password",
                            "tamper_alarm",
                            "low_battery",
                            "door_unclosed",
                        ],
                    ),
                    icons=[
                        "mdi:shield-check",
                        "mdi:shield-alert",
                        "mdi:shield-alert",
                        "mdi:battery-alert",
                        "mdi:door-open",
                    ],
                ),
                TuyaBLESensorMapping(
                    dp_id=36,
                    description=SensorEntityDescription(
                        key="auto_lock_delay",
                        name="Auto Lock Delay",
                        icon="mdi:timer-outline",
                        native_unit_of_measurement=UnitOfTime.SECONDS,
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                ),
            ],
            "hc7n0urm":  # Raykube A1 Ultra / A1 Pro Max TuyaOS FD50 lock
            [
                TuyaBLESensorMapping(
                    dp_id=9,
                    description=SensorEntityDescription(
                        key="battery_state",
                        icon="mdi:battery",
                        device_class=SensorDeviceClass.ENUM,
                        options=[
                            BATTERY_STATE_HIGH,
                            BATTERY_STATE_NORMAL,
                            BATTERY_STATE_LOW,
                            BATTERY_STATE_LOW,
                        ],
                    ),
                    icons=[
                        "mdi:battery-check",
                        "mdi:battery-50",
                        "mdi:battery-alert",
                        "mdi:battery-alert",
                    ],
                ),
            ],
            "ikphogdj":  # HL Knob-2, TuyaOS FD50 lock
            [
                TuyaBLEBatteryMapping(
                    # dp 8 (residual_electricity), confirmed via HCI capture:
                    # spontaneous report on connect, value matched the app's
                    # displayed battery % exactly.
                    dp_id=8,
                ),
                TuyaBLESensorMapping(
                    # dp 9 (battery_state enum) - present in cloud schema but
                    # never observed being reported over BLE; kept in case it
                    # shows up. dp 8 above is the confirmed-working one.
                    dp_id=9,
                    description=SensorEntityDescription(
                        key="battery_state",
                        icon="mdi:battery",
                        device_class=SensorDeviceClass.ENUM,
                        options=[
                            BATTERY_STATE_HIGH,
                            BATTERY_STATE_NORMAL,
                            BATTERY_STATE_LOW,
                            BATTERY_STATE_LOW,
                        ],
                    ),
                    icons=[
                        "mdi:battery-check",
                        "mdi:battery-50",
                        "mdi:battery-alert",
                        "mdi:battery-alert",
                    ],
                ),
                TuyaBLESensorMapping(
                    # dp 12 (unlock_fingerprint). Confirmed via two HCI
                    # captures with different fingers. This is the
                    # fingerprint slot index used to unlockNo name mapping is 
                    # availablelocally (that only exists in the app/cloud)
                    dp_id=12,
                    description=SensorEntityDescription(
                        key="last_fingerprint_unlock_slot",
                        icon="mdi:fingerprint",
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                ),
            ],
        }
    ),      
    "szjqr": TuyaBLECategorySensorMapping(
        products={
            **dict.fromkeys(
                ["3yqdo5yt", "xhf790if"],  # CubeTouch 1s and II
                [
                    TuyaBLESensorMapping(
                        dp_id=7,
                        description=SensorEntityDescription(
                            key="battery_charging",
                            device_class=SensorDeviceClass.ENUM,
                            entity_category=EntityCategory.DIAGNOSTIC,
                            options=[
                                BATTERY_NOT_CHARGING,
                                BATTERY_CHARGING,
                                BATTERY_CHARGED,
                            ],
                        ),
                        icons=[
                            "mdi:battery",
                            "mdi:power-plug-battery",
                            "mdi:battery-check",
                        ],
                    ),
                    TuyaBLEBatteryMapping(dp_id=8),
                ],
            ),
            **dict.fromkeys(
                [
                    "blliqpsj",
                    "ndvkgsrm",
                    "yiihr7zh", 
                    "neq16kgd"
                ],  # Fingerbot Plus
                [
                    TuyaBLEBatteryMapping(dp_id=12),
                ],
            ),
            **dict.fromkeys(
                [
                    "ltak7e1p",
                    "y6kttvd6",
                    "yrnk7mnn",
                    "nvr2rocq",
                    "bnt7wajf",
                    "rvdceqjh",
                    "5xhbk964",
                ],  # Fingerbot
                [
                    TuyaBLEBatteryMapping(dp_id=12),
                ],
            ),
        },
    ),
    "wsdcg": TuyaBLECategorySensorMapping(
        products={
            "ojzlzzsw": [  # Soil moisture sensor
                TuyaBLETemperatureMapping(
                    dp_id=1,
                    coefficient=10.0,
                ),
                TuyaBLESensorMapping(
                    dp_id=2,
                    description=SensorEntityDescription(
                        key="moisture",
                        device_class=SensorDeviceClass.MOISTURE,
                        native_unit_of_measurement=PERCENTAGE,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=3,
                    description=SensorEntityDescription(
                        key="battery_state",
                        icon="mdi:battery",
                        device_class=SensorDeviceClass.ENUM,
                        entity_category=EntityCategory.DIAGNOSTIC,
                        options=[
                            BATTERY_STATE_LOW,
                            BATTERY_STATE_NORMAL,
                            BATTERY_STATE_HIGH,
                        ],
                    ),
                    icons=[
                        "mdi:battery-alert",
                        "mdi:battery-50",
                        "mdi:battery-check",
                    ],
                ),
                TuyaBLEBatteryMapping(dp_id=4),
            ],
            "jm6iasmb": [  # Temperature Humidity Sensor
                TuyaBLETemperatureMapping(
                    dp_id=1,
                    coefficient=10.0,
                ),
                TuyaBLESensorMapping(
                    dp_id=2,
                    description=SensorEntityDescription(
                        key="humidity",
                        device_class=SensorDeviceClass.HUMIDITY,
                        native_unit_of_measurement=PERCENTAGE,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=3,
                    description=SensorEntityDescription(
                        key="battery_state",
                        icon="mdi:battery",
                        device_class=SensorDeviceClass.ENUM,
                        entity_category=EntityCategory.DIAGNOSTIC,
                        options=[
                            BATTERY_STATE_LOW,
                            BATTERY_STATE_NORMAL,
                            BATTERY_STATE_HIGH,
                        ],
                    ),
                    icons=[
                        "mdi:battery-alert",
                        "mdi:battery-50",
                        "mdi:battery-check",
                    ],
                ),
                TuyaBLEBatteryMapping(dp_id=4),
            ],
        },
    ),
    "znhsb": TuyaBLECategorySensorMapping(
        products={
            "cdlandip":  # Smart water bottle
            [
                TuyaBLETemperatureMapping(
                    dp_id=101,
                ),
                TuyaBLESensorMapping(
                    dp_id=102,
                    description=SensorEntityDescription(
                        key="water_intake",
                        device_class=SensorDeviceClass.WATER,
                        native_unit_of_measurement=UnitOfVolume.MILLILITERS,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=104,
                    description=SensorEntityDescription(
                        key="battery",
                        device_class=SensorDeviceClass.BATTERY,
                        native_unit_of_measurement=PERCENTAGE,
                        entity_category=EntityCategory.DIAGNOSTIC,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                    getter=battery_enum_getter,
                ),
            ],
        },
    ),
    "ggq": TuyaBLECategorySensorMapping(
        products={
            "6pahkcau": [  # Irrigation computer
                TuyaBLEBatteryMapping(dp_id=11),
                TuyaBLESensorMapping(
                    dp_id=6,
                    description=SensorEntityDescription(
                        key="time_left",
                        device_class=SensorDeviceClass.DURATION,
                        native_unit_of_measurement=UnitOfTime.MINUTES,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
            ],
        },
    ),
    "tdq": TuyaBLECategorySensorMapping(
        mapping=[
            TuyaBLESensorMapping(
                dp_id=17,
                description=SensorEntityDescription(
                    key="total_energy",
                    device_class=SensorDeviceClass.ENERGY,
                    native_unit_of_measurement="Wh",
                    state_class=SensorStateClass.TOTAL_INCREASING,
                ),
                force_add=False,
            ),
            TuyaBLESensorMapping(
                dp_id=18,
                description=SensorEntityDescription(
                    key="current",
                    device_class=SensorDeviceClass.CURRENT,
                    native_unit_of_measurement="mA",
                    state_class=SensorStateClass.MEASUREMENT,
                ),
                force_add=False,
            ),
            TuyaBLESensorMapping(
                dp_id=19,
                description=SensorEntityDescription(
                    key="power",
                    device_class=SensorDeviceClass.POWER,
                    native_unit_of_measurement="W",
                    state_class=SensorStateClass.MEASUREMENT,
                ),
                coefficient=10.0,
                force_add=False,
            ),
            TuyaBLESensorMapping(
                dp_id=20,
                description=SensorEntityDescription(
                    key="voltage",
                    device_class=SensorDeviceClass.VOLTAGE,
                    native_unit_of_measurement="V",
                    state_class=SensorStateClass.MEASUREMENT,
                ),
                coefficient=10.0,
                force_add=False,
            ),
        ],
        products={
            "dfs6sn0hbmfqqu3v": [
                TuyaBLESensorMapping(
                    dp_id=17,
                    description=SensorEntityDescription(
                        key="total_energy",
                        device_class=SensorDeviceClass.ENERGY,
                        native_unit_of_measurement="Wh",
                        state_class=SensorStateClass.TOTAL_INCREASING,
                    ),
                    force_add=False,
                ),
                TuyaBLESensorMapping(
                    dp_id=18,
                    description=SensorEntityDescription(
                        key="current",
                        device_class=SensorDeviceClass.CURRENT,
                        native_unit_of_measurement="mA",
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                    force_add=False,
                ),
                TuyaBLESensorMapping(
                    dp_id=19,
                    description=SensorEntityDescription(
                        key="power",
                        device_class=SensorDeviceClass.POWER,
                        native_unit_of_measurement="W",
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                    coefficient=10.0,
                    force_add=False,
                ),
                TuyaBLESensorMapping(
                    dp_id=20,
                    description=SensorEntityDescription(
                        key="voltage",
                        device_class=SensorDeviceClass.VOLTAGE,
                        native_unit_of_measurement="V",
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                    coefficient=10.0,
                    force_add=False,
                ),
            ],
        },
    ),
}


def rssi_getter(sensor: TuyaBLESensor) -> None:
    sensor._attr_native_value = sensor._device.rssi


rssi_mapping = TuyaBLESensorMapping(
    dp_id=SIGNAL_STRENGTH_DP_ID,
    description=SensorEntityDescription(
        key="signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    getter=rssi_getter,
)


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLESensorMapping]:
    category = mapping.get(device.category)
    if category is not None:
        if category.products is not None:
            product_mapping = category.products.get(device.product_id)
            if product_mapping is not None:
                return product_mapping
        if category.mapping is not None:
            return category.mapping
    return []


class TuyaBLESensor(TuyaBLEEntity, SensorEntity):
    """Representation of a Tuya BLE sensor."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DataUpdateCoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        mapping: TuyaBLESensorMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, mapping.description)
        self._mapping = mapping
        self._update_value_from_datapoint()

    def _update_value_from_datapoint(self) -> None:
        """Update native value and icon from current datapoint."""
        if self._mapping.getter is not None:
            self._mapping.getter(self)
            return

        datapoint = self._device.datapoints[self._mapping.dp_id]
        if not datapoint:
            return

        if self.entity_description.options is not None:
            options = self.entity_description.options
            value = datapoint.value
            option: str | None = None
            try:
                int_value = int(value)
            except (TypeError, ValueError):
                int_value = None

            if int_value is not None and 0 <= int_value < len(options):
                option = options[int_value]
            elif isinstance(value, str) and value in options:
                option = value
            else:
                _LOGGER.warning(
                    "Sensor %s received value %r which is not in options %s",
                    self.entity_id,
                    value,
                    options,
                )

            self._attr_native_value = option

            if self._mapping.icons is not None and int_value is not None:
                if 0 <= int_value < len(self._mapping.icons):
                    self._attr_icon = self._mapping.icons[int_value]
        elif datapoint.type == TuyaBLEDataPointType.DT_ENUM:
            if self._mapping.icons is not None:
                try:
                    int_value = int(datapoint.value)
                    if 0 <= int_value < len(self._mapping.icons):
                        self._attr_icon = self._mapping.icons[int_value]
                except (TypeError, ValueError):
                    pass
            self._attr_native_value = datapoint.value
        elif datapoint.type == TuyaBLEDataPointType.DT_VALUE:
            if self._mapping.coefficient == 1.0:
                self._attr_native_value = datapoint.value
            else:
                self._attr_native_value = (
                    datapoint.value / self._mapping.coefficient
                )
        else:
            self._attr_native_value = datapoint.value

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_value_from_datapoint()
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        result = super().available
        if result and self._mapping.is_available:
            result = self._mapping.is_available(self, self._product)
        return result


class TuyaBLEDynamicPasswordSensor(SensorEntity, RestoreEntity):
    """Representation of Tuya BLE lock dynamic password sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = "dynamic_password"
    _attr_icon = "mdi:shield-key-outline"

    def __init__(
        self,
        hass: HomeAssistant,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
    ) -> None:
        self.hass = hass
        self._device = device
        self._product = product
        self._attr_unique_id = f"{device.device_id}-dynamic_password"
        self._attr_device_info = get_device_info(device)
        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            "device_id": device.device_id,
            "valid_for_minutes": 5,
        }
        self._unsub_expiry: CALLBACK_TYPE | None = None

    @property
    def available(self) -> bool:
        """Dynamic password sensor is a cloud entity and always available."""
        return True

    async def async_added_to_hass(self) -> None:
        """Register dispatcher listener and restore state."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_DYNAMIC_PASSWORD_UPDATE}_{self._device.device_id}",
                self._handle_otp_update,
            )
        )

        if (last_state := await self.async_get_last_state()) is not None:
            expires_at_str = last_state.attributes.get("expires_at")
            if expires_at_str:
                try:
                    expires_at = dt_util.parse_datetime(expires_at_str)
                    if expires_at:
                        now = dt_util.now()
                        if now < expires_at:
                            self._attr_native_value = last_state.state
                            self._attr_extra_state_attributes = dict(last_state.attributes)
                            remaining = (expires_at - now).total_seconds()
                            self._unsub_expiry = async_call_later(
                                self.hass, remaining, self._handle_expired
                            )
                        else:
                            self._attr_native_value = "expired"
                            self._attr_extra_state_attributes = dict(last_state.attributes)
                            self._attr_extra_state_attributes["is_expired"] = True
                except Exception:
                    pass

    @callback
    def _handle_otp_update(self, data: dict[str, Any]) -> None:
        if self._unsub_expiry:
            self._unsub_expiry()
            self._unsub_expiry = None

        self._attr_native_value = data.get("dynamic_password")
        self._attr_extra_state_attributes = {
            "expires_at": data.get("expires_at"),
            "expires_time": data.get("expires_time"),
            "keypad_entry": data.get("keypad_entry"),
            "valid_for_minutes": data.get("valid_for_minutes", 5),
            "device_id": self._device.device_id,
            "is_expired": False,
        }
        self.async_write_ha_state()

        self._unsub_expiry = async_call_later(
            self.hass,
            timedelta(minutes=5),
            self._handle_expired,
        )

    @callback
    def _handle_expired(self, _: Any = None) -> None:
        self._unsub_expiry = None
        self._attr_native_value = "expired"
        if self._attr_extra_state_attributes:
            self._attr_extra_state_attributes = {
                **self._attr_extra_state_attributes,
                "is_expired": True,
            }
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tuya BLE sensors."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[SensorEntity] = [
        TuyaBLESensor(
            hass,
            data.coordinator,
            data.device,
            data.product,
            rssi_mapping,
        )
    ]
    for mapping in mappings:
        if mapping.force_add or data.device.datapoints.has_id(
            mapping.dp_id, mapping.dp_type
        ):
            entities.append(
                TuyaBLESensor(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    mapping,
                )
            )

    if data.device.category == "jtmspro" or data.device.product_id == "rppmvevx":
        entities.append(
            TuyaBLEDynamicPasswordSensor(
                hass,
                data.device,
                data.product,
            )
        )

    async_add_entities(entities)

