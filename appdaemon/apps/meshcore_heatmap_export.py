import appdaemon.plugins.hass.hassapi as hass
import json
import time

class MeshCoreHeatmapExport(hass.Hass):
    """
    Exports hop node data to JSON for the heatmap visualization.
    Writes to /config/www/meshcore_heatmap_data.json
    """

    def initialize(self):
        self.log("MeshCoreHeatmapExport initialized")
        
        # Export on startup
        self.run_in(self.export_heatmap_data, 10)
        
        # Export every 5 minutes
        self.run_every(self.export_heatmap_data, "now+60", 300)
        
        # Export when hop entities sensor updates
        self.listen_state(self.export_heatmap_data, "sensor.meshcore_hop_entities")
        
        # Export when threshold changes
        self.listen_state(self.export_heatmap_data, "input_number.meshcore_heatmap_threshold_hours")

    def get_threshold_seconds(self):
        """Get current threshold in seconds from input_number"""
        try:
            threshold_hours = float(self.get_state("input_number.meshcore_heatmap_threshold_hours"))
        except Exception:
            threshold_hours = 168.0  # Default 7 days
        return threshold_hours * 3600

    def export_heatmap_data(self, *args, **kwargs):
        """Export hop node data to JSON file"""
        try:
            all_states = self.get_state()
            hop_data = []
            path_data = []
            
            now_ts = time.time()
            threshold_sec = self.get_threshold_seconds()
            
            # Collect hop nodes
            for entity_id, state_data in all_states.items():
                if entity_id.startswith("device_tracker.meshcore_hop_"):
                    attrs = state_data.get("attributes", {}) if state_data else {}
                    
                    lat = attrs.get("latitude")
                    lon = attrs.get("longitude")
                    name = attrs.get("node_name", "Unknown")
                    use_count = attrs.get("use_count", 0)
                    last_used = attrs.get("last_used", 0)
                    node_type = attrs.get("node_type", "unknown")
                    
                    # Filter by threshold
                    if not last_used or (now_ts - last_used) > threshold_sec:
                        continue
                    
                    if lat and lon and use_count > 0:
                        hop_data.append({
                            "name": name,
                            "lat": float(lat),
                            "lon": float(lon),
                            "use_count": int(use_count),
                            "node_type": node_type.lower() if node_type else "unknown"
                        })
            
            # Build pubkey prefix -> coords lookup from contact sensors
            prefix_to_coords = {}
            for entity_id, state_data in all_states.items():
                if not (entity_id.startswith("binary_sensor.meshcore_") and "_contact" in entity_id):
                    continue
                attrs = state_data.get("attributes", {}) if state_data else {}
                pubkey = attrs.get("pubkey_prefix", "").lower()
                lat = attrs.get("adv_lat") or attrs.get("latitude")
                lon = attrs.get("adv_lon") or attrs.get("longitude")
                name = attrs.get("adv_name") or attrs.get("friendly_name", "").replace(" Contact", "")
                if pubkey and lat is not None and lon is not None:
                    for length in [2, 4, 6, 8, 10, 12, len(pubkey)]:
                        if len(pubkey) >= length:
                            short_key = pubkey[:length]
                            if short_key not in prefix_to_coords:
                                prefix_to_coords[short_key] = {
                                    "lat": float(lat), "lon": float(lon), "name": name
                                }

            # Collect recent paths from hops sensors
            for entity_id, state_data in all_states.items():
                if entity_id.startswith("sensor.meshcore_hops_"):
                    attrs = state_data.get("attributes", {}) if state_data else {}
                    
                    last_message = attrs.get("last_message_time", 0)
                    if not last_message or (now_ts - last_message) > threshold_sec:
                        continue
                    
                    path_nodes = attrs.get("path_nodes", [])
                    sender_name = attrs.get("sender_name", "Unknown")
                    
                    if len(path_nodes) >= 2:
                        path_coords = []
                        for node_prefix in path_nodes:
                            coords = prefix_to_coords.get(node_prefix.lower())
                            if coords:
                                path_coords.append(coords)
                        
                        if len(path_coords) >= 2:
                            path_data.append({
                                "sender": sender_name,
                                "coords": path_coords,
                                "hops": len(path_coords)
                            })
            
            # Sort by use_count descending
            hop_data.sort(key=lambda x: x["use_count"], reverse=True)
            
            # Get threshold for display
            try:
                threshold_hours = float(self.get_state("input_number.meshcore_heatmap_threshold_hours"))
            except:
                threshold_hours = 168.0
            
            # Write to www folder with metadata
            output_path = "/homeassistant/www/meshcore_heatmap_data.json"
            output_data = {
                "threshold_hours": threshold_hours,
                "node_count": len(hop_data),
                "path_count": len(path_data),
                "updated": time.time(),
                "nodes": hop_data,
                "paths": path_data
            }
            with open(output_path, 'w') as f:
                json.dump(output_data, f, indent=2)
            
            self.log(f"Exported {len(hop_data)} hop nodes, {len(path_data)} paths to heatmap (threshold: {threshold_sec/3600}h)")
            
        except Exception as e:
            self.log(f"Error exporting heatmap data: {e}", level="ERROR")

