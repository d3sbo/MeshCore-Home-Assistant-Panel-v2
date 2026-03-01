import appdaemon.plugins.hass.hassapi as hass
import time
import json
import re
import os
from datetime import datetime

class MeshCoreHops(hass.Hass):

    def initialize(self):
        self.log("MeshCoreHops initialized")
        
        # Persistence files
        self.persistence_file = "/homeassistant/www/meshcore_last_messages.json"
        self.sensors_persistence_file = "/homeassistant/www/meshcore_hops_sensors.json"
        
        # Cache for correlating RX_LOG_DATA with subsequent message events
        # Now stores LIST of receptions per message
        self.rx_log_cache = {}
        self.rx_log_cache_timeout = 10  # seconds (increased to catch all receptions)
        
        # Cache for sender name -> pubkey mapping
        self.name_to_pubkey_cache = {}
        self.rebuild_name_cache()
        
        # Cache for last message times (pubkey -> timestamp)
        self.last_message_times = {}
        
        # Cache for full hops sensor data (sensor_id -> attributes)
        self.hops_sensors_data = {}
        
        self.load_persisted_data()
        
        # Listen for raw meshcore events
        self.listen_event(self.handle_raw_event, "meshcore_raw_event")
        
        # Listen for contact sensor changes (for advertisement-based SNR/RSSI and cache updates)
        self.listen_state(self.handle_contact_update, "binary_sensor", attribute="all")
        
        # Periodic persistence save
        self.run_every(self.save_persisted_data, "now+120", 300)  # Every 5 minutes
        
        # Restore sensors and last message data
        self.run_in(self.restore_hops_sensors, 10)
        self.run_in(self.restore_last_messages, 15)
    
    def load_persisted_data(self):
        """Load last message times and hops sensors from JSON files"""
        try:
            # Load last message times
            if os.path.exists(self.persistence_file):
                with open(self.persistence_file, 'r') as f:
                    data = json.load(f)
                    self.last_message_times = data.get("last_messages", {})
                    self.log(f"Loaded {len(self.last_message_times)} last message times from persistence")
            else:
                self.log("No persistence file found for last messages")
            
            # Load hops sensors data
            if os.path.exists(self.sensors_persistence_file):
                with open(self.sensors_persistence_file, 'r') as f:
                    data = json.load(f)
                    self.hops_sensors_data = data.get("sensors", {})
                    self.log(f"Loaded {len(self.hops_sensors_data)} hops sensors from persistence")
            else:
                self.log("No persistence file found for hops sensors")
                
        except Exception as e:
            self.log(f"Error loading persisted data: {e}", level="WARNING")
            self.last_message_times = {}
            self.hops_sensors_data = {}
    
    def save_persisted_data(self, kwargs=None):
        """Save last message times and hops sensors to JSON files"""
        try:
            now_ts = time.time()
            max_age_sec = 7 * 24 * 3600  # 7 days
            
            # Clean old last message times
            cleaned_messages = {}
            for pubkey, timestamp in self.last_message_times.items():
                if (now_ts - timestamp) <= max_age_sec:
                    cleaned_messages[pubkey] = timestamp
            
            removed_messages = len(self.last_message_times) - len(cleaned_messages)
            self.last_message_times = cleaned_messages
            
            # Clean old hops sensors
            cleaned_sensors = {}
            for sensor_id, sensor_data in self.hops_sensors_data.items():
                attrs = sensor_data.get("attributes", {})
                last_msg_time = attrs.get("last_message_time") or attrs.get("last_seen", 0)
                if last_msg_time and (now_ts - last_msg_time) <= max_age_sec:
                    cleaned_sensors[sensor_id] = sensor_data
            
            removed_sensors = len(self.hops_sensors_data) - len(cleaned_sensors)
            self.hops_sensors_data = cleaned_sensors
            
            if removed_messages > 0 or removed_sensors > 0:
                self.log(f"Cleaned up {removed_messages} old messages, {removed_sensors} old sensors (>7 days)")
            
            # Save last message times
            data = {
                "last_messages": self.last_message_times,
                "count": len(self.last_message_times),
                "saved_at": time.time(),
                "saved_at_formatted": datetime.now().isoformat()
            }
            with open(self.persistence_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            # Save hops sensors data
            sensors_data = {
                "sensors": self.hops_sensors_data,
                "count": len(self.hops_sensors_data),
                "saved_at": time.time(),
                "saved_at_formatted": datetime.now().isoformat()
            }
            with open(self.sensors_persistence_file, 'w') as f:
                json.dump(sensors_data, f, indent=2)
            
            self.log(f"Saved {len(self.last_message_times)} last messages, {len(self.hops_sensors_data)} hops sensors")
        except Exception as e:
            self.log(f"Error saving persisted data: {e}", level="ERROR")
    
    def restore_hops_sensors(self, kwargs=None):
        """Restore hops sensors from persisted data"""
        try:
            if not self.hops_sensors_data:
                self.log("No hops sensors to restore")
                return
            
            restored = 0
            for sensor_id, sensor_data in self.hops_sensors_data.items():
                state = sensor_data.get("state", "0")
                attrs = sensor_data.get("attributes", {})
                
                if attrs:
                    self.set_state(sensor_id, state=str(state), attributes=attrs)
                    restored += 1
            
            self.log(f"Restored {restored} hops sensors")
            
        except Exception as e:
            self.log(f"Error restoring hops sensors: {e}", level="ERROR")
    
    def track_hops_sensor(self, sensor_id, state, attributes):
        """Track hops sensor data for persistence"""
        self.hops_sensors_data[sensor_id] = {
            "state": state,
            "attributes": attributes
        }
    
    def restore_last_messages(self, kwargs=None):
        """Restore last_message attribute to contact sensors from persisted data"""
        try:
            if not self.last_message_times:
                self.log("No last message times to restore")
                return
            
            all_states = self.get_state()
            restored = 0
            
            for entity_id, state_data in all_states.items():
                if not (entity_id.startswith("binary_sensor.meshcore_") and "_contact" in entity_id):
                    continue
                
                attrs = state_data.get("attributes", {}) if state_data else {}
                pubkey = attrs.get("pubkey_prefix")
                
                if pubkey and pubkey in self.last_message_times:
                    last_msg_time = self.last_message_times[pubkey]
                    
                    # Update the contact sensor with last_message
                    current_state = state_data.get("state", "unknown")
                    new_attrs = dict(attrs)
                    new_attrs["last_message"] = last_msg_time
                    new_attrs["last_message_formatted"] = datetime.fromtimestamp(last_msg_time).isoformat()
                    
                    self.set_state(entity_id, state=current_state, attributes=new_attrs)
                    restored += 1
            
            self.log(f"Restored last_message to {restored} contact sensors")
            
        except Exception as e:
            self.log(f"Error restoring last messages: {e}", level="ERROR")
    
    def track_last_message(self, pubkey, timestamp):
        """Track last message time for a pubkey"""
        if pubkey:
            self.last_message_times[pubkey] = timestamp
            # Save periodically (every 5 new messages)
            if len(self.last_message_times) % 5 == 0:
                self.save_persisted_data()
    
    def rebuild_name_cache(self):
        """Build cache of sender names to pubkey_prefix"""
        try:
            all_states = self.get_state()
            for ent_id in all_states.keys():
                if ent_id.startswith("binary_sensor.meshcore_") and "_contact" in ent_id:
                    attrs = all_states[ent_id].get("attributes", {})
                    pubkey = attrs.get("pubkey_prefix")
                    name = attrs.get("name") or attrs.get("friendly_name", "")
                    if pubkey and name:
                        # Clean name - remove " Contact" suffix and node type suffixes
                        clean_name = name.replace(" Contact", "").strip()
                        # Also remove node type suffixes like (Client), (Repeater), (Room Server)
                        clean_name = re.sub(r'\s*\((Client|Repeater|Room Server|Room|Server)\)\s*$', '', clean_name, flags=re.IGNORECASE).strip()
                        
                        # Store multiple variations
                        self.name_to_pubkey_cache[clean_name] = pubkey
                        self.name_to_pubkey_cache[clean_name.lower()] = pubkey
                        # Also store the original name in case messages include the suffix
                        original_clean = name.replace(" Contact", "").strip()
                        self.name_to_pubkey_cache[original_clean] = pubkey
                        self.name_to_pubkey_cache[original_clean.lower()] = pubkey
            self.log(f"Name cache built with {len(self.name_to_pubkey_cache)} entries")
        except Exception as e:
            self.log(f"Error building name cache: {e}", level="WARNING")
    
    def get_pubkey_for_sender(self, sender_name):
        """Look up pubkey_prefix for a sender name"""
        # Try exact match first
        if sender_name in self.name_to_pubkey_cache:
            return self.name_to_pubkey_cache[sender_name]
        
        # Try lowercase
        if sender_name.lower() in self.name_to_pubkey_cache:
            return self.name_to_pubkey_cache[sender_name.lower()]
        
        # Try partial match (for names with emojis that might be stripped)
        clean_sender = self.sanitize_entity_name(sender_name)
        for name, pubkey in self.name_to_pubkey_cache.items():
            if self.sanitize_entity_name(name) == clean_sender:
                # Cache this mapping for next time
                self.name_to_pubkey_cache[sender_name] = pubkey
                return pubkey
        
        # Log what we're looking for vs what's in cache (sample)
        similar_names = [n for n in self.name_to_pubkey_cache.keys() 
                        if 'smlf' in n.lower() or 'portable' in n.lower()]
        if similar_names:
            self.log(f"Looking for '{sender_name}', found similar: {similar_names[:5]}", level="WARNING")
        else:
            self.log(f"Looking for '{sender_name}' (sanitized: {clean_sender}), no similar names found", level="WARNING")
        
        # Only rebuild cache if we haven't found it and cache might be stale
        # Check if cache was built recently (within last 60 seconds)
        if not hasattr(self, '_last_cache_rebuild') or (time.time() - self._last_cache_rebuild) > 60:
            self.rebuild_name_cache()
            self._last_cache_rebuild = time.time()
            
            # Try again after rebuild
            if sender_name in self.name_to_pubkey_cache:
                return self.name_to_pubkey_cache[sender_name]
            if sender_name.lower() in self.name_to_pubkey_cache:
                return self.name_to_pubkey_cache[sender_name.lower()]
        
        return None

    def handle_raw_event(self, event_name, data, kwargs):
        """Handle raw meshcore events"""
        try:
            event_type = data.get("event_type", "")
            payload = data.get("payload", {})
            
            # Only log interesting events (not battery/no_more_msgs/rx_log spam)
            if event_type not in ["EventType.BATTERY", "EventType.NO_MORE_MSGS", 
                                   "EventType.OK", "EventType.MESSAGES_WAITING",
                                   "EventType.RX_LOG_DATA"]:
                self.log(f"Raw event: {event_type}")
            
            # Handle RX_LOG_DATA - has the best signal data (SNR + RSSI)
            if event_type == "EventType.RX_LOG_DATA":
                self.process_rx_log_data(payload)
            
            # Handle direct messages
            elif event_type == "EventType.CONTACT_MSG_RECV":
                self.process_direct_message(payload)
            
            # Handle channel messages
            elif event_type == "EventType.CHANNEL_MSG_RECV":
                self.process_channel_message(payload)
            
            # Handle advertisements
            elif event_type == "EventType.ADVERTISEMENT":
                self.process_advertisement(payload)
                
        except Exception as e:
            self.log(f"Error handling raw event: {e}", level="ERROR")
            import traceback
            self.log(traceback.format_exc(), level="ERROR")

    def process_rx_log_data(self, payload):
        """
        Process RX_LOG_DATA events - these have the richest signal data.
        Collect ALL receptions for a message to show all paths it took.
        """
        try:
            snr = payload.get("snr")
            rssi = payload.get("rssi")
            parsed = payload.get("parsed", {})
            decrypted = payload.get("decrypted", {})
            
            path_len = parsed.get("path_len", 0)
            path = parsed.get("path", "")
            path_nodes = parsed.get("path_nodes", [])
            
            # Get message info from decrypted section
            channel_idx = decrypted.get("channel_idx")
            text = decrypted.get("text", "")
            timestamp = decrypted.get("timestamp")
            
            # Skip if we couldn't decrypt (no text means wrong channel/key)
            if not text and not decrypted.get("decrypted"):
                self.log(f"RX_LOG: Undecrypted packet, SNR: {snr}, RSSI: {rssi}", level="DEBUG")
                return
            
            # Extract sender name from "Name: Message" format
            sender_name = "Unknown"
            message_text = text
            if ": " in text:
                parts = text.split(": ", 1)
                sender_name = parts[0]
                message_text = parts[1] if len(parts) > 1 else ""
            
            # Create a unique key for this message (same message = same key regardless of path)
            cache_key = f"{channel_idx}_{timestamp}_{sender_name}"
            
            # Create reception record
            reception = {
                "hops": path_len,
                "snr": snr,
                "rssi": rssi,
                "path": path,
                "path_nodes": path_nodes,
                "received_at": time.time()
            }
            
            # Add to cache - accumulate all receptions for this message
            if cache_key not in self.rx_log_cache:
                self.rx_log_cache[cache_key] = {
                    "sender_name": sender_name,
                    "channel_idx": channel_idx,
                    "text": text,
                    "message_text": message_text,
                    "timestamp": timestamp,
                    "receptions": [],
                    "first_seen": time.time()
                }
            
            # Add this reception to the list
            self.rx_log_cache[cache_key]["receptions"].append(reception)
            
            # Clean old cache entries
            self.clean_rx_log_cache()
            
            # Update sensor with ALL receptions so far
            self.update_sensor_from_cache(cache_key)
            
            path_str = ' → '.join(path_nodes) if path_nodes else 'direct'
            self.log(f"RX_LOG: {sender_name} - {path_len} hops, SNR: {snr}, RSSI: {rssi}, path: {path_str}")
                
        except Exception as e:
            self.log(f"Error processing RX_LOG_DATA: {e}", level="ERROR")
            import traceback
            self.log(traceback.format_exc(), level="ERROR")

    def update_sensor_from_cache(self, cache_key):
        """Update sensor with all reception data from cache"""
        try:
            cache_data = self.rx_log_cache.get(cache_key)
            if not cache_data:
                return
            
            sender_name = cache_data["sender_name"]
            channel_idx = cache_data["channel_idx"]
            text = cache_data["text"]
            message_text = cache_data["message_text"]
            receptions = cache_data["receptions"]
            
            if not receptions:
                return
            
            # Find best reception (direct preferred, then highest SNR)
            best = None
            for r in receptions:
                if best is None:
                    best = r
                elif r["hops"] == 0 and best["hops"] > 0:
                    # Prefer direct
                    best = r
                elif r["hops"] == best["hops"] and (r["snr"] or 0) > (best["snr"] or 0):
                    # Same hops, prefer higher SNR
                    best = r
            
            # Also find the longest path (most interesting for mesh visualization)
            longest_path = max(receptions, key=lambda r: r["hops"])
            
            current_ts = time.time()
            
            # Try to get pubkey for this sender (needed for card compatibility)
            pubkey = self.get_pubkey_for_sender(sender_name)
            
            # Determine sensor ID - prefer pubkey, fallback to sanitized name
            if pubkey:
                sensor_id = f"sensor.meshcore_hops_{pubkey}"
                self.log(f"Found pubkey {pubkey} for sender {sender_name}")
            else:
                safe_sender = self.sanitize_entity_name(sender_name)
                sensor_id = f"sensor.meshcore_hops_{safe_sender}"
                self.log(f"No pubkey found for {sender_name}, using name-based sensor ID: {sensor_id}", level="WARNING")
            
            # Format receptions for attribute storage
            receptions_formatted = []
            for r in receptions:
                receptions_formatted.append({
                    "hops": r["hops"],
                    "snr": r["snr"],
                    "rssi": r["rssi"],
                    "path": ' → '.join(r["path_nodes"]) if r["path_nodes"] else "direct"
                })
            
            # Get location from contact sensor
            location = self.get_contact_location(pubkey_prefix=pubkey, sender_name=sender_name)
            
            # Build attributes dict
            sensor_attrs = {
                "friendly_name": f"{sender_name} Hops",
                "sender_name": sender_name,
                "pubkey_prefix": pubkey if pubkey else "",
                "message_type": "channel",
                "channel_idx": channel_idx,
                # Location data
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                # Best reception data
                "path_length": best["hops"],
                "snr": best["snr"] if best["snr"] is not None else 0,
                "rssi": best["rssi"] if best["rssi"] is not None else 0,
                # Use longest_path for path_nodes (for map visualization)
                "path_nodes": longest_path["path_nodes"],
                # Longest path data (for mesh visualization)
                "max_hops": longest_path["hops"],
                "longest_path": ' → '.join(longest_path["path_nodes"]) if longest_path["path_nodes"] else "direct",
                # All receptions
                "receptions": receptions_formatted,
                "reception_count": len(receptions),
                # Message info
                "last_message_text": message_text[:100] if message_text else "",
                "last_message_time": current_ts,
                "last_message_formatted": datetime.fromtimestamp(current_ts).isoformat(),
                "icon": "mdi:routes",
                "unit_of_measurement": "hops",
                "data_source": "rx_log_data"
            }
            
            self.set_state(
                sensor_id,
                state=str(best["hops"]),  # State is best/direct hop count
                attributes=sensor_attrs
            )
            
            # Track for persistence
            self.track_hops_sensor(sensor_id, best["hops"], sensor_attrs)
            
            # Also update the contact sensor's last_message attribute if we have pubkey
            if pubkey:
                self.update_contact_last_message(pubkey, current_ts)
                
        except Exception as e:
            self.log(f"Error updating sensor from cache: {e}", level="ERROR")

    def clean_rx_log_cache(self):
        """Remove old entries from rx_log cache"""
        now = time.time()
        expired = [k for k, v in self.rx_log_cache.items() 
                   if now - v.get("first_seen", 0) > self.rx_log_cache_timeout]
        for k in expired:
            del self.rx_log_cache[k]

    def sanitize_entity_name(self, name):
        """Convert a name to a valid entity_id component - ASCII only"""
        import unicodedata
        # First normalize accented chars to ASCII equivalents (é -> e)
        normalized = unicodedata.normalize('NFKD', name)
        # Keep only ASCII alphanumeric and spaces
        sanitized = ''.join(c if (c.isalnum() and ord(c) < 128) or c == ' ' else '' for c in normalized)
        sanitized = re.sub(r'\s+', '_', sanitized.strip())
        sanitized = sanitized.lower()
        # Remove any leading/trailing underscores
        sanitized = sanitized.strip('_')
        # Remove consecutive underscores
        sanitized = re.sub(r'_+', '_', sanitized)
        # Ensure it's not empty
        if not sanitized:
            sanitized = "unknown"
        return sanitized

    def process_direct_message(self, payload):
        """Process direct message events (EventType.CONTACT_MSG_RECV)"""
        try:
            snr = payload.get("SNR")
            rssi = payload.get("RSSI")  # May not be present
            pubkey_prefix = payload.get("pubkey_prefix", "")
            text = payload.get("text", "")
            sender_timestamp = payload.get("sender_timestamp")
            path_len = payload.get("path_len", 0)
            
            if not pubkey_prefix:
                self.log("No pubkey_prefix in direct message", level="DEBUG")
                return
            
            # Get sender name from contact lookup
            sender_name = self.get_contact_name(pubkey_prefix)
            
            # Try to get RSSI from cached RX_LOG_DATA
            cached_rssi = None
            for cache_key, cache_data in self.rx_log_cache.items():
                if cache_data.get("text", "")[:20] == text[:20]:
                    cached_rssi = cache_data.get("rssi")
                    if cached_rssi:
                        rssi = cached_rssi
                    break
            
            current_ts = time.time()
            sensor_id = f"sensor.meshcore_hops_{pubkey_prefix}"
            
            # Get location from contact sensor
            location = self.get_contact_location(pubkey_prefix=pubkey_prefix)
            
            self.set_state(
                sensor_id,
                state=str(path_len) if path_len is not None else "0",
                attributes={
                    "friendly_name": f"{sender_name} Hops",
                    "sender_name": sender_name,
                    "pubkey_prefix": pubkey_prefix,
                    "message_type": "direct",
                    "latitude": location["latitude"],
                    "longitude": location["longitude"],
                    "path_length": path_len if path_len is not None else 0,
                    "snr": snr if snr is not None else 0,
                    "rssi": rssi if rssi is not None else 0,
                    "last_message_text": text[:100] if text else "",
                    "last_message_time": current_ts,
                    "last_message_formatted": datetime.fromtimestamp(current_ts).isoformat(),
                    "icon": "mdi:routes",
                    "unit_of_measurement": "hops",
                    "data_source": "direct_message"
                }
            )
            
            # Update contact sensor with last_message timestamp
            self.update_contact_last_message(pubkey_prefix, current_ts)
            
            self.log(f"DM from {sender_name}: {path_len} hops, SNR: {snr}, RSSI: {rssi}")
                
        except Exception as e:
            self.log(f"Error processing direct message: {e}", level="ERROR")
            import traceback
            self.log(traceback.format_exc(), level="ERROR")

    def process_channel_message(self, payload):
        """Process channel message events (EventType.CHANNEL_MSG_RECV)"""
        try:
            snr = payload.get("SNR")
            channel_idx = payload.get("channel_idx", 0)
            text = payload.get("text", "")
            sender_timestamp = payload.get("sender_timestamp")
            path_len = payload.get("path_len", 0)
            
            # Extract sender name from "Name: Message" format
            sender_name = "Unknown"
            message_text = text
            if ": " in text:
                parts = text.split(": ", 1)
                sender_name = parts[0]
                message_text = parts[1] if len(parts) > 1 else ""
            
            # Try to get data from cached RX_LOG_DATA
            cache_key = f"{channel_idx}_{sender_timestamp}_{sender_name}"
            cache_data = self.rx_log_cache.get(cache_key)
            
            if cache_data and cache_data.get("receptions"):
                # Already handled by RX_LOG_DATA with full reception data
                # Just log that we received the message event
                receptions = cache_data["receptions"]
                self.log(f"Channel {channel_idx} from {sender_name}: {len(receptions)} receptions captured")
                return
            
            # No cached data - create sensor from MSG_RECV data alone (less detailed)
            current_ts = time.time()
            
            # Try to get pubkey for this sender
            pubkey = self.get_pubkey_for_sender(sender_name)
            
            if pubkey:
                sensor_id = f"sensor.meshcore_hops_{pubkey}"
            else:
                safe_sender = self.sanitize_entity_name(sender_name)
                sensor_id = f"sensor.meshcore_hops_{safe_sender}"
            
            # Get location from contact sensor
            location = self.get_contact_location(pubkey_prefix=pubkey, sender_name=sender_name)
            
            sensor_attrs = {
                "friendly_name": f"{sender_name} Hops",
                "sender_name": sender_name,
                "pubkey_prefix": pubkey if pubkey else "",
                "message_type": "channel",
                "channel_idx": channel_idx,
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "path_length": path_len,
                "snr": snr if snr is not None else 0,
                "rssi": 0,  # Not available in CHANNEL_MSG_RECV
                "receptions": [{
                    "hops": path_len,
                    "snr": snr,
                    "rssi": None,
                    "path": "unknown"
                }],
                "reception_count": 1,
                "last_message_text": message_text[:100] if message_text else "",
                "last_message_time": current_ts,
                "last_message_formatted": datetime.fromtimestamp(current_ts).isoformat(),
                "icon": "mdi:routes",
                "unit_of_measurement": "hops",
                "data_source": "channel_message"
            }
            
            self.set_state(
                sensor_id,
                state=str(path_len),
                attributes=sensor_attrs
            )
            
            # Track for persistence
            self.track_hops_sensor(sensor_id, path_len, sensor_attrs)
            
            # Update contact sensor if we have pubkey
            if pubkey:
                self.update_contact_last_message(pubkey, current_ts)
            
            self.log(f"Channel {channel_idx} from {sender_name}: {path_len} hops, SNR: {snr} (no RX_LOG data)")
                
        except Exception as e:
            self.log(f"Error processing channel message: {e}", level="ERROR")
            import traceback
            self.log(traceback.format_exc(), level="ERROR")

    def process_advertisement(self, payload):
        """Process advertisement events for SNR/RSSI tracking"""
        try:
            snr = payload.get("SNR") or payload.get("snr")
            rssi = payload.get("RSSI") or payload.get("rssi")
            pubkey_prefix = payload.get("pubkey_prefix", "")
            
            if not pubkey_prefix:
                return
            
            sender_name = self.get_contact_name(pubkey_prefix)
            current_ts = time.time()
            
            sensor_id = f"sensor.meshcore_hops_{pubkey_prefix}"
            
            # Check if sensor already exists with message data
            existing_state = self.get_state(sensor_id, attribute="all")
            
            # Don't overwrite message data with advertisement data
            if existing_state:
                existing_attrs = existing_state.get("attributes", {})
                if existing_attrs.get("data_source") in ["direct_message", "channel_message", "rx_log_data"]:
                    return
            
            sensor_attrs = {
                "friendly_name": f"{sender_name} Signal",
                "sender_name": sender_name,
                "pubkey_prefix": pubkey_prefix,
                "path_length": 0,
                "snr": snr if snr is not None else 0,
                "rssi": rssi if rssi is not None else 0,
                "last_seen": current_ts,
                "last_seen_formatted": datetime.fromtimestamp(current_ts).isoformat(),
                "icon": "mdi:signal",
                "unit_of_measurement": "hops",
                "data_source": "advertisement"
            }
            
            self.set_state(
                sensor_id,
                state="0",
                attributes=sensor_attrs
            )
            
            # Track for persistence (only advertisement data, lower priority)
            self.track_hops_sensor(sensor_id, 0, sensor_attrs)
            
            self.log(f"Advert from {sender_name}: SNR: {snr}, RSSI: {rssi}")
                
        except Exception as e:
            self.log(f"Error processing advertisement: {e}", level="ERROR")

    def get_contact_name(self, pubkey_prefix):
        """Look up contact name from pubkey_prefix"""
        try:
            all_states = self.get_state()
            
            for ent_id in all_states.keys():
                if ent_id.startswith("binary_sensor.meshcore_") and "_contact" in ent_id:
                    ent_attrs = all_states[ent_id].get("attributes", {})
                    if ent_attrs.get("pubkey_prefix") == pubkey_prefix:
                        name = ent_attrs.get("name") or ent_attrs.get("friendly_name", "")
                        if name:
                            name = name.replace(" Contact", "").strip()
                            return name
            
            return f"Unknown ({pubkey_prefix[:8]})"
            
        except Exception as e:
            self.log(f"Error looking up contact name: {e}", level="WARNING")
            return f"Unknown ({pubkey_prefix[:8] if pubkey_prefix else 'N/A'})"

    def get_contact_location(self, pubkey_prefix=None, sender_name=None):
        """Look up contact location (lat/lon) from pubkey_prefix or sender_name"""
        try:
            all_states = self.get_state()
            
            for ent_id in all_states.keys():
                if ent_id.startswith("binary_sensor.meshcore_") and "_contact" in ent_id:
                    ent_attrs = all_states[ent_id].get("attributes", {})
                    
                    # Match by pubkey if provided
                    if pubkey_prefix and ent_attrs.get("pubkey_prefix") == pubkey_prefix:
                        lat = ent_attrs.get("adv_lat") or ent_attrs.get("latitude")
                        lon = ent_attrs.get("adv_lon") or ent_attrs.get("longitude")
                        if lat is not None and lon is not None:
                            return {"latitude": lat, "longitude": lon}
                    
                    # Match by name if no pubkey
                    if sender_name and not pubkey_prefix:
                        contact_name = ent_attrs.get("adv_name") or ent_attrs.get("friendly_name", "")
                        contact_name = contact_name.replace(" Contact", "").replace(" (Client)", "").replace(" (Repeater)", "").replace(" (Room Server)", "").strip()
                        if contact_name == sender_name or sender_name in contact_name:
                            lat = ent_attrs.get("adv_lat") or ent_attrs.get("latitude")
                            lon = ent_attrs.get("adv_lon") or ent_attrs.get("longitude")
                            if lat is not None and lon is not None:
                                return {"latitude": lat, "longitude": lon}
            
            return {"latitude": None, "longitude": None}
            
        except Exception as e:
            self.log(f"Error looking up contact location: {e}", level="WARNING")
            return {"latitude": None, "longitude": None}

    def update_contact_last_message(self, pubkey_prefix, timestamp):
        """Update contact sensor with last message timestamp"""
        try:
            all_states = self.get_state()
            contact_sensor = None
            
            for ent_id in all_states.keys():
                if ent_id.startswith("binary_sensor.meshcore_") and "_contact" in ent_id:
                    ent_attrs = all_states[ent_id].get("attributes", {})
                    if ent_attrs.get("pubkey_prefix") == pubkey_prefix:
                        contact_sensor = ent_id
                        break
            
            if not contact_sensor:
                self.log(f"No contact sensor found for pubkey {pubkey_prefix}", level="WARNING")
                return
            
            # Get current state and ALL attributes
            current_state = self.get_state(contact_sensor)
            contact_state = self.get_state(contact_sensor, attribute="all")
            
            if contact_state:
                attrs = dict(contact_state.get("attributes", {}))
            else:
                attrs = {}
            
            # Add our new attributes
            attrs["last_message"] = timestamp
            attrs["last_message_formatted"] = datetime.fromtimestamp(timestamp).isoformat()
            
            # Track for persistence
            self.track_last_message(pubkey_prefix, timestamp)
            
            # Set state with ALL attributes preserved, including the current state value
            self.set_state(contact_sensor, state=current_state, attributes=attrs)
            self.log(f"Updated {contact_sensor} with last_message: {timestamp}")
                
        except Exception as e:
            self.log(f"Error updating contact last_message: {e}", level="WARNING")
            import traceback
            self.log(traceback.format_exc(), level="WARNING")

    def handle_contact_update(self, entity, attribute, old, new, kwargs):
        """Handle contact sensor updates - track SNR/RSSI from advertisements and update name cache"""
        try:
            if not entity.startswith("binary_sensor.meshcore_") or "_contact" not in entity:
                return
            
            if not new or "attributes" not in new:
                return
                
            attrs = new.get("attributes", {})
            pubkey_prefix = attrs.get("pubkey_prefix")
            if not pubkey_prefix:
                return
            
            # Update name cache with this contact
            name = attrs.get("name") or attrs.get("friendly_name", "")
            if name:
                clean_name = name.replace(" Contact", "").strip()
                self.name_to_pubkey_cache[clean_name] = pubkey_prefix
                self.name_to_pubkey_cache[clean_name.lower()] = pubkey_prefix
            
            last_snr = attrs.get("last_snr")
            last_rssi = attrs.get("last_rssi")
            sender_name = attrs.get("friendly_name", "Unknown").replace(" Contact", "").strip()
            
            if last_snr is None and last_rssi is None:
                return
            
            sensor_id = f"sensor.meshcore_hops_{pubkey_prefix}"
            
            existing_state = self.get_state(sensor_id, attribute="all")
            if existing_state:
                existing_attrs = existing_state.get("attributes", {})
                if existing_attrs.get("data_source") in ["direct_message", "channel_message", "rx_log_data"]:
                    return
            
            current_ts = time.time()
            
            self.set_state(
                sensor_id,
                state="0",
                attributes={
                    "friendly_name": f"{sender_name} Signal",
                    "sender_name": sender_name,
                    "pubkey_prefix": pubkey_prefix,
                    "entity_id": entity,
                    "path_length": 0,
                    "snr": last_snr if last_snr is not None else 0,
                    "rssi": last_rssi if last_rssi is not None else 0,
                    "last_seen": current_ts,
                    "last_seen_formatted": datetime.fromtimestamp(current_ts).isoformat(),
                    "icon": "mdi:signal",
                    "unit_of_measurement": "hops",
                    "data_source": "contact_sensor"
                }
            )
                
        except Exception as e:
            self.log(f"Error handling contact update: {e}", level="ERROR")
