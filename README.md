# Adaptive Services ParkPower

A HACS-ready Home Assistant custom integration for commercial-style monitoring of
ESPHome/Sonoff POW power and energy devices in managed car park charging
outlet deployments.

The integration adds a sidebar panel with:

- automatic discovery of likely power and energy entities
- automatic discovery of likely outlet relay switches
- individual outlet power control
- a master **ALL Off** control
- customer, bay, booking, or car park spot references before energising an
  outlet
- centrally stored outlet on/off audit logs with timestamps
- persisted charging sessions and billing reports stored in Home Assistant
- local customer, vehicle, and user-group records
- local organisation, site, building, distribution board, circuit group, and
  outlet assignment records
- formal charging-session state tracking with load-limit pause/resume support
- configurable energy rate and currency for billing reports
- tenant accounts with multiple assigned outlets or power meters
- consolidated tenant statements and email delivery through Home Assistant
- live charge timers on managed outlets
- current load, total energy, and per-device/entity summaries
- daily, weekly, monthly, and custom-range reports
- management KPIs, report filters, chart-ready summaries, and monthly statement
  totals
- gateway/network health monitoring for Wi-Fi bridge and MQTT/NAT deployments
- charting from Home Assistant recorder statistics when available
- CSV export for energy statistics, outlet power events, and billing sessions
- local dashboard branding controls for name, logo URL, and accent color

## Install with HACS

1. Publish this repository to GitHub.
2. In Home Assistant, open HACS.
3. Add `https://github.com/clintonmarshall/ParkPower` as a custom
   repository with category `Integration`.
4. Install `Adaptive Services ParkPower`.
5. Restart Home Assistant.
6. Go to **Settings > Devices & services > Add integration** and add
   `Adaptive Services ParkPower`.

## Local/manual install

Copy `custom_components/pow_reporting` into your Home Assistant
`config/custom_components/` directory, restart Home Assistant, then add the
integration from the UI.

## Branding

ParkPower includes the SM logo as its default dashboard and customer-portal
brand mark. The image is packaged with the HACS integration, so no `/config/www`
copy is required.

The first version stores branding in the browser using local storage. Open the
dashboard, choose the settings tab, and set:

- dashboard name
- logo URL
- accent color
- entity filter

Entering another logo URL replaces the packaged logo. Clearing the field restores
the built-in SM logo.

For a commercial deployment, the next step should be moving those options into
the integration options flow so they are managed centrally in Home Assistant.

## Outlet Audit Log

Outlet control events are stored on the Home Assistant instance using Home
Assistant storage. Each record includes:

- local timestamp
- outlet name
- switch entity id
- action: `turn_on` or `turn_off`
- operator-entered reference
- success/failure state

The dashboard requires a reference before turning an outlet on. Turning an
outlet off reuses the active reference where available. The master **ALL Off**
action logs one off event per managed outlet.

### Home Assistant Entity Naming

The HACS dashboard Settings tab includes a **Home Assistant Entity Naming** tool.
It previews and then optionally applies display names based on HA Floor, Area,
and Bay/Spot labels.

For example, a device on `Level 1` with label `Bay 7` is named:

- `L1-B7 Control` for the outlet switch
- `L1-B7 Watts` for power
- `L1-B7 Amps` for current
- `L1-B7 Voltage` for voltage
- `L1-B7 Wh` for energy
- `L1-B7 Daily Wh` for daily energy, where present

The tool changes Home Assistant entity display names only. It does not rename
raw `entity_id` values, which keeps existing automations, history, and dashboards
safer.

## Self-contained HACS Portal

The operational portal is served by the HACS custom integration as a Home
Assistant sidebar panel. No separate Node/Express service is required for a
fresh Home Assistant installation.

By default it registers two Home Assistant routes on port `8123`:

- `/parkpower` for the admin dashboard, outlet controls, settings, entity
  naming tools, and reports
- `/parkpower-portal` for the customer-style charging portal view

For example:

```text
http://homeassistant.local:8123/parkpower
http://homeassistant.local:8123/parkpower-portal
```

Both routes are served by Home Assistant and use Home Assistant authentication.
That is intentional for the HACS package. A public customer portal should be a
separate tokenized/share-link route so outlet state and control permissions are
not exposed accidentally.

The panel stores charge sessions and billing settings in Home Assistant storage
under `.storage/pow_reporting.billing`. Outlet audit events are stored under
`.storage/pow_reporting.outlet_log`.

Phase 1/2 managed session and customer records are stored under
`.storage/pow_reporting.sessions`. This schema is additive so existing billing
and audit data remains valid.

Charging sessions are recorded when outlets are controlled through the HACS
panel:

- Power On records the reference, start time, current energy meter reading, and
  current rate.
- Power Off records end time, end meter reading, duration, kWh used, and cost.
- Master ALL Off completes active sessions for every outlet it successfully
  turns off.

Home Assistant Recorder remains the source for raw sensor history and charting.
The HACS integration owns the commercial/session context: reference, rate,
start/end readings, and billing totals.

### Managed Records

The dashboard **Records** tab stores lightweight local records for:

- Customers: name, contact details, apartment/unit/company, billing reference,
  assigned outlets/meters, per-kWh rate, currency, user group, status, and notes
- Vehicles: linked customer, registration, make/model description, and notes
- User groups: default tariff placeholder, priority, charging allowed, free
  charging, and discount percentage

The existing free-text outlet reference remains available. The outlet controls
also provide a tenant selector and automatically select the customer assigned to
that outlet. New billing and managed-session records keep that tenant ID.

### Tenant Billing And Email

The admin-only **Billing** tab creates one statement for a tenant across every
completed session explicitly linked to that tenant, including sessions from
multiple assigned meters. It provides a per-meter breakdown, total energy, total
cost, an email preview, and delivery history.

Statement pricing uses the tenant rate when configured, otherwise the global
energy rate. User-group discounts and free-charging policy are applied. A
successful email marks the statement and its included sessions as invoiced;
failed attempts stay as drafts, and overlapping drafts cannot invoice the same
session twice.

Email is sent only after an administrator clicks **Email Tenant**. Configure an
email-capable Home Assistant `notify` integration, then enter its service name in
ParkPower Settings as either `notify.smtp` or `smtp`. Statements are sent as
plain text to the email address on the tenant record.

### Site And Electrical Hierarchy

The dashboard **Hierarchy** tab stores Phase 5 commercial and electrical context:

- organisations, sites, buildings/levels, distribution boards, circuit groups,
  and outlet assignments
- electrical limits such as maximum current, maximum power, reserve margin,
  warning threshold, maximum simultaneous outlets, load-management mode, relay
  timing limits, relay operation limits, optional main-meter power entity, and
  enabled/disabled state
- outlet mappings that can start from Home Assistant-discovered relay, power,
  and energy entities, including HA area/floor/label metadata where available

These records are local to the integration under `.storage/pow_reporting.sessions`.
They are the policy source for live load management and detailed area/level
reporting.

### Load Management

Phase 6 uses the configured circuit group limits to protect electrical capacity:

- live power readings are summed for each configured circuit group
- effective limits subtract reserve margin from maximum power
- `monitor_only` groups report overloads without changing relays
- active modes can pause lower-priority charging sessions when power or
  simultaneous-outlet limits are exceeded
- paused sessions remain active in ParkPower as `paused_load_limit` rather than
  being completed for billing
- outlets are resumed when capacity returns and relay guardrails allow it
- minimum relay on/off duration and maximum relay operations per hour are
  enforced before any automatic action

The **Reports** tab shows the latest load-management evaluation for each circuit
group, including live watts, effective limit, active/paused outlet counts, and
the last recommended actions.

### Session State Tracking

Alongside the existing billing session list, ParkPower now keeps a richer
managed-session ledger with states such as `waiting_for_load`, `charging`,
`idle_grace_period`, `paused_load_limit`, `completed`, `cancelled`, and
`requires_review`.

Configurable thresholds control delayed charging start/stop detection:

- `charging_start_watts`
- `charging_start_delay_seconds`
- `charging_stop_watts`
- `charging_stop_delay_minutes`
- `maximum_session_hours`
- `meter_stale_minutes`
- `offline_timeout_minutes`

The integration listens to matched Home Assistant power and energy sensors so
live readings can advance sessions from waiting, to charging, to completed.

### Management Reports

The dashboard **Reports** tab includes Phase 4 management reporting:

- total managed, available, charging, waiting, paused, offline, and faulted
  outlets
- sessions today, energy today, monthly energy, estimated recovery, live demand,
  average kWh, average duration, and utilisation
- filters for period, outlet, billing status, and reference text
- chart-ready summaries for energy by day, sessions by day, top outlets, costs,
  and live outlet load
- CSV export for filtered management sessions
- monthly-style statement totals for measured energy, electricity cost,
  recoverable amount, invoiced, paid, waived, and outstanding values

Tariff-specific reporting columns are intentionally prepared but not fully
calculated until tariff profiles are added in Phase 3.

### Outlet Mapping

The HACS portal reads Home Assistant registries for outlet metadata:

- Home Assistant **Area** becomes the portal area, such as `L1 Parking`
- The Area's Home Assistant **Floor** becomes the portal level, such as `Level 1`
- Home Assistant **Labels** named like `Bay 7`, `Spot 014`, or `Space A12`
  become the portal bay/spot

Assign the ESPHome/Sonoff device to an Area in Home Assistant, put that Area on
a Floor, and add a Bay/Spot label to the device or switch entity. The HACS panel
will pick those changes up on the next refresh.

### Large Car Park Wi-Fi Bridge Deployment

Larger car parks can place Sonoff POW Elite outlets behind ESP32 Wi-Fi NAT bridge
nodes when the main Wi-Fi network does not reach every bay cleanly. In that
topology, ParkPower still discovers normal ESPHome/Home Assistant entities, and
outlets can also be manually mapped with gateway metadata in the **Hierarchy**
tab.

Recommended topology:

- Home Assistant and MQTT broker on the main building network.
- One ESP32 bridge per local parking zone, mounted where it has a solid uplink
  to the main Wi-Fi and clear coverage to nearby Sonoff outlets.
- Sonoff POW Elite devices join the bridge SSID and publish relay, power,
  energy, RSSI, and availability through MQTT.
- Gateway health entities are exposed in Home Assistant as:
  `sensor.<gateway>_uptime`, `sensor.<gateway>_client_count`,
  `sensor.<gateway>_uplink_rssi`, `sensor.<gateway>_free_heap`, and
  `binary_sensor.<gateway>_online`.

ESP32 NAT bridge notes:

- Treat each bridge as a small zone gateway, not as invisible infrastructure.
  Give it a stable ID, name, zone, IP, SSID, and subnet, then map those fields on
  each associated outlet.
- Watch `client_count`, `uplink_rssi`, and `free_heap`. Too many clients or weak
  uplink RSSI will show in ParkPower diagnostics before users report unreliable
  charging.
- Avoid chaining repeaters. Every repeated hop increases latency, airtime use,
  packet loss, and recovery time after a power or Wi-Fi event.

MQTT is recommended for Sonoffs behind NAT bridges. ESPHome native API uses
direct TCP connections and discovery assumptions that may not work cleanly across
NAT, especially when Home Assistant cannot route back to the private bridge
subnet or when mDNS/device adoption traffic is blocked. MQTT gives each outlet a
broker-facing path for telemetry, availability, and commands even when the
device sits behind a translated subnet.

Suggested Sonoff ESPHome MQTT shape:

```yaml
mqtt:
  broker: 192.168.1.10
  username: !secret mqtt_user
  password: !secret mqtt_password
  topic_prefix: parkpower/l1/bay01
  birth_message:
    topic: parkpower/l1/bay01/status
    payload: online
  will_message:
    topic: parkpower/l1/bay01/status
    payload: offline

wifi:
  ssid: ParkPower-L1-East
  password: !secret bridge_wifi_password

sensor:
  - platform: wifi_signal
    name: L1 Bay 01 RSSI
    update_interval: 60s

binary_sensor:
  - platform: status
    name: L1 Bay 01 Online
```

In ParkPower, set the outlet `source_type` to `mqtt`, add the
`mqtt_topic_prefix`, set `stale_after_minutes`, and map any availability/RSSI
entities. If the outlet is offline, the gateway is offline, or power/energy
telemetry is stale, ParkPower marks the outlet as offline, stale, or
`gateway_unreachable`. It will still allow recovery actions such as Power Off,
but it will not start a new billing session or calculate live cost from stale
sensor readings.

## ESP Display Panel

The `display/parking_power_panel/` folder contains first-pass Arduino/LVGL
firmware for the JC1060P470C_I_W_Y 7 inch ESP32-P4 display.

It provides:

- live total load / outlets on / energy today summary
- parking spot lookup with on-screen keypad
- outlet detail screen
- power on/off controls through the Home Assistant API

The display firmware is still a companion client. The core portal, reporting,
and billing logic lives inside the HACS integration.

## Notes

The dashboard reads Home Assistant entity/device registries and recorder
statistics through the frontend WebSocket connection. The recorder statistics
calls are used defensively because Home Assistant does not publish them as a
stable public REST API.
