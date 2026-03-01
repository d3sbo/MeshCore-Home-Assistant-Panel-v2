import appdaemon.plugins.hass.hassapi as hass
import time
import json
import os
import re
from datetime import datetime

class MeshCorePathMap(hass.Hass):
    """
    Creates device_tracker entities that trace message paths.
    The HA map card will show lines using the history feature.
    Also creates markers for each hop node used in paths.
    """

    def sanitize_for_entity_id(self, name):
        """Sanitize name for use in entity_id only - removes special chars"""
        if not name:
            return "unknown"
        # Keep only ASCII letters, numbers, spaces
        import re
        sanitized = "".join(c if c.isalnum() or c == " " else "" for c in name.lower())
        sanitized = re.sub(r'\s+', '_', sanitized.strip())
        sanitized = re.sub(r'_+', '_', sanitized)
        sanitized = sanitized.strip('_')
        return sanitized if sanitized else "unknown"

    def normalize_display_name(self, name):
        """Normalize name for display - remove problematic characters"""
        if not name:
            return "Unknown"
        import unicodedata
        # Normalize accented characters to ASCII equivalents
        normalized = unicodedata.normalize('NFKD', name)
        # Keep only ASCII characters and common emojis (skip for now)
        result = []
        for c in normalized:
            code = ord(c)
            if code < 128:
                # ASCII - keep
                result.append(c)
            # Skip all non-ASCII for now to debug the issue
        display = ''.join(result).strip()
        # Clean up multiple spaces
        while '  ' in display:
            display = display.replace('  ', ' ')
        return display.strip() if display.strip() else "Unknown"

    def initialize(self):
        self.log("MeshCorePathMap initialized")
        
        # Persistence file path
        self.persistence_file = "/homeassistant/www/meshcore_hops_data.json"
        
        # Your repeater's pubkey prefix - the endpoint of all paths
        self.my_repeater_pubkey = "YOUR_PUBKEY_HERE"
        self.my_coords = None
        
        # Cache for pubkey prefix -> coordinates
        self.node_coordinates = {}
        self.build_coordinate_cache()
        
        # Track paths we've already drawn
        self.drawn_paths = {}
        
        # Track hop nodes that have been used in paths (for markers)
        # Key: pubkey, Value: {coords, last_used, use_count}
        self.hop_nodes_used = {}
        
        # Load persisted data
        self.load_persisted_data()
        
        # Listen for new messages via hops sensor updates
        self.listen_state(self.handle_hops_update, "sensor", attribute="all")
        
        # Listen for threshold changes to update entity lists
        self.listen_state(self.update_entity_sensors, "input_number.meshcore_messages_threshold_hours")
        
        # Periodic cache refresh
        self.run_every(self.refresh_cache, "now+60", 300)  # Every 5 minutes
        
        # Periodic persistence save
        self.run_every(self.save_persisted_data, "now+120", 300)  # Every 5 minutes
        
        # Initial entity sensor update on startup
        self.run_in(self.update_entity_sensors, 30)
        
        # Restore hop markers from persisted data
        self.run_in(self.restore_hop_markers, 10)
    
    def load_persisted_data(self):
        """Load hop_nodes_used from JSON file"""
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
        """Save hop_nodes_used to JSON file"""
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
    
    def restore_hop_markers(self, kwargs=None):
        """Restore device_tracker entities from persisted hop data"""
        try:
            import re
            import unicodedata
            
            if not self.hop_nodes_used:
                self.log("No hop nodes to restore")
                return
            
            self.log(f"Restoring {len(self.hop_nodes_used)} hop node markers...")
            
            # First pass: group nodes by sanitized name
            nodes_by_name = {}
            for pubkey, data in self.hop_nodes_used.items():
                coords = data.get("coords", {})
                if not coords or not coords.get("lat") or not coords.get("lon"):
                    continue
                
                name = coords.get("name", "Unknown")
                
                # Sanitize name for entity ID - ASCII only
                normalized = unicodedata.normalize('NFKD', name.lower())
                safe_name = "".join(c if (c.isalnum() and ord(c) < 128) or c == " " else "" for c in normalized)
                safe_name = re.sub(r'\s+', '_', safe_name.strip())
                safe_name = re.sub(r'_+', '_', safe_name)
                safe_name = safe_name.strip('_')
                if not safe_name:
                    safe_name = "unknown"
                
                if safe_name not in nodes_by_name:
                    nodes_by_name[safe_name] = []
                nodes_by_name[safe_name].append((pubkey, data, coords))
            
            # Second pass: create entities, adding pubkey suffix only if needed
            for safe_name, nodes in nodes_by_name.items():
                # Check if nodes with same name are at different locations (>100m apart)
                needs_disambiguation = False
                if len(nodes) > 1:
                    first_lat = nodes[0][2].get("lat", 0)
                    first_lon = nodes[0][2].get("lon", 0)
                    for _, _, coords in nodes[1:]:
                        lat = coords.get("lat", 0)
                        lon = coords.get("lon", 0)
                        if abs(lat - first_lat) > 0.001 or abs(lon - first_lon) > 0.001:
                            needs_disambiguation = True
                            break
                
                for pubkey, data, coords in nodes:
                    name = coords.get("name", "Unknown")
                    node_type = coords.get("node_type", "Unknown")
                    
                    # Add pubkey suffix only if disambiguation needed
                    if needs_disambiguation:
                        entity_id = f"device_tracker.meshcore_hop_{safe_name}_{pubkey[:6]}"
                    else:
                        entity_id = f"device_tracker.meshcore_hop_{safe_name}"
                    
                    # Set icon based on node type
                    if "repeater" in node_type.lower():
                        icon = "mdi:radio-tower"
                    elif "client" in node_type.lower():
                        icon = "mdi:cellphone-wireless"
                    elif "room" in node_type.lower():
                        icon = "mdi:forum"
                    else:
                        icon = "mdi:access-point"
                    
                    # Normalize accented chars but keep emojis
                    display_name = self.normalize_display_name(name)
                    
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
                            "pubkey": pubkey,
                            "use_count": data.get("use_count", 0),
                            "last_used": data.get("last_used", 0),
                            "icon": icon
                        }
                    )
            
            self.log(f"Restored {len(self.hop_nodes_used)} hop node markers")
            
            # Update entity sensors
            self.update_entity_sensors()
            
        except Exception as e:
            self.log(f"Error restoring hop markers: {e}", level="ERROR")
    
    def get_threshold_seconds(self):
        """Get current threshold in seconds from input_number"""
        try:
            threshold_hours = float(self.get_state("input_number.meshcore_messages_threshold_hours"))
        except Exception:
            threshold_hours = 12.0
        return threshold_hours * 3600
    
    def update_entity_sensors(self, *args, **kwargs):
        """Update both path and hop entity sensors with threshold filtering"""
        self.update_path_entities_sensor()
        self.update_hop_entities_sensor()
    
    def refresh_cache(self, kwargs=None):
        """Refresh the coordinate cache"""
        self.build_coordinate_cache()
    
    def build_coordinate_cache(self):
        """Build cache of pubkey_prefix -> {lat, lon, name, node_type} from contact sensors"""
        try:
            all_states = self.get_state()
            self.node_coordinates = {}
            
            for ent_id, state_data in all_states.items():
                if ent_id.startswith("binary_sensor.meshcore_") and "_contact" in ent_id:
                    attrs = state_data.get("attributes", {})
                    pubkey = attrs.get("pubkey_prefix", "")
                    lat = attrs.get("adv_lat") or attrs.get("latitude")
                    lon = attrs.get("adv_lon") or attrs.get("longitude")
                    name = attrs.get("adv_name") or attrs.get("friendly_name", "").replace(" Contact", "")
                    node_type = attrs.get("node_type_str", "Unknown")
                    
                    # Check if this is my repeater by pubkey
                    if pubkey == self.my_repeater_pubkey and lat is not None and lon is not None:
                        self.my_coords = {
                            "lat": float(lat),
                            "lon": float(lon),
                            "name": name,
                            "pubkey": pubkey,
                            "node_type": node_type
                        }
                        self.log(f"Found my repeater: {name} at {lat}, {lon}")
                    
                    if pubkey and lat is not None and lon is not None:
                        # Store with various key lengths for matching
                        for length in [2, 4, 6, 8, 10, 12, len(pubkey)]:
                            if len(pubkey) >= length:
                                short_key = pubkey[:length].lower()
                                if short_key not in self.node_coordinates:
                                    self.node_coordinates[short_key] = {
                                        "lat": float(lat),
                                        "lon": float(lon),
                                        "name": name,
                                        "pubkey": pubkey,
                                        "node_type": node_type
                                    }
            
            self.log(f"Coordinate cache built with {len(self.node_coordinates)} entries")
        except Exception as e:
            self.log(f"Error building coordinate cache: {e}", level="ERROR")
    
    def get_node_coords(self, pubkey_prefix):
        """Look up coordinates for a node by pubkey prefix"""
        lower_key = pubkey_prefix.lower()
        
        # Special case: if this matches my repeater's prefix, use my coords
        if self.my_coords and self.my_repeater_pubkey.lower().startswith(lower_key):
            self.log(f"    Node {pubkey_prefix} matched my repeater")
            return self.my_coords
        
        # Collect all matches for this prefix
        matches = []
        for key, coords in self.node_coordinates.items():
            if key.startswith(lower_key) or lower_key.startswith(key):
                matches.append(coords)
        
        if not matches:
            return None
        
        # If only one match, use it
        if len(matches) == 1:
            return matches[0]
        
        # Multiple matches - pick the one closest to my repeater
        if self.my_coords and len(matches) > 1:
            def distance_to_me(node):
                # Simple distance calculation (not geodesic, but good enough for sorting)
                lat_diff = node["lat"] - self.my_coords["lat"]
                lon_diff = node["lon"] - self.my_coords["lon"]
                return lat_diff * lat_diff + lon_diff * lon_diff
            
            matches.sort(key=distance_to_me)
            self.log(f"    Node {pubkey_prefix} had {len(matches)} matches, picked closest: {matches[0]['name']}")
            return matches[0]
        
        # Fallback to first match
        return matches[0]
    
    def handle_hops_update(self, entity, attribute, old, new, kwargs):
        """Handle updates to hops sensors - create path trackers"""
        if "meshcore_hops_" not in entity:
            return
        
        try:
            if not new or "attributes" not in new:
                return
            
            attrs = new.get("attributes", {})
            path_nodes = attrs.get("path_nodes", [])
            sender_name = attrs.get("sender_name", "Unknown")
            sender_lat = attrs.get("latitude")
            sender_lon = attrs.get("longitude")
            last_message = attrs.get("last_message_time", 0)
            hops = attrs.get("max_hops", 0)  # Use max_hops for path visualization
            
            # Debug logging
            self.log(f"Path check: {sender_name} - max_hops={hops}, path_nodes={path_nodes}")
            
            # Skip if no path or direct message
            if not path_nodes or hops == 0:
                self.log(f"Skipping {sender_name}: no path_nodes or direct message")
                return
            
            # Create unique path ID
            path_id = f"{sender_name}_{last_message}"
            
            # Skip if we've already drawn this path recently
            if path_id in self.drawn_paths:
                if time.time() - self.drawn_paths[path_id] < 60:  # 60 second debounce
                    return
            
            self.drawn_paths[path_id] = time.time()
            
            # Build path coordinates
            path_coords = []
            
            # Add intermediate nodes (relay nodes)
            for node_prefix in path_nodes:
                node_coords = self.get_node_coords(node_prefix)
                if node_coords:
                    path_coords.append(node_coords)
                    self.log(f"  Found coords for node {node_prefix}: {node_coords['name']}")
                    
                    # Track this hop node for marker creation
                    self.track_hop_node(node_prefix, node_coords)
                else:
                    self.log(f"  No coords for node {node_prefix}")
            
            # Need at least 2 points to draw a line
            if len(path_coords) < 2:
                self.log(f"Not enough relay coordinates for path from {sender_name} (need 2+, got {len(path_coords)})")
                return
            
            # Create/update the path tracker
            self.create_path_tracker(sender_name, path_coords, last_message)
            
            # Update hop node markers
            self.update_hop_node_markers()
            
        except Exception as e:
            self.log(f"Error handling hops update: {e}", level="ERROR")
            import traceback
            self.log(traceback.format_exc(), level="ERROR")
    
    def track_hop_node(self, pubkey_prefix, coords):
        """Track a hop node that was used in a path"""
        key = coords.get("pubkey", pubkey_prefix).lower()
        
        if key in self.hop_nodes_used:
            self.hop_nodes_used[key]["last_used"] = time.time()
            self.hop_nodes_used[key]["use_count"] += 1
        else:
            self.hop_nodes_used[key] = {
                "coords": coords,
                "last_used": time.time(),
                "use_count": 1
            }
        
        # Save periodically (every 10 new hops tracked)
        total_uses = sum(h.get("use_count", 0) for h in self.hop_nodes_used.values())
        if total_uses % 10 == 0:
            self.save_persisted_data()
    
    def update_hop_node_markers(self):
        """Create/update device_tracker markers for all hop nodes used in paths"""
        try:
            import re
            import unicodedata
            
            # First pass: group nodes by sanitized name
            nodes_by_name = {}
            for pubkey, data in self.hop_nodes_used.items():
                coords = data["coords"]
                name = coords.get("name", "Unknown")
                
                # Sanitize name for entity ID - ASCII only
                normalized = unicodedata.normalize('NFKD', name.lower())
                safe_name = "".join(c if (c.isalnum() and ord(c) < 128) or c == " " else "" for c in normalized)
                safe_name = re.sub(r'\s+', '_', safe_name.strip())
                safe_name = re.sub(r'_+', '_', safe_name)
                safe_name = safe_name.strip('_')
                if not safe_name:
                    safe_name = "unknown"
                
                if safe_name not in nodes_by_name:
                    nodes_by_name[safe_name] = []
                nodes_by_name[safe_name].append((pubkey, data, coords))
            
            # Second pass: create entities, adding pubkey suffix only if needed for disambiguation
            for safe_name, nodes in nodes_by_name.items():
                # Check if nodes with same name are at different locations (>100m apart)
                needs_disambiguation = False
                if len(nodes) > 1:
                    first_lat = nodes[0][2].get("lat", 0)
                    first_lon = nodes[0][2].get("lon", 0)
                    for _, _, coords in nodes[1:]:
                        lat = coords.get("lat", 0)
                        lon = coords.get("lon", 0)
                        # Simple distance check (~100m threshold)
                        if abs(lat - first_lat) > 0.001 or abs(lon - first_lon) > 0.001:
                            needs_disambiguation = True
                            break
                
                for pubkey, data, coords in nodes:
                    name = coords.get("name", "Unknown")
                    node_type = coords.get("node_type", "Unknown")
                    
                    # Add pubkey suffix only if disambiguation needed
                    if needs_disambiguation:
                        entity_id = f"device_tracker.meshcore_hop_{safe_name}_{pubkey[:6]}"
                    else:
                        entity_id = f"device_tracker.meshcore_hop_{safe_name}"
                    
                    # Set icon based on node type
                    if "repeater" in node_type.lower():
                        icon = "mdi:radio-tower"
                    elif "client" in node_type.lower():
                        icon = "mdi:cellphone-wireless"
                    elif "room" in node_type.lower():
                        icon = "mdi:forum"
                    else:
                        icon = "mdi:access-point"
                    
                    # Normalize accented chars but keep emojis
                    display_name = self.normalize_display_name(name)
                    
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
                            "pubkey": pubkey,
                            "use_count": data["use_count"],
                            "last_used": data["last_used"],
                            "icon": icon
                        }
                    )
            
            # Update the hop entities sensor
            self.update_hop_entities_sensor()
            
        except Exception as e:
            self.log(f"Error updating hop node markers: {e}", level="ERROR")
    
    def update_hop_entities_sensor(self, *args, **kwargs):
        """Update sensor with list of hop node device_trackers filtered by threshold"""
        try:
            import json
            all_states = self.get_state()
            hop_entities = []
            
            now_ts = time.time()
            threshold_sec = self.get_threshold_seconds()
            
            for entity_id in all_states.keys():
                if entity_id.startswith("device_tracker.meshcore_hop_"):
                    state_data = all_states[entity_id]
                    attrs = state_data.get("attributes", {}) if state_data else {}
                    
                    # Only include entities with valid coordinates
                    if not attrs.get("latitude") or not attrs.get("longitude"):
                        continue
                    
                    # Filter by last_used time
                    last_used = attrs.get("last_used", 0)
                    if last_used and (now_ts - last_used) <= threshold_sec:
                        hop_entities.append(entity_id)
            
            self.set_state(
                "sensor.meshcore_hop_entities",
                state=str(len(hop_entities)),
                attributes={
                    "friendly_name": "MeshCore Hop Node Entities",
                    "entities": json.loads(json.dumps(hop_entities)),
                    "icon": "mdi:transit-connection-variant",
                    "last_updated": datetime.now().isoformat()
                }
            )
            
            self.log(f"Updated sensor.meshcore_hop_entities with {len(hop_entities)} entities (threshold: {threshold_sec/3600}h)")
            
        except Exception as e:
            self.log(f"Error updating hop entities sensor: {e}", level="ERROR")
    
    def create_path_tracker(self, sender_name, path_coords, timestamp):
        """Create a device_tracker that traces through the path points"""
        try:
            # Sanitize name for entity ID - ASCII alphanumeric only
            import re
            import unicodedata
            # First normalize accented chars to ASCII equivalents
            normalized = unicodedata.normalize('NFKD', sender_name.lower())
            # Keep only ASCII alphanumeric and spaces
            safe_name = "".join(c if (c.isalnum() and ord(c) < 128) or c == " " else "" for c in normalized)
            safe_name = re.sub(r'\s+', '_', safe_name.strip())  # Replace spaces with underscores
            safe_name = re.sub(r'_+', '_', safe_name)  # Remove consecutive underscores
            safe_name = safe_name.strip('_')  # Remove leading/trailing underscores
            if not safe_name:
                safe_name = "unknown"
            
            display_name = self.normalize_display_name(sender_name)
            
            entity_id = f"device_tracker.meshcore_path_{safe_name}"
            
            # Check if entity already exists
            existing = self.get_state(entity_id)
            if existing:
                self.log(f"Entity {entity_id} exists with state: {existing}")
            else:
                self.log(f"Entity {entity_id} does not exist yet, creating...")
            
            # Update the tracker through each point in the path
            # This creates history that the map will show as a line
            for i, coord in enumerate(path_coords):
                # Normalize node_name - accents to ASCII, keep emojis
                node_name = coord.get("name", "Unknown")
                safe_node_name = self.normalize_display_name(node_name)
                
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
                        "node_name": safe_node_name,
                        "icon": "mdi:map-marker-path"
                    }
                )
                # Small delay to create separate history points
                time.sleep(0.1)
            
            # Verify entity was created
            final_state = self.get_state(entity_id, attribute="all")
            if final_state:
                attrs = final_state.get("attributes", {})
                self.log(f"Created {entity_id} - lat: {attrs.get('latitude')}, lon: {attrs.get('longitude')}, node: {attrs.get('node_name')}")
            else:
                self.log(f"WARNING: Failed to verify {entity_id} creation", level="WARNING")
            
            # Update the sensor with all path entities
            self.update_path_entities_sensor()
            
            # Clean old drawn paths
            self.clean_old_paths()
            
        except Exception as e:
            self.log(f"Error creating path tracker: {e}", level="ERROR")
    
    def clean_old_paths(self):
        """Remove old entries from drawn_paths cache"""
        now = time.time()
        old_paths = [k for k, v in self.drawn_paths.items() if now - v > 3600]  # 1 hour
        for k in old_paths:
            del self.drawn_paths[k]
    
    def update_path_entities_sensor(self, *args, **kwargs):
        """Update sensor with list of path device_trackers filtered by threshold"""
        try:
            import json
            import re
            all_states = self.get_state()
            path_entities = []
            
            now_ts = time.time()
            threshold_sec = self.get_threshold_seconds()
            
            for entity_id in all_states.keys():
                if entity_id.startswith("device_tracker.meshcore_path_"):
                    state_data = all_states[entity_id]
                    attrs = state_data.get("attributes", {}) if state_data else {}
                    
                    # Only include entities with valid coordinates
                    if not attrs.get("latitude") or not attrs.get("longitude"):
                        continue
                    
                    # Extract sender name from entity_id to find corresponding hops sensor
                    # entity_id format: device_tracker.meshcore_path_<safe_name>
                    safe_name = entity_id.replace("device_tracker.meshcore_path_", "")
                    
                    # Find the corresponding hops sensor by checking all hops sensors
                    # for a matching sender_name
                    include_entity = False
                    for hops_entity_id, hops_data in all_states.items():
                        if hops_entity_id.startswith("sensor.meshcore_hops_"):
                            hops_attrs = hops_data.get("attributes", {}) if hops_data else {}
                            sender_name = hops_attrs.get("sender_name", "")
                            
                            # Sanitize the sender name the same way path tracker does
                            check_name = "".join(c if c.isalnum() or c == " " else "" for c in sender_name.lower())
                            check_name = re.sub(r'\s+', '_', check_name.strip())
                            check_name = re.sub(r'_+', '_', check_name)
                            check_name = check_name.strip('_')
                            
                            if check_name == safe_name:
                                # Found matching hops sensor - check last_message_time
                                last_message = hops_attrs.get("last_message_time", 0)
                                if last_message and (now_ts - last_message) <= threshold_sec:
                                    include_entity = True
                                break
                    
                    if include_entity:
                        path_entities.append(entity_id)
            
            self.set_state(
                "sensor.meshcore_path_entities",
                state=str(len(path_entities)),
                attributes={
                    "friendly_name": "MeshCore Path Entities",
                    "entities": json.loads(json.dumps(path_entities)),
                    "icon": "mdi:map-marker-path",
                    "last_updated": datetime.now().isoformat()
                }
            )
            
            self.log(f"Updated sensor.meshcore_path_entities with {len(path_entities)} entities (threshold: {threshold_sec/3600}h)")
            
        except Exception as e:
            self.log(f"Error updating path entities sensor: {e}", level="ERROR")
