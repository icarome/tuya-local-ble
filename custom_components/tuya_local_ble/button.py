"""The Tuya BLE integration."""
from __future__ import annotations

from dataclasses import dataclass, field

import logging
from typing import Callable

from homeassistant.components.button import (
    ButtonEntityDescription,
    ButtonEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .devices import TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo, get_device_info
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice
from .tuya_cloud import async_generate_and_dispatch_dynamic_password

_LOGGER = logging.getLogger(__name__)


TuyaBLEButtonIsAvailable = Callable[["TuyaBLEButton", TuyaBLEProductInfo], bool] | None


@dataclass
class TuyaBLEButtonMapping:
    dp_id: int
    description: ButtonEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None
    is_available: TuyaBLEButtonIsAvailable = None


def is_fingerbot_in_push_mode(self: TuyaBLEButton, product: TuyaBLEProductInfo) -> bool:
    result: bool = True
    if product.fingerbot:
        datapoint = self._device.datapoints[product.fingerbot.mode]
        if datapoint:
            result = datapoint.value == 0
    return result

@dataclass
class TuyaBLEFingerbotModeMapping(TuyaBLEButtonMapping):
    description: ButtonEntityDescription = field(
        default_factory=lambda: ButtonEntityDescription(
            key="push",
            translation_key="push",
        )
    )
    is_available: TuyaBLEButtonIsAvailable = is_fingerbot_in_push_mode

@dataclass
class TuyaBLEButtonMapping(TuyaBLEButtonMapping):
    description: ButtonEntityDescription = field(
        default_factory=lambda: ButtonEntityDescription(
            key="push",
            translation_key="push",
        )
    )
    is_available: TuyaBLEButtonIsAvailable = 0

@dataclass
class TuyaBLECategoryButtonMapping:
    products: dict[str, list[TuyaBLEButtonMapping]] | None = None
    mapping: list[TuyaBLEButtonMapping] | None = None


mapping: dict[str, TuyaBLECategoryButtonMapping] = {
    "szjqr": TuyaBLECategoryButtonMapping(
        products={
            **dict.fromkeys(
                ["3yqdo5yt", "xhf790if"],  # CubeTouch 1s and II
                [
                    TuyaBLEFingerbotModeMapping(dp_id=1),
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
                    TuyaBLEFingerbotModeMapping(dp_id=2),
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
                    TuyaBLEFingerbotModeMapping(dp_id=2),
                ],
            ),
        },
    ),
    "znhsb": TuyaBLECategoryButtonMapping(
        products={
            "cdlandip":  # Smart water bottle
            [
                TuyaBLEButtonMapping(
                    dp_id=109,
                    description=ButtonEntityDescription(
                        key="bright_lid_screen",
                    ),
                ),
            ],
        },
    ),
}


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLECategoryButtonMapping]:
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
        else:
            return []
    else:
        return []


class TuyaBLEButton(TuyaBLEEntity, ButtonEntity):
    """Representation of a Tuya BLE Button."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DataUpdateCoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        mapping: TuyaBLEButtonMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, mapping.description)
        self._mapping = mapping

    def press(self) -> None:
        """Press the button."""
        datapoint = self._device.datapoints.get_or_create(
            self._mapping.dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            False,
        )
        if datapoint:
            self._hass.create_task(datapoint.set_value(not bool(datapoint.value)))

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        result = super().available
        if result and self._mapping.is_available:
            result = self._mapping.is_available(self, self._product)
        return result


class TuyaBLEDynamicPasswordButton(ButtonEntity):
    """Representation of Tuya BLE lock generate dynamic password button."""

    _attr_has_entity_name = True
    _attr_translation_key = "generate_dynamic_password"
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
        self._attr_unique_id = f"{device.device_id}-generate_dynamic_password"
        self._attr_device_info = get_device_info(device)

    @property
    def available(self) -> bool:
        """Dynamic password generation is a cloud API and is always available."""
        return True

    async def async_press(self) -> None:
        """Handle the button press to generate a dynamic password."""
        await async_generate_and_dispatch_dynamic_password(
            self.hass,
            device_id=self._device.device_id,
            device_name=self._device.name or "Smart Lock",
            notify=True,
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tuya BLE buttons."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[ButtonEntity] = []
    for mapping in mappings:
        if mapping.force_add or data.device.datapoints.has_id(
            mapping.dp_id, mapping.dp_type
        ):
            entities.append(
                TuyaBLEButton(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    mapping,
                )
            )

    if data.device.category == "jtmspro" or data.device.product_id == "rppmvevx":
        entities.append(
            TuyaBLEDynamicPasswordButton(
                hass,
                data.device,
                data.product,
            )
        )

    async_add_entities(entities)

