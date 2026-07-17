"""Live Home Assistant entity coordinator for ParkPower sessions."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_ENTITY_FILTER, DEFAULT_ENTITY_FILTER
from .load_management import ACTION_PAUSE, ACTION_RESUME, LoadManagementEngine
from .models import SessionState, utc_now
from .outlet_registry import OutletMapping, discover_outlets, read_numeric_state
from .session_manager import SessionManager
from .storage import PowReportingStore
from .websocket import async_load_session_manager, async_save_session_manager

_LOGGER = logging.getLogger(__name__)


class PowReportingCoordinator:
    """Listen to live outlet meter state and advance charging sessions."""

    def __init__(self, hass: HomeAssistant, options: dict[str, Any]) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.options = options
        self.outlets: dict[str, OutletMapping] = {}
        self.sensor_to_outlet: dict[str, str] = {}
        self._unsub: list[Callable[[], None]] = []
        self._evaluating_load = False

    async def async_start(self) -> None:
        """Discover entities and subscribe to state changes."""
        self.async_stop()
        self.async_refresh_mappings()
        entity_ids = sorted({*self.sensor_to_outlet, *self.outlets})
        if not entity_ids:
            _LOGGER.debug("No ParkPower outlet entities discovered for live session tracking")
            return
        self._unsub.append(
            async_track_state_change_event(
                self.hass,
                entity_ids,
                self._state_changed,
            )
        )
        _LOGGER.debug("Tracking %s ParkPower entities for live session updates", len(entity_ids))

    def async_stop(self) -> None:
        """Stop all listeners."""
        while self._unsub:
            self._unsub.pop()()

    def async_refresh_mappings(self) -> None:
        """Refresh outlet/sensor mappings from Home Assistant registries."""
        entity_filter = self.options.get(CONF_ENTITY_FILTER, DEFAULT_ENTITY_FILTER)
        self.outlets = discover_outlets(self.hass, entity_filter=entity_filter)
        self.sensor_to_outlet = {}
        for outlet in self.outlets.values():
            if outlet.power_entity_id:
                self.sensor_to_outlet[outlet.power_entity_id] = outlet.switch_entity_id
            if outlet.energy_entity_id:
                self.sensor_to_outlet[outlet.energy_entity_id] = outlet.switch_entity_id

    @callback
    def _state_changed(self, event: Event) -> None:
        """Handle a Home Assistant state_changed event."""
        entity_id = event.data.get("entity_id")
        if entity_id in self.sensor_to_outlet:
            self.hass.async_create_task(self._async_update_from_sensor(entity_id))
            return
        if entity_id in self.outlets:
            self.hass.async_create_task(self._async_update_from_switch(entity_id))

    async def _async_update_from_sensor(self, entity_id: str) -> None:
        """Advance a session from a power or energy sensor update."""
        outlet_entity_id = self.sensor_to_outlet.get(entity_id)
        if outlet_entity_id is None:
            return
        outlet = self.outlets.get(outlet_entity_id)
        if outlet is None:
            return

        power_watts = read_numeric_state(self.hass, outlet.power_entity_id)
        meter_reading = read_numeric_state(self.hass, outlet.energy_entity_id)
        manager = await async_load_session_manager(self.hass)
        session = manager.update_measurement(
            outlet_entity_id=outlet_entity_id,
            power_watts=power_watts,
            meter_reading=meter_reading,
        )
        if session is not None:
            await async_save_session_manager(self.hass, manager)
        await self._async_evaluate_load_management()

    async def _async_update_from_switch(self, switch_entity_id: str) -> None:
        """Complete an active session when a tracked switch is turned off externally."""
        state = self.hass.states.get(switch_entity_id)
        if state is None or state.state != "off":
            await self._async_evaluate_load_management()
            return
        outlet = self.outlets.get(switch_entity_id)
        meter_reading = read_numeric_state(self.hass, outlet.energy_entity_id) if outlet else None
        manager = await async_load_session_manager(self.hass)
        active = manager.active_for_outlet(switch_entity_id)
        if active is not None and active.state == SessionState.PAUSED_LOAD_LIMIT:
            await self._async_evaluate_load_management()
            return
        session = manager.complete_session(
            outlet_entity_id=switch_entity_id,
            reason="Relay switched off outside ParkPower",
            end_meter_reading=meter_reading,
        )
        if session is not None:
            await async_save_session_manager(self.hass, manager)
        await self._async_evaluate_load_management()

    async def _async_evaluate_load_management(self) -> None:
        """Evaluate hierarchy limits and apply safe relay pause/resume actions."""
        if self._evaluating_load:
            return
        self._evaluating_load = True
        try:
            store = PowReportingStore(self.hass)
            data = await store.async_load()
            load_state = dict(data.get("load_management") or {})
            manager = SessionManager(data)
            active_sessions = {
                session.outlet_entity_id: session.as_dict()
                for session in manager.sessions
                if manager.active_for_outlet(session.outlet_entity_id) is not None
            }
            engine = LoadManagementEngine(
                hierarchy=data,
                outlet_power_w={
                    switch_id: read_numeric_state(self.hass, outlet.power_entity_id) or 0.0
                    for switch_id, outlet in self.outlets.items()
                },
                switch_states={
                    switch_id: self.hass.states.get(switch_id).state
                    for switch_id in self.outlets
                    if self.hass.states.get(switch_id) is not None
                },
                active_sessions=active_sessions,
                paused_outlets=dict(load_state.get("paused_outlets") or {}),
                relay_events=dict(load_state.get("relay_events") or {}),
            )
            result = engine.evaluate()
            load_state["last_evaluation"] = {
                "time": utc_now(),
                "groups": result["groups"],
                "actions": result["actions"],
            }
            data["load_management"] = load_state
            await store.async_save(data)

            for action in result["actions"]:
                await self._async_apply_load_action(action)
        finally:
            self._evaluating_load = False

    async def _async_apply_load_action(self, action: dict[str, Any]) -> None:
        """Apply one load-management action and persist session/load state."""
        switch_id = str(action.get("switch_entity_id") or "")
        if not switch_id:
            return
        store = PowReportingStore(self.hass)
        data = await store.async_load()
        manager = SessionManager(data)
        load_state = dict(data.get("load_management") or {})
        paused_outlets = dict(load_state.get("paused_outlets") or {})
        relay_events = dict(load_state.get("relay_events") or {})
        now = utc_now()

        if action.get("action") == ACTION_PAUSE:
            session = manager.active_for_outlet(switch_id)
            if session is None:
                return
            manager.transition(session, SessionState.PAUSED_LOAD_LIMIT, action.get("reason") or "load_limit_exceeded")
            await self.hass.services.async_call("switch", "turn_off", {"entity_id": switch_id}, blocking=True)
            paused_outlets[switch_id] = {
                "paused_at": now,
                "reason": action.get("reason") or "load_limit_exceeded",
                "circuit_group_id": action.get("circuit_group_id") or "",
                "circuit_group_name": action.get("circuit_group_name") or "",
            }
            _LOGGER.info("Paused %s for ParkPower load management", switch_id)
        elif action.get("action") == ACTION_RESUME:
            session = manager.active_for_outlet(switch_id)
            if session is None:
                paused_outlets.pop(switch_id, None)
                return
            await self.hass.services.async_call("switch", "turn_on", {"entity_id": switch_id}, blocking=True)
            manager.transition(session, SessionState.WAITING_FOR_LOAD, action.get("reason") or "capacity_available")
            paused_outlets.pop(switch_id, None)
            _LOGGER.info("Resumed %s after ParkPower load management pause", switch_id)
        else:
            return

        relay_events.setdefault(switch_id, []).append(now)
        relay_events[switch_id] = relay_events[switch_id][-24:]
        load_state["paused_outlets"] = paused_outlets
        load_state["relay_events"] = relay_events
        data["load_management"] = load_state
        await store.async_save(manager.dump())
