import appdaemon.plugins.hass.hassapi as hass
import json
import time
from datetime import datetime

class MeshCoreDirectLinksExport(hass.Hass):
    """
    Exports direct link (1-hop) data to JSON for visualization.
    Shows which nodes can directly reach each other.
    Writes to /config/www/meshcore_directlinks_data.json
    """

    def initialize(self):
        self.log("MeshCoreDirectLinksExport initialized")
        
        # Track direct links: {node_a_pubkey: {node_b_pubkey: {last_seen, count}}}
        self.direct_links = {}
        
        # Persistence file
        self.persistence_file = "/homeassistant/www/meshcore_directlinks_persist.json"
        self.load_persisted_data()
        
        # Export on startup
        self.run_in(self.export_directlinks_data, 20)
        
        # Export every 5 minutes
        self.run_every(self.export_directlinks_data, "now+60", 300)
        
        # Listen for hops sensor updates to capture direct links from paths
        self.listen_state(self.handle_hops_update, "sensor", attribute="all")
        
        # Listen for threshold changes
        self.listen_state(self.export_directlinks_data, "input_number.meshcore_heatmap_threshold_hours")

    def load_persisted_data(self):
        """Load direct links from persistence file"""
        try:
            import os
            if os.path.exists(self.persistence_file):
                with open(self.persistence_file, 'r') as f:
                    data = json.load(f)
                    self.direct_links = data.get("direct_links", {})
                    self.log(f"Loaded {len(self.direct_links)} nodes with direct links from persistence")
        except Exception as e:
            self.log(f"Error loading persisted data: {e}", level="WARNING")
            self.direct_links = {}

    def save_persisted_data(self):
        """Save direct links to persistence file"""
        try:
            # Clean old links (older than 7 days)
            now_ts = time.time()
            max_age = 7 * 24 * 3600
            
            cleaned_links = {}
            for node_a, connections in self.direct_links.items():
                cleaned_connections = {}
                for node_b, link_data in connections.items():
                    if (now_ts - link_data.get("last_seen", 0)) <= max_age:
                        cleaned_connections[node_b] = link_data
                if cleaned_connections:
                    cleaned_links[node_a] = cleaned_connections
            
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

    def get_threshold_seconds(self):
        """Get current threshold in seconds from input_number"""
        try:
            threshold_hours = float(self.get_state("input_number.meshcore_heatmap_threshold_hours"))
        except Exception:
            threshold_hours = 168.0  # Default 7 days
        return threshold_hours * 3600

    def handle_hops_update(self, entity, attribute, old, new, kwargs):
        """Extract direct links from path data"""
        if "meshcore_hops_" not in entity:
            return
        
        try:
            if not new or "attributes" not in new:
                return
            
            attrs = new.get("attributes", {})
            path_nodes = attrs.get("path_nodes", [])
            
            # Need at least 2 nodes to have a direct link
            if len(path_nodes) < 2:
                return
            
            now_ts = time.time()
            
            # Each consecutive pair in path_nodes represents a direct link
            for i in range(len(path_nodes) - 1):
                node_a = path_nodes[i].lower()
                node_b = path_nodes[i + 1].lower()
                
                # Store bidirectionally (both directions)
                self.record_direct_link(node_a, node_b, now_ts)
                self.record_direct_link(node_b, node_a, now_ts)
            
            # Save periodically
            total_links = sum(len(v) for v in self.direct_links.values())
            if total_links % 20 == 0:
                self.save_persisted_data()
                
        except Exception as e:
            self.log(f"Error handling hops update: {e}", level="ERROR")

    def record_direct_link(self, node_a, node_b, timestamp):
        """Record a direct link between two nodes"""
        if node_a not in self.direct_links:
            self.direct_links[node_a] = {}
        
        if node_b in self.direct_links[node_a]:
            self.direct_links[node_a][node_b]["last_seen"] = timestamp
            self.direct_links[node_a][node_b]["count"] = self.direct_links[node_a][node_b].get("count", 0) + 1
        else:
            self.direct_links[node_a][node_b] = {
                "last_seen": timestamp,
                "count": 1
            }

    def get_node_info(self, pubkey_prefix, all_states):
        """Get node coordinates and info from contact sensors"""
        matches = []
        
        for entity_id, state_data in all_states.items():
            if not (entity_id.startswith("binary_sensor.meshcore_") and "_contact" in entity_id):
                continue
            
            attrs = state_data.get("attributes", {}) if state_data else {}
            pubkey = attrs.get("pubkey_prefix", "").lower()
            
            # Match by prefix (path_nodes are 2-char prefixes)
            if pubkey and pubkey.startswith(pubkey_prefix):
                lat = attrs.get("adv_lat") or attrs.get("latitude")
                lon = attrs.get("adv_lon") or attrs.get("longitude")
                name = attrs.get("adv_name") or attrs.get("friendly_name", "").replace(" Contact", "")
                node_type = attrs.get("node_type_str", "Unknown")
                
                if lat and lon:
                    matches.append({
                        "name": name,
                        "lat": float(lat),
                        "lon": float(lon),
                        "pubkey": pubkey,
                        "node_type": node_type.lower()
                    })
        
        if not matches:
            return None
        
        # If only one match, return it
        if len(matches) == 1:
            return matches[0]
        
        # Multiple matches - prefer repeaters over clients/room servers
        for m in matches:
            if "repeater" in m["node_type"]:
                return m
        
        # No repeater found, return first match
        return matches[0]

    def export_directlinks_data(self, *args, **kwargs):
        """Export direct links data to JSON file"""
        try:
            all_states = self.get_state()
            now_ts = time.time()
            threshold_sec = self.get_threshold_seconds()
            
            # Build node data with direct link counts
            node_data = {}  # pubkey -> {name, lat, lon, link_count}
            link_data = []  # [{from: {lat, lon}, to: {lat, lon}, count}]
            
            # Process all direct links
            for node_a_prefix, connections in self.direct_links.items():
                node_a_info = self.get_node_info(node_a_prefix, all_states)
                if not node_a_info:
                    continue
                
                for node_b_prefix, link_info in connections.items():
                    # Filter by threshold
                    if (now_ts - link_info.get("last_seen", 0)) > threshold_sec:
                        continue
                    
                    node_b_info = self.get_node_info(node_b_prefix, all_states)
                    if not node_b_info:
                        continue
                    
                    # Track node with link count
                    if node_a_info["pubkey"] not in node_data:
                        node_data[node_a_info["pubkey"]] = {
                            "name": node_a_info["name"],
                            "lat": node_a_info["lat"],
                            "lon": node_a_info["lon"],
                            "node_type": node_a_info["node_type"],
                            "link_count": 0
                        }
                    node_data[node_a_info["pubkey"]]["link_count"] += 1
                    
                    # Create unique link key to avoid duplicates (A-B same as B-A)
                    link_key = tuple(sorted([node_a_info["pubkey"], node_b_info["pubkey"]]))
                    
                    # Check if we already have this link
                    link_exists = False
                    for existing_link in link_data:
                        existing_key = tuple(sorted([existing_link["from_pubkey"], existing_link["to_pubkey"]]))
                        if existing_key == link_key:
                            link_exists = True
                            # Update count to max
                            existing_link["count"] = max(existing_link["count"], link_info.get("count", 1))
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
            
            # Convert node_data to list, sorted by link_count
            nodes_list = [
                {
                    "name": v["name"],
                    "lat": v["lat"],
                    "lon": v["lon"],
                    "node_type": v["node_type"],
                    "link_count": v["link_count"]
                }
                for v in node_data.values()
            ]
            nodes_list.sort(key=lambda x: x["link_count"], reverse=True)
            
            # Get threshold for display
            try:
                threshold_hours = float(self.get_state("input_number.meshcore_heatmap_threshold_hours"))
            except:
                threshold_hours = 168.0
            
            # Write to www folder
            output_path = "/homeassistant/www/meshcore_directlinks_data.json"
            output_data = {
                "threshold_hours": threshold_hours,
                "node_count": len(nodes_list),
                "link_count": len(link_data),
                "updated": time.time(),
                "nodes": nodes_list,
                "links": link_data
            }
            with open(output_path, 'w') as f:
                json.dump(output_data, f, indent=2)
            
            # Save persistence
            self.save_persisted_data()
            
            self.log(f"Exported {len(nodes_list)} nodes, {len(link_data)} direct links (threshold: {threshold_hours}h)")
            
        except Exception as e:
            self.log(f"Error exporting direct links data: {e}", level="ERROR")
            import traceback
            self.log(traceback.format_exc(), level="ERROR")
