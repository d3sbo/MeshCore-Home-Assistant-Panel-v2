import appdaemon.plugins.hass.hassapi as hass
import json
import os
import time
from datetime import datetime

class MeshCoreSnapshotRecorder(hass.Hass):
    """
    Records snapshots of heatmap and directlinks data every 5 minutes.
    Stores 24 hours of history server-side so playback works without browser.
    
    Records RAW data from persistence files (not threshold-filtered).
    Playback HTML applies threshold filtering client-side.
    """

    def initialize(self):
        self.log("MeshCoreSnapshotRecorder initialized")
        
        # File paths - use /homeassistant for HA OS Add-on
        self.www_path = "/homeassistant/www"
        
        # RAW persistence files (contains ALL data, not threshold-filtered)
        self.hops_persist_file = f"{self.www_path}/meshcore_hops_data.json"
        self.directlinks_persist_file = f"{self.www_path}/meshcore_directlinks_persist.json"
        
        # History output files
        self.heatmap_history_file = f"{self.www_path}/meshcore_heatmap_history.json"
        self.directlinks_history_file = f"{self.www_path}/meshcore_directlinks_history.json"
        
        # Settings
        self.max_snapshots = 288  # 24 hours at 5-min intervals
        self.snapshot_interval = 5 * 60  # 5 minutes in seconds
        self.min_snapshot_gap = 30  # Minimum seconds between snapshots
        self.last_snapshot_time = 0
        
        # Load existing history
        self.heatmap_history = self.load_history(self.heatmap_history_file)
        self.directlinks_history = self.load_history(self.directlinks_history_file)
        
        self.log(f"Loaded {len(self.heatmap_history)} heatmap snapshots")
        self.log(f"Loaded {len(self.directlinks_history)} directlinks snapshots")
        
        # Take initial snapshot
        self.run_in(self.take_snapshots, 10)
        
        # Schedule regular snapshots every 5 minutes
        self.run_every(self.take_snapshots, f"now+60", self.snapshot_interval)
        
        # Listen for MeshCore events to capture on message activity
        self.listen_event(self.on_meshcore_event, "meshcore_raw_event")
        
    def load_history(self, filepath):
        """Load snapshot history from file"""
        try:
            if os.path.exists(filepath):
                with open(filepath, 'r') as f:
                    data = json.load(f)
                    snapshots = data.get("snapshots", [])
                    
                    # Clean old snapshots (older than 24 hours)
                    cutoff = time.time() - (24 * 60 * 60)
                    snapshots = [s for s in snapshots if s.get("timestamp", 0) > cutoff]
                    
                    return snapshots
        except Exception as e:
            self.log(f"Error loading history from {filepath}: {e}", level="WARNING")
        return []
    
    def save_history(self, filepath, snapshots):
        """Save snapshot history to file"""
        try:
            # Keep only last max_snapshots
            if len(snapshots) > self.max_snapshots:
                snapshots = snapshots[-self.max_snapshots:]
            
            data = {
                "snapshots": snapshots,
                "count": len(snapshots),
                "max_snapshots": self.max_snapshots,
                "snapshot_interval_minutes": self.snapshot_interval // 60,
                "last_updated": datetime.now().isoformat(),
                "version": int(time.time())  # Unix timestamp for cache busting
            }
            
            with open(filepath, 'w') as f:
                json.dump(data, f)
            
            self.log(f"Saved history to {filepath} ({len(snapshots)} snapshots)")
                
        except Exception as e:
            self.log(f"Error saving history to {filepath}: {e}", level="ERROR")
    
    def get_data_hash(self, nodes):
        """Create a simple hash to detect data changes"""
        if not nodes:
            return ""
        try:
            # Hash based on node count and first/last few nodes
            hash_str = str(len(nodes))
            if nodes:
                hash_str += json.dumps([(n.get("name", ""), n.get("use_count", n.get("link_count", 0))) for n in nodes[:10]])
            return hash_str
        except:
            return ""
    
    def take_snapshots(self, kwargs=None):
        """Take snapshots of both heatmap and directlinks data"""
        self.log("Taking snapshots...")
        self.take_heatmap_snapshot()
        self.take_directlinks_snapshot()
        self.last_snapshot_time = time.time()
    
    def on_meshcore_event(self, event_name, data, kwargs):
        """Handle MeshCore events - take snapshot on message activity"""
        try:
            event_type = data.get("event_type", "")
            
            # Only snapshot on message events
            if "MSG" in event_type or "LOG" in event_type:
                # Rate limit - don't snapshot more than once per 30 seconds
                now = time.time()
                if (now - self.last_snapshot_time) >= self.min_snapshot_gap:
                    # Delay slightly to let other scripts update the data files first
                    self.run_in(self.take_snapshots_on_event, 5)
        except Exception as e:
            self.log(f"Error handling meshcore event: {e}", level="ERROR")
    
    def take_snapshots_on_event(self, kwargs=None):
        """Take snapshots triggered by event (with rate limiting)"""
        now = time.time()
        if (now - self.last_snapshot_time) >= self.min_snapshot_gap:
            self.log("Taking snapshot on message activity...")
            self.take_heatmap_snapshot()
            self.take_directlinks_snapshot()
            self.last_snapshot_time = now
    
    def take_heatmap_snapshot(self):
        """Take a snapshot of heatmap data from RAW persistence file"""
        try:
            # Try to read from hops persistence file (raw data)
            if not os.path.exists(self.hops_persist_file):
                self.log(f"Hops persistence file not found: {self.hops_persist_file}")
                return
            
            with open(self.hops_persist_file, 'r') as f:
                file_data = json.load(f)
            
            # Data is nested under 'hop_nodes_used' key
            raw_data = file_data.get("hop_nodes_used", file_data)
            
            if not raw_data or not isinstance(raw_data, dict):
                self.log("No valid data in hops persistence file")
                return
            
            # Convert raw persistence data to node list with coordinates
            nodes = []
            for pubkey, data in raw_data.items():
                if not isinstance(data, dict):
                    continue
                coords = data.get("coords", {})
                if coords and coords.get("lat") and coords.get("lon"):
                    nodes.append({
                        "name": coords.get("name", "Unknown"),
                        "lat": coords.get("lat"),
                        "lon": coords.get("lon"),
                        "use_count": data.get("use_count", 0),
                        "last_used": data.get("last_used", 0),
                        "node_type": coords.get("node_type", "Unknown"),
                        "pubkey": pubkey
                    })
            
            if not nodes:
                self.log("No nodes with coordinates in hops data")
                return
            
            # Check if data has changed
            current_hash = self.get_data_hash(nodes)
            if self.heatmap_history:
                last_hash = self.get_data_hash(self.heatmap_history[-1].get("nodes", []))
                if current_hash == last_hash:
                    self.log(f"Heatmap data unchanged, skipping snapshot ({len(self.heatmap_history)} total)")
                    return  # No change, skip
            
            # Create snapshot with ALL data (no threshold filtering)
            snapshot = {
                "timestamp": time.time(),
                "nodes": nodes,
                "paths": [],  # Paths would need separate handling
                "threshold_hours": None  # Raw data - no threshold applied
            }
            
            self.heatmap_history.append(snapshot)
            self.save_history(self.heatmap_history_file, self.heatmap_history)
            
            self.log(f"Heatmap snapshot taken: {len(self.heatmap_history)} total ({len(nodes)} nodes)")
            
        except Exception as e:
            self.log(f"Error taking heatmap snapshot: {e}", level="ERROR")
    
    def take_directlinks_snapshot(self):
        """Take a snapshot of directlinks data from RAW persistence file"""
        try:
            # Read from the raw persistence file
            if not os.path.exists(self.directlinks_persist_file):
                self.log(f"Directlinks persistence file not found: {self.directlinks_persist_file}")
                return
            
            with open(self.directlinks_persist_file, 'r') as f:
                file_data = json.load(f)
            
            # Data is nested under 'direct_links' key
            raw_links = file_data.get("direct_links", file_data)
            
            if not raw_links or not isinstance(raw_links, dict):
                self.log("No valid data in directlinks persistence file")
                return
            
            # Build coordinate lookup from contact sensors
            coord_lookup = {}
            try:
                all_states = self.get_state()
                for entity_id, state_data in all_states.items():
                    if not (entity_id.startswith("binary_sensor.meshcore_") and "_contact" in entity_id):
                        continue
                    attrs = state_data.get("attributes", {})
                    pubkey = attrs.get("pubkey_prefix", "")
                    lat = attrs.get("adv_lat")
                    lon = attrs.get("adv_lon")
                    name = attrs.get("adv_name", "Unknown")
                    node_type = attrs.get("node_type_str", "Unknown")
                    last_advert = attrs.get("last_advert", 0)
                    
                    if pubkey and lat and lon:
                        coord_lookup[pubkey[:2]] = {
                            "name": name,
                            "lat": lat,
                            "lon": lon,
                            "node_type": node_type,
                            "pubkey": pubkey,
                            "last_advert": last_advert
                        }
            except Exception as e:
                self.log(f"Error building coord lookup: {e}", level="WARNING")
            
            # Build nodes and links lists
            nodes = {}
            all_links = []
            current_time = time.time()
            
            for from_prefix, targets in raw_links.items():
                if not isinstance(targets, dict):
                    continue
                    
                # Get coords for source node
                from_coords = coord_lookup.get(from_prefix, {})
                if not from_coords.get("lat"):
                    continue
                
                # Add source node
                if from_prefix not in nodes:
                    nodes[from_prefix] = {
                        "name": from_coords.get("name", "Unknown"),
                        "lat": from_coords.get("lat"),
                        "lon": from_coords.get("lon"),
                        "node_type": from_coords.get("node_type", "Unknown"),
                        "pubkey": from_coords.get("pubkey", from_prefix),
                        "link_count": 0,
                        "last_seen": from_coords.get("last_advert", current_time)
                    }
                
                for to_prefix, link_data in targets.items():
                    if not isinstance(link_data, dict):
                        continue
                    
                    # Get coords for target node
                    to_coords = coord_lookup.get(to_prefix, {})
                    if not to_coords.get("lat"):
                        continue
                    
                    # Add target node
                    if to_prefix not in nodes:
                        nodes[to_prefix] = {
                            "name": to_coords.get("name", "Unknown"),
                            "lat": to_coords.get("lat"),
                            "lon": to_coords.get("lon"),
                            "node_type": to_coords.get("node_type", "Unknown"),
                            "pubkey": to_coords.get("pubkey", to_prefix),
                            "link_count": 0,
                            "last_seen": to_coords.get("last_advert", current_time)
                        }
                    
                    # Increment link counts
                    nodes[from_prefix]["link_count"] += 1
                    
                    # Add link
                    last_seen = link_data.get("last_seen", current_time)
                    all_links.append({
                        "from_name": from_coords.get("name", "Unknown"),
                        "from_lat": from_coords.get("lat"),
                        "from_lon": from_coords.get("lon"),
                        "to_name": to_coords.get("name", "Unknown"),
                        "to_lat": to_coords.get("lat"),
                        "to_lon": to_coords.get("lon"),
                        "count": link_data.get("count", 1),
                        "last_seen": last_seen
                    })
            
            nodes_list = list(nodes.values())
            
            if not nodes_list:
                self.log("No nodes with coordinates in directlinks data")
                return
            
            # Check if data has changed
            current_hash = self.get_data_hash(nodes_list)
            if self.directlinks_history:
                last_hash = self.get_data_hash(self.directlinks_history[-1].get("nodes", []))
                if current_hash == last_hash:
                    self.log(f"Directlinks data unchanged, skipping snapshot ({len(self.directlinks_history)} total)")
                    return  # No change, skip
            
            # Create snapshot with ALL data (no threshold filtering)
            snapshot = {
                "timestamp": time.time(),
                "nodes": nodes_list,
                "links": all_links,
                "threshold_hours": None  # Raw data - no threshold applied
            }
            
            self.directlinks_history.append(snapshot)
            self.save_history(self.directlinks_history_file, self.directlinks_history)
            
            self.log(f"Directlinks snapshot taken: {len(self.directlinks_history)} total ({len(nodes_list)} nodes, {len(all_links)} links)")
            
        except Exception as e:
            self.log(f"Error taking directlinks snapshot: {e}", level="ERROR")
