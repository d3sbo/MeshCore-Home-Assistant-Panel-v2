import appdaemon.plugins.hass.hassapi as hass
import json
import time
import os
from datetime import datetime

class MeshCoreDirectLinksExport(hass.Hass):
    """
    Exports direct link (1-hop) data to JSON for visualization.
    Listens directly to meshcore_raw_event instead of sensor state changes
    to avoid the sensor cascade that caused ever-growing thread queues.
    """

    def initialize(self):
        self.log("MeshCoreDirectLinksExport initialized")

        self.direct_links = {}
        self.persistence_file = "/homeassistant/www/meshcore_directlinks_persist.json"
        self.load_persisted_data()

        # Debounce timer for export
        self._export_timer = None

        # Export on startup
        self.run_in(self.export_directlinks_data, 20)

        # Periodic export every 5 minutes
        self.run_every(self.export_directlinks_data, "now+60", 300)

        # Listen directly to raw meshcore events - no sensor state cascade
        self.listen_event(self.handle_raw_event, "meshcore_raw_event")

        # Listen for threshold changes
        self.listen_state(self.export_directlinks_data, "input_number.meshcore_heatmap_threshold_hours")

    # -------------------------------------------------------------------------
    # Raw event handling
    # -------------------------------------------------------------------------

    def handle_raw_event(self, event_name, data, kwargs):
        """Handle meshcore_raw_event - extract direct links from RX_LOG_DATA"""
        try:
            if data.get("event_type") != "EventType.RX_LOG_DATA":
                return

            payload = data.get("payload", {})
            decrypted = payload.get("decrypted", {})

            # Skip undecrypted packets
            if not decrypted.get("decrypted"):
                return

            parsed = payload.get("parsed", {})
            path_nodes = parsed.get("path_nodes", [])

            # Need at least 2 nodes to have a direct link
            if len(path_nodes) < 2:
                return

            now_ts = time.time()

            # Each consecutive pair in path_nodes represents a direct link
            for i in range(len(path_nodes) - 1):
                node_a = path_nodes[i].lower()
                node_b = path_nodes[i + 1].lower()
                self.record_direct_link(node_a, node_b, now_ts)
                self.record_direct_link(node_b, node_a, now_ts)

            # Schedule debounced export
            self._schedule_export()

        except Exception as e:
            self.log(f"Error handling raw event: {e}", level="ERROR")

    # -------------------------------------------------------------------------
    # Debounce
    # -------------------------------------------------------------------------

    def _schedule_export(self):
        """Debounce export - waits 5s after last activity before running"""
        try:
            if self._export_timer is not None:
                self.cancel_timer(self._export_timer)
        except Exception:
            pass
        self._export_timer = self.run_in(self._run_export, 5)

    def _run_export(self, kwargs=None):
        self._export_timer = None
        self.export_directlinks_data()

    # -------------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------------

    def load_persisted_data(self):
        try:
            if os.path.exists(self.persistence_file):
                with open(self.persistence_file, 'r') as f:
                    data = json.load(f)
                    self.direct_links = data.get("direct_links", {})
                    self.log(f"Loaded {len(self.direct_links)} nodes with direct links from persistence")
        except Exception as e:
            self.log(f"Error loading persisted data: {e}", level="WARNING")
            self.direct_links = {}

    def save_persisted_data(self):
        try:
            now_ts = time.time()
            max_age = 7 * 24 * 3600

            cleaned_links = {}
            for node_a, connections in self.direct_links.items():
                cleaned = {k: v for k, v in connections.items()
                           if (now_ts - v.get("last_seen", 0)) <= max_age}
                if cleaned:
                    cleaned_links[node_a] = cleaned
            self.direct_links = cleaned_links

            data = {
                "direct_links": self.direct_links,
                "saved_at": now_ts,
                "saved_at_formatted": datetime.now().isoformat()
            }
            with open(self.persistence_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.log(f"Error saving persisted data: {e}", level="ERROR")

    # -------------------------------------------------------------------------
    # Link tracking
    # -------------------------------------------------------------------------

    def record_direct_link(self, node_a, node_b, timestamp):
        if node_a not in self.direct_links:
            self.direct_links[node_a] = {}
        if node_b in self.direct_links[node_a]:
            self.direct_links[node_a][node_b]["last_seen"] = timestamp
            self.direct_links[node_a][node_b]["count"] = self.direct_links[node_a][node_b].get("count", 0) + 1
        else:
            self.direct_links[node_a][node_b] = {"last_seen": timestamp, "count": 1}

    # -------------------------------------------------------------------------
    # Export
    # -------------------------------------------------------------------------

    def get_threshold_seconds(self):
        try:
            return float(self.get_state("input_number.meshcore_heatmap_threshold_hours")) * 3600
        except Exception:
            return 168.0 * 3600  # 7 days default

    def get_node_info(self, pubkey_prefix, all_states):
        """Get node coordinates and info from contact sensors"""
        matches = []
        for entity_id, state_data in all_states.items():
            if not (entity_id.startswith("binary_sensor.meshcore_") and "_contact" in entity_id):
                continue
            attrs = (state_data or {}).get("attributes", {})
            pubkey = attrs.get("pubkey_prefix", "").lower()
            if not pubkey or not pubkey.startswith(pubkey_prefix):
                continue
            lat = attrs.get("adv_lat") or attrs.get("latitude")
            lon = attrs.get("adv_lon") or attrs.get("longitude")
            if not lat or not lon:
                continue
            name = attrs.get("adv_name") or attrs.get("friendly_name", "").replace(" Contact", "")
            node_type = attrs.get("node_type_str", "Unknown").lower()
            last_advert = attrs.get("last_advert", 0) or 0
            matches.append({"name": name, "lat": float(lat), "lon": float(lon),
                            "pubkey": pubkey, "node_type": node_type, "last_advert": last_advert})

        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]

        repeaters = [m for m in matches if "repeater" in m["node_type"]]
        if repeaters:
            repeaters.sort(key=lambda x: x["last_advert"], reverse=True)
            return repeaters[0]

        matches.sort(key=lambda x: x["last_advert"], reverse=True)
        return matches[0]

    def export_directlinks_data(self, *args, **kwargs):
        """Export direct links data to JSON file"""
        try:
            all_states = self.get_state()
            now_ts = time.time()
            threshold_sec = self.get_threshold_seconds()

            node_data = {}
            link_data = []

            for node_a_prefix, connections in self.direct_links.items():
                node_a_info = self.get_node_info(node_a_prefix, all_states)
                if not node_a_info:
                    continue

                for node_b_prefix, link_info in connections.items():
                    if (now_ts - link_info.get("last_seen", 0)) > threshold_sec:
                        continue

                    node_b_info = self.get_node_info(node_b_prefix, all_states)
                    if not node_b_info:
                        continue

                    if node_a_info["pubkey"] not in node_data:
                        node_data[node_a_info["pubkey"]] = {
                            "name": node_a_info["name"],
                            "lat": node_a_info["lat"],
                            "lon": node_a_info["lon"],
                            "node_type": node_a_info["node_type"],
                            "link_count": 0
                        }
                    node_data[node_a_info["pubkey"]]["link_count"] += 1

                    link_key = tuple(sorted([node_a_info["pubkey"], node_b_info["pubkey"]]))
                    link_exists = False
                    for existing in link_data:
                        if tuple(sorted([existing["from_pubkey"], existing["to_pubkey"]])) == link_key:
                            existing["count"] = max(existing["count"], link_info.get("count", 1))
                            link_exists = True
                            break

                    if not link_exists:
                        link_data.append({
                            "from_pubkey": node_a_info["pubkey"],
                            "from_name": node_a_info["name"],
                            "from_lat": node_a_info["lat"],
                            "from_lon": node_a_info["lon"],
                            "to_pubkey": node_b_info["pubkey"],
                            "to_name": node_b_info["name"],
                            "to_lat": node_b_info["lat"],
                            "to_lon": node_b_info["lon"],
                            "count": link_info.get("count", 1)
                        })

            nodes_list = sorted(
                [{"name": v["name"], "lat": v["lat"], "lon": v["lon"],
                  "node_type": v["node_type"], "link_count": v["link_count"]}
                 for v in node_data.values()],
                key=lambda x: x["link_count"], reverse=True
            )

            try:
                threshold_hours = float(self.get_state("input_number.meshcore_heatmap_threshold_hours"))
            except Exception:
                threshold_hours = 168.0

            output_path = "/homeassistant/www/meshcore_directlinks_data.json"
            with open(output_path, 'w') as f:
                json.dump({
                    "threshold_hours": threshold_hours,
                    "node_count": len(nodes_list),
                    "link_count": len(link_data),
                    "updated": time.time(),
                    "nodes": nodes_list,
                    "links": link_data
                }, f, indent=2)

            self.save_persisted_data()
            self.log(f"Exported {len(nodes_list)} nodes, {len(link_data)} direct links (threshold: {threshold_hours}h)")

        except Exception as e:
            self.log(f"Error exporting direct links data: {e}", level="ERROR")
            import traceback
            self.log(traceback.format_exc(), level="ERROR")
