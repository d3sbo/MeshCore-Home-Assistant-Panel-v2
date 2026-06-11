import appdaemon.plugins.hass.hassapi as hass
import json
import time

class MeshCoreNodeMapExport(hass.Hass):
    """
    Exports all node data to JSON for the node map visualization.
    Writes to /config/www/meshcore_nodemap_data.json
    """

    def normalize_timestamp(self, ts):
        """Convert millisecond timestamps to seconds if needed"""
        if ts and isinstance(ts, (int, float)) and ts > 1e12:
            return ts / 1000.0
        return ts

    def initialize(self):
        self.log("MeshCoreNodeMapExport initialized")
        
        # Export on startup
        self.run_in(self.export_nodemap_data, 15)
        
        # Export every 5 minutes
        self.run_every(self.export_nodemap_data, "now+60", 300)
        
        # Export when threshold changes
        self.listen_state(self.export_nodemap_data, "input_number.meshcore_advert_threshold_hours")
        
        # Export when map entities sensor updates
        self.listen_state(self.export_nodemap_data, "sensor.meshcore_map_entities")

    def get_threshold_seconds(self):
        """Get current threshold in seconds from input_number"""
        try:
            threshold_hours = float(self.get_state("input_number.meshcore_advert_threshold_hours"))
        except Exception:
            threshold_hours = 12.0
        return threshold_hours * 3600

    def export_nodemap_data(self, *args, **kwargs):
        """Export node data to JSON file"""
        try:
            all_states = self.get_state()
            node_data = []
            
            now_ts = time.time()
            threshold_sec = self.get_threshold_seconds()
            
            # Collect nodes from contact sensors
            for entity_id, state_data in all_states.items():
                if not entity_id.startswith("binary_sensor.meshcore_"):
                    continue
                
                attrs = state_data.get("attributes", {}) if state_data else {}
                
                if not attrs.get("pubkey_prefix") or not attrs.get("last_advert"):
                    continue
                
                lat = attrs.get("adv_lat") or attrs.get("latitude")
                lon = attrs.get("adv_lon") or attrs.get("longitude")
                name = attrs.get("adv_name") or attrs.get("friendly_name", "").replace(" Contact", "")
                last_advert = self.normalize_timestamp(attrs.get("last_advert", 0))
                node_type = attrs.get("node_type_str", "Unknown")
                
                # Use HA's last_updated as fallback when node clock is wrong (future timestamp)
                last_seen = last_advert
                if last_advert and last_advert > now_ts:
                    # Node clock is ahead - use HA's last_updated instead
                    ha_updated = state_data.get("last_updated")
                    if ha_updated:
                        try:
                            from datetime import datetime
                            if isinstance(ha_updated, str):
                                dt = datetime.fromisoformat(ha_updated.replace("Z", "+00:00"))
                                last_seen = dt.timestamp()
                        except Exception:
                            last_seen = now_ts  # fallback to now
                
                # Filter by threshold using corrected timestamp
                if not last_advert or (now_ts - last_seen) > threshold_sec:
                    continue
                
                if lat and lon:
                    age_hours = max(0, (now_ts - last_seen) / 3600) if last_seen else 0
                    
                    node_data.append({
                        "name": name,
                        "lat": float(lat),
                        "lon": float(lon),
                        "node_type": node_type.lower() if node_type else "unknown",
                        "last_advert": last_advert,
                        "last_seen": last_seen,
                        "age_hours": round(age_hours, 1)
                    })
            
            # Sort by name
            node_data.sort(key=lambda x: x["name"].lower())
            
            # Count by type
            type_counts = {}
            for node in node_data:
                nt = node["node_type"]
                type_counts[nt] = type_counts.get(nt, 0) + 1
            
            # Get threshold for display
            try:
                threshold_hours = float(self.get_state("input_number.meshcore_advert_threshold_hours"))
            except:
                threshold_hours = 12.0
            
            # Write to www folder with metadata
            output_path = "/homeassistant/www/meshcore_nodemap_data.json"
            output_data = {
                "threshold_hours": threshold_hours,
                "node_count": len(node_data),
                "type_counts": type_counts,
                "updated": time.time(),
                "nodes": node_data
            }
            with open(output_path, 'w') as f:
                json.dump(output_data, f, indent=2)
            
            self.log(f"Exported {len(node_data)} nodes to nodemap (threshold: {threshold_hours}h)")
            
        except Exception as e:
            self.log(f"Error exporting nodemap data: {e}", level="ERROR")
