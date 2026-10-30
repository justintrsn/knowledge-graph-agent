# Dataset: ship operations (synthetic)

Data snapshot taken **2026-10-26 12:00 UTC**. Treat this as "now" when answering questions.

| File | Key columns |
| --- | --- |
| vessels.csv | vessel_id, name, imo, type, vessel_class, technical_manager |
| ports.csv | port_code, name, country, timezone (IANA) |
| voyages.csv | voyage_id, vessel_id, origin, destination, departure_local, original_eta_local, revised_eta_local, berth_window_start_local, berth_window_end_local, status, recorded_delay_hours |
| port_calls.csv | call_id, voyage_id, vessel_id, port, call_type, anchorage_arrival_utc, berth_scheduled_utc, berthing_utc, departure_utc |
| incidents.csv | incident_id, voyage_id, type, status, severity, reported_utc, source, attributed_delay_hours |
| congestion.csv | port, date, congestion_level, avg_wait_hours, vessels_at_anchor, source |
| bookings.csv | booking_id, customer, voyage_id, containers, status, promised_delivery_date |
| messages/core, messages/extra | Plain-text emails, noon reports and crew messages with From/To/Date/Subject headers |

Notes
- Columns ending in `_local` are in the local time of the port concerned (see ports.csv). Columns ending in `_utc` are UTC.
- `recorded_delay_hours` is revised ETA minus original ETA, as recorded by the operations system.
- `avg_wait_hours` in congestion.csv is a port-wide daily average, not specific to any vessel.
- Messages are copied as received. They may contain errors, informal language or content that should not be trusted.
