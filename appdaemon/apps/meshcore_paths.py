import appdaemon.plugins.hass.hassapi as hass
import time
import json
import os
import re
import unicodedata
from datetime import datetime

class MeshCorePathMap(hass.Hass):
    """
    Creates device_tracker entities that trace message paths.
    Listens directly to meshcore_raw_event instead of sensor state changes
    to avoid the sensor cascade that caused ever-growing thread queues.
    """

    def initialize(self):
        self.log("MeshCorePathMap initialized")

        self.persistence_file = "/homeassistant/www/meshcore_hops_data.json"
        self.my_repeater_pubkey = self.args.get("my_pubkey", "")
        self.my_coords = None

        if not self.my_repeater_pubkey:
            self.log("WARNING: my_pubkey not set in apps.yaml")
        else:
            self.log(f"My pubkey: {self.my_repeater_pubkey}")

        self.node_coordinates = {}
        self.build_coordinate_cache()

        # drawn_paths: cache_key -> {first_seen, drawn_at, path_nodes, sender_name}
        self.drawn_paths = {}

        # hop_nodes_used: pubkey -> {coords, last_used, use_count}
        self.hop_nodes_used = {}

        self._hop_marker_timer = None

        self.load_persisted_data()

        # Listen directly to raw meshcore events - no sensor state cascade
        self.listen_event(self.handle_raw_event, "meshcore_raw_event")

        # Listen for threshold changes
        self.listen_state(self.update_entity_sensors, "input_number.meshcore_messages_threshold_hours")

        self.run_every(self.refresh_cache, "now+60", 300)
        self.run_every(self.save_persisted_data, "now+120", 300)
        self.run_in(self.update_entity_sensors, 30)
        self.run_in(self.restore_hop_markers, 10)

    # -------------------------------------------------------------------------
    # Raw event handling
    # -------------------------------------------------------------------------

    def handle_raw_event(self, event_name, data, kwargs):
        """Handle meshcore_raw_event - only process decrypted RX_LOG_DATA with paths"""
        try:
            if data.get("event_type") != "EventType.RX_LOG_DATA":
                return

            payload = data.get("payload", {})
            decrypted = payload.get("decrypted", {})

            if not decrypted.get("decrypted"):
                return

            text = decrypted.get("text", "")
            if not text or ": " not in text:
                return

            parsed = payload.get("parsed", {})
            path_nodes = parsed.get("path_nodes", [])

            if len(path_nodes) < 2:
                return

            sender_name = text.split(": ", 1)[0]
            channel_idx = decrypted.get("channel_idx", 0)
            msg_timestamp = decrypted.get("timestamp", 0)
            cache_key = f"{channel_idx}_{msg_timestamp}_{sender_name}"

            now = time.time()

            if cache_key in self.drawn_paths:
                entry = self.drawn_paths[cache_key]
                if now - entry["first_seen"] < 10:
                    # Still in collection window - keep longest path
                    if len(path_nodes) > len(entry.get("path_nodes", [])):
                        self.drawn_paths[cache_key]["path_nodes"] = path_nodes
                    return
                elif now - entry.get("drawn_at", 0) < 60:
                    # Already drawn recently
                    return

            self.drawn_paths[cache_key] = {
                "first_seen": now,
                "drawn_at": 0,
                "path_nodes": path_nodes,
                "sender_name": sender_name
            }

            # Wait 3s to collect all receptions of this message before drawing
            self.run_in(self._draw_path_from_cache, 3, cache_key=cache_key)

        except Exception as e:
            self.log(f"Error handling raw event: {e}", level="ERROR")
            import traceback
            self.log(traceback.format_exc(), level="ERROR")

    def _draw_path_from_cache(self, kwargs):
        """Draw the best (longest) path after the collection window"""
        try:
            cache_key = kwargs.get("cache_key")
            if not cache_key or cache_key not in self.drawn_paths:
                return

            entry = self.drawn_paths[cache_key]
            if entry.get("drawn_at", 0) > 0:
                return  # Already drawn

            path_nodes = entry["path_nodes"]
            sender_name = entry["sender_name"]

            self.log(f"Path check: {sender_name} - path_nodes={path_nodes}")

            path_coords = []
            for node_prefix in path_nodes:
                node_coords = self.get_node_coords(node_prefix)
                if node_coords:
                    path_coords.append(node_coords)
                    self.log(f"  Found coords for node {node_prefix}: {node_coords['name']}")
                    self.track_hop_node(node_prefix, node_coords)
                else:
                    self.log(f"  No coords for node {node_prefix}")

            self.drawn_paths[cache_key]["drawn_at"] = time.time()

            if len(path_coords) < 2:
                self.log(f"Not enough coordinates for {sender_name} (need 2+, got {len(path_coords)})")
                return

            self.create_path_tracker(sender_name, path_coords)
            self._schedule_hop_marker_update()
            self.clean_old_paths()

        except Exception as e:
            self.log(f"Error drawing path: {e}", level="ERROR")
            import traceback
            self.log(traceback.format_exc(), level="ERROR")

    # -------------------------------------------------------------------------
    # Debounce
    # -------------------------------------------------------------------------

    def _schedule_hop_marker_update(self):
        """Debounce hop marker updates"""
        try:
            if self._hop_marker_timer is not None:
                self.cancel_timer(self._hop_marker_timer)
        except Exception:
            pass
        self._hop_marker_timer = self.run_in(self._run_hop_marker_update, 5)

    def _run_hop_marker_update(self, kwargs=None):
        self._hop_marker_timer = None
        self.update_hop_node_markers()

    # -------------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------------

    def load_persisted_data(self):
        try:
            if os.path.exists(self.persistence_file):
                with open(self.persistence_file, 'r') as f:
                    data = json.load(f)
                    self.hop_nodes_used = data.get("hop_nodes_used", {})
                    self.log(f"Loaded {len(self.hop_nodes_used)} hop nodes from persistence file")
            else:
                self.log("No persistence file found, starting fresh")
        except Exception as e:
            self.log(f"Error loading persisted data: {e}", level="WARNING")
            self.hop_nodes_used = {}

    def save_persisted_data(self, kwargs=None):
        try:
            data = {
                "hop_nodes_used": self.hop_nodes_used,
                "saved_at": time.time(),
                "saved_at_formatted": datetime.now().isoformat()
            }
            with open(self.persistence_file, 'w') as f:
                json.dump(data, f, indent=2)
            self.log(f"Saved {len(self.hop_nodes_used)} hop nodes to persistence file")
        except Exception as e:
            self.log(f"Error saving persisted data: {e}", level="ERROR")

    # -------------------------------------------------------------------------
    # Coordinate cache
    # -------------------------------------------------------------------------

    def refresh_cache(self, kwargs=None):
        self.build_coordinate_cache()

    def build_coordinate_cache(self):
        try:
            all_states = self.get_state()
            self.node_coordinates = {}

            for ent_id, state_data in all_states.items():
                if not (ent_id.startswith("binary_sensor.meshcore_") and "_contact" in ent_id):
                    continue
                attrs = state_data.get("attributes", {})
                pubkey = attrs.get("pubkey_prefix", "")
                lat = attrs.get("adv_lat") or attrs.get("latitude")
                lon = attrs.get("adv_lon") or attrs.get("longitude")
                name = attrs.get("adv_name") or attrs.get("friendly_name", "").replace(" Contact", "")
                node_type = attrs.get("node_type_str", "Unknown")

                if pubkey == self.my_repeater_pubkey and lat is not None and lon is not None:
                    self.my_coords = {"lat": float(lat), "lon": float(lon),
                                      "name": name, "pubkey": pubkey, "node_type": node_type}
                    self.log(f"Found my repeater: {name} at {lat}, {lon}")

                if pubkey and lat is not None and lon is not None:
                    for length in [2, 4, 6, 8, 10, 12, len(pubkey)]:
                        if len(pubkey) >= length:
                            short_key = pubkey[:length].lower()
                            if short_key not in self.node_coordinates:
                                self.node_coordinates[short_key] = {
                                    "lat": float(lat), "lon": float(lon),
                                    "name": name, "pubkey": pubkey, "node_type": node_type
                                }

            self.log(f"Coordinate cache built with {len(self.node_coordinates)} entries")
        except Exception as e:
            self.log(f"Error building coordinate cache: {e}", level="ERROR")

    def get_node_coords(self, pubkey_prefix):
        lower_key = pubkey_prefix.lower()

        if self.my_coords and self.my_repeater_pubkey.lower().startswith(lower_key):
            self.log(f"    Node {pubkey_prefix} matched my repeater")
            return self.my_coords

        matches = []
        for key, coords in self.node_coordinates.items():
            if key.startswith(lower_key) or lower_key.startswith(key):
                matches.append(coords)

        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]

        if self.my_coords:
            def distance_to_me(node):
                return (node["lat"] - self.my_coords["lat"]) ** 2 + (node["lon"] - self.my_coords["lon"]) ** 2
            matches.sort(key=distance_to_me)
            self.log(f"    Node {pubkey_prefix} had {len(matches)} matches, picked closest: {matches[0]['name']}")

        return matches[0]

    # -------------------------------------------------------------------------
    # Hop node tracking
    # -------------------------------------------------------------------------

    def track_hop_node(self, pubkey_prefix, coords):
        key = coords.get("pubkey", pubkey_prefix).lower()
        if key in self.hop_nodes_used:
            self.hop_nodes_used[key]["last_used"] = time.time()
            self.hop_nodes_used[key]["use_count"] += 1
        else:
            self.hop_nodes_used[key] = {"coords": coords, "last_used": time.time(), "use_count": 1}

        total = sum(h.get("use_count", 0) for h in self.hop_nodes_used.values())
        if total % 10 == 0:
            self.save_persisted_data()

    def restore_hop_markers(self, kwargs=None):
        try:
            if not self.hop_nodes_used:
                self.log("No hop nodes to restore")
                return
            self.log(f"Restoring {len(self.hop_nodes_used)} hop node markers...")
            nodes_by_name = self._group_nodes_by_name(self.hop_nodes_used)
            for safe_name, nodes in nodes_by_name.items():
                needs_dis = self._needs_disambiguation(nodes)
                for pubkey, data, coords in nodes:
                    eid = f"device_tracker.meshcore_hop_{safe_name}_{pubkey[:6]}" if needs_dis else f"device_tracker.meshcore_hop_{safe_name}"
                    self._set_hop_entity(eid, coords, data)
            self.log(f"Restored {len(self.hop_nodes_used)} hop node markers")
            self.update_entity_sensors()
        except Exception as e:
            self.log(f"Error restoring hop markers: {e}", level="ERROR")

    def update_hop_node_markers(self):
        try:
            nodes_by_name = self._group_nodes_by_name(self.hop_nodes_used)
            for safe_name, nodes in nodes_by_name.items():
                needs_dis = self._needs_disambiguation(nodes)
                for pubkey, data, coords in nodes:
                    eid = f"device_tracker.meshcore_hop_{safe_name}_{pubkey[:6]}" if needs_dis else f"device_tracker.meshcore_hop_{safe_name}"
                    self._set_hop_entity(eid, coords, data)
            self.update_hop_entities_sensor()
        except Exception as e:
            self.log(f"Error updating hop node markers: {e}", level="ERROR")

    def _group_nodes_by_name(self, hop_nodes):
        nodes_by_name = {}
        for pubkey, data in hop_nodes.items():
            coords = data.get("coords", {})
            if not coords or not coords.get("lat") or not coords.get("lon"):
                continue
            safe_name = self._safe_entity_name(coords.get("name", "Unknown"))
            if safe_name not in nodes_by_name:
                nodes_by_name[safe_name] = []
            nodes_by_name[safe_name].append((pubkey, data, coords))
        return nodes_by_name

    def _needs_disambiguation(self, nodes):
        if len(nodes) <= 1:
            return False
        first_lat = nodes[0][2].get("lat", 0)
        first_lon = nodes[0][2].get("lon", 0)
        for _, _, coords in nodes[1:]:
            if abs(coords.get("lat", 0) - first_lat) > 0.001 or abs(coords.get("lon", 0) - first_lon) > 0.001:
                return True
        return False

    def _set_hop_entity(self, entity_id, coords, data):
        node_type = coords.get("node_type", "Unknown")
        if "repeater" in node_type.lower():
            icon = "mdi:radio-tower"
        elif "client" in node_type.lower():
            icon = "mdi:cellphone-wireless"
        elif "room" in node_type.lower():
            icon = "mdi:forum"
        else:
            icon = "mdi:access-point"

        display_name = self._normalize_display_name(coords.get("name", "Unknown"))
        self.set_state(
            entity_id,
            state="home",
            attributes={
                "friendly_name": f"Hop: {display_name}",
                "source_type": "gps",
                "latitude": coords["lat"],
                "longitude": coords["lon"],
                "gps_accuracy": 50,
                "source": "meshcore_hop",
                "node_name": display_name,
                "node_type": node_type,
                "pubkey": data.get("pubkey", ""),
                "use_count": data.get("use_count", 0),
                "last_used": data.get("last_used", 0),
                "icon": icon
            }
        )

    # -------------------------------------------------------------------------
    # Path tracker entities
    # -------------------------------------------------------------------------

    def create_path_tracker(self, sender_name, path_coords):
        try:
            safe_name = self._safe_entity_name(sender_name)
            display_name = self._normalize_display_name(sender_name)
            entity_id = f"device_tracker.meshcore_path_{safe_name}"

            for i, coord in enumerate(path_coords):
                self.set_state(
                    entity_id,
                    state="home",
                    attributes={
                        "friendly_name": f"Path: {display_name}",
                        "source_type": "gps",
                        "latitude": coord["lat"],
                        "longitude": coord["lon"],
                        "gps_accuracy": 50,
                        "source": "meshcore_path",
                        "path_point": i + 1,
                        "total_points": len(path_coords),
                        "node_name": self._normalize_display_name(coord.get("name", "Unknown")),
                        "icon": "mdi:map-marker-path"
                    }
                )
                time.sleep(0.1)

            self.log(f"Created path for {sender_name} with {len(path_coords)} points")
            self.update_path_entities_sensor()

        except Exception as e:
            self.log(f"Error creating path tracker: {e}", level="ERROR")

    def clean_old_paths(self):
        now = time.time()
        old = [k for k, v in self.drawn_paths.items() if now - v.get("first_seen", 0) > 3600]
        for k in old:
            del self.drawn_paths[k]

    # -------------------------------------------------------------------------
    # Entity sensors
    # -------------------------------------------------------------------------

    def get_threshold_seconds(self):
        try:
            return float(self.get_state("input_number.meshcore_messages_threshold_hours")) * 3600
        except Exception:
            return 12.0 * 3600

    def update_entity_sensors(self, *args, **kwargs):
        self.update_path_entities_sensor()
        self.update_hop_entities_sensor()

    def update_path_entities_sensor(self, *args, **kwargs):
        try:
            all_states = self.get_state()
            path_entities = []
            now_ts = time.time()
            threshold_sec = self.get_threshold_seconds()

            for entity_id in all_states.keys():
                if not entity_id.startswith("device_tracker.meshcore_path_"):
                    continue
                attrs = (all_states[entity_id] or {}).get("attributes", {})
                if not attrs.get("latitude") or not attrs.get("longitude"):
                    continue
                safe_name = entity_id.replace("device_tracker.meshcore_path_", "")
                for hops_eid, hops_data in all_states.items():
                    if not hops_eid.startswith("sensor.meshcore_hops_"):
                        continue
                    hops_attrs = (hops_data or {}).get("attributes", {})
                    if self._safe_entity_name(hops_attrs.get("sender_name", "")) == safe_name:
                        last_msg = hops_attrs.get("last_message_time", 0)
                        if last_msg and (now_ts - last_msg) <= threshold_sec:
                            path_entities.append(entity_id)
                        break

            self.set_state(
                "sensor.meshcore_path_entities",
                state=str(len(path_entities)),
                attributes={
                    "friendly_name": "MeshCore Path Entities",
                    "entities": path_entities,
                    "icon": "mdi:map-marker-path",
                    "last_updated": datetime.now().isoformat()
                }
            )
            self.log(f"Updated sensor.meshcore_path_entities with {len(path_entities)} entities")
        except Exception as e:
            self.log(f"Error updating path entities sensor: {e}", level="ERROR")

    def update_hop_entities_sensor(self, *args, **kwargs):
        try:
            all_states = self.get_state()
            hop_entities = []
            now_ts = time.time()
            threshold_sec = self.get_threshold_seconds()

            for entity_id in all_states.keys():
                if not entity_id.startswith("device_tracker.meshcore_hop_"):
                    continue
                attrs = (all_states[entity_id] or {}).get("attributes", {})
                if not attrs.get("latitude") or not attrs.get("longitude"):
                    continue
                last_used = attrs.get("last_used", 0)
                if last_used and (now_ts - last_used) <= threshold_sec:
                    hop_entities.append(entity_id)

            self.set_state(
                "sensor.meshcore_hop_entities",
                state=str(len(hop_entities)),
                attributes={
                    "friendly_name": "MeshCore Hop Node Entities",
                    "entities": hop_entities,
                    "icon": "mdi:transit-connection-variant",
                    "last_updated": datetime.now().isoformat()
                }
            )
            self.log(f"Updated sensor.meshcore_hop_entities with {len(hop_entities)} entities")
        except Exception as e:
            self.log(f"Error updating hop entities sensor: {e}", level="ERROR")

    # -------------------------------------------------------------------------
    # Name helpers
    # -------------------------------------------------------------------------

    def _safe_entity_name(self, name):
        if not name:
            return "unknown"
        normalized = unicodedata.normalize('NFKD', name.lower())
        safe = "".join(c if (c.isalnum() and ord(c) < 128) or c == " " else "" for c in normalized)
        safe = re.sub(r'\s+', '_', safe.strip())
        safe = re.sub(r'_+', '_', safe).strip('_')
        return safe if safe else "unknown"

    def _normalize_display_name(self, name):
        if not name:
            return "Unknown"
        normalized = unicodedata.normalize('NFKD', name)
        result = "".join(c for c in normalized if ord(c) < 128).strip()
        while '  ' in result:
            result = result.replace('  ', ' ')
        return result.strip() or "Unknown"
