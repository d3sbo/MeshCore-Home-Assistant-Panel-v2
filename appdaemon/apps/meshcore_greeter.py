import appdaemon.plugins.hass.hassapi as hass
import json
import os
import time
from datetime import datetime

class MeshCoreGreeter(hass.Hass):
    """
    Greets new companions/clients on the Public channel when first detected.
    - Only greets clients/companions (not repeaters)
    - Only greets if within 5 hops
    - Only greets once per device (persisted)
    """

    def initialize(self):
        self.log("MeshCoreGreeter initialized")
        
        # Persistence file for tracking greeted contacts
        self.greeted_file = "/homeassistant/www/meshcore_greeted.json"
        
        # Load already greeted contacts
        self.greeted_pubkeys = set()
        self.load_greeted()
        
        # Max hops to greet
        self.max_hops = self.args.get("hops_distant", 5)
        
        # Channel to greet on (0 = Public)
        self.greet_channel = 0
        
        # Your name for the greeting
        self.my_name = self.args.get("my_name", "MyRepeater")
        
        # Listen for new contact sensors being created
        self.listen_state(self.handle_contact_change, "binary_sensor")
        
        # Also listen for meshcore events for first advertisement
        self.listen_event(self.handle_new_contact_event, "meshcore_raw_event")
        
        # Listen for test greeting event
        self.listen_event(self.handle_test_event, "meshcore_greeter_test")
        
        self.log(f"Loaded {len(self.greeted_pubkeys)} previously greeted contacts")
        self.log(f"Greeter name: {self.my_name}, max hops: {self.max_hops}")
    
    def handle_test_event(self, event_name, data, kwargs):
        """Handle test greeting event"""
        self.log("Test greeting event received")
        self.test_greeting()
    
    def load_greeted(self):
        """Load list of already greeted pubkeys"""
        try:
            if os.path.exists(self.greeted_file):
                with open(self.greeted_file, 'r') as f:
                    data = json.load(f)
                    self.greeted_pubkeys = set(data.get("greeted", []))
        except Exception as e:
            self.log(f"Error loading greeted list: {e}", level="WARNING")
            self.greeted_pubkeys = set()
    
    def save_greeted(self):
        """Save list of greeted pubkeys"""
        try:
            data = {
                "greeted": list(self.greeted_pubkeys),
                "count": len(self.greeted_pubkeys),
                "last_updated": datetime.now().isoformat()
            }
            with open(self.greeted_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.log(f"Error saving greeted list: {e}", level="ERROR")
    
    def handle_new_contact_event(self, event_name, data, kwargs):
        """Handle meshcore_raw_event for NEW_CONTACT events"""
        try:
            event_type = data.get("event_type", "")
            
            if event_type != "EventType.NEW_CONTACT":
                return
            
            payload = data.get("payload", {})
            pubkey = payload.get("public_key", "")[:12]  # First 12 chars
            name = payload.get("adv_name", "Unknown")
            node_type = payload.get("type", 0)
            
            # Node types: 1=Client, 2=Repeater, 3=Room Server
            if node_type == 2:  # Repeater
                self.log(f"New repeater detected: {name} - not greeting")
                return
            
            if not pubkey:
                return
            
            # Check if already greeted
            if pubkey in self.greeted_pubkeys:
                self.log(f"Already greeted {name} ({pubkey})")
                return
            
            # For NEW_CONTACT, we don't have hop info yet
            # Mark as pending and wait for first message/advert with hop data
            self.log(f"New contact detected: {name} ({pubkey}), type={node_type} - waiting for hop data")
            
        except Exception as e:
            self.log(f"Error handling new contact event: {e}", level="ERROR")
    
    def handle_contact_change(self, entity, attribute, old, new, kwargs):
        """Handle contact sensor state changes"""
        try:
            # Only process meshcore contact sensors
            if not entity.startswith("binary_sensor.meshcore_") or "_contact" not in entity:
                return
            
            # Skip if no new state
            if not new:
                return
            
            attrs = new.get("attributes", {}) if isinstance(new, dict) else {}
            if not attrs:
                # Try getting attributes directly
                attrs = self.get_state(entity, attribute="all")
                if attrs:
                    attrs = attrs.get("attributes", {})
            
            if not attrs:
                return
            
            pubkey = attrs.get("pubkey_prefix", "")
            name = attrs.get("adv_name") or attrs.get("friendly_name", "").replace(" Contact", "")
            node_type_str = attrs.get("node_type_str", "").lower()
            
            # Only greet clients/companions, not repeaters or room servers
            if "repeater" in node_type_str or "room" in node_type_str or "server" in node_type_str:
                return
            
            if not pubkey or not name:
                return
            
            # Check if already greeted
            if pubkey in self.greeted_pubkeys:
                return
            
            # Check hop count from hops sensor if available
            hops = self.get_hop_count(pubkey, name)
            
            if hops is None:
                # No hop data yet - might be first detection
                # Check if this is a state change to "fresh" (newly active)
                current_state = new.get("state") if isinstance(new, dict) else new
                if current_state == "fresh":
                    # Assume within range if fresh, greet with unknown hops
                    self.log(f"New fresh contact {name} ({pubkey}) - no hop data, greeting anyway")
                    self.send_greeting(name, pubkey, "unknown")
                return
            
            if hops > self.max_hops:
                self.log(f"Contact {name} is {hops} hops away (>{self.max_hops}), not greeting")
                return
            
            # All checks passed - send greeting!
            self.send_greeting(name, pubkey, hops)
            
        except Exception as e:
            self.log(f"Error handling contact change: {e}", level="ERROR")
            import traceback
            self.log(traceback.format_exc(), level="ERROR")
    
    def get_hop_count(self, pubkey, name):
        """Get hop count for a contact from hops sensor"""
        try:
            # Try pubkey-based sensor first
            sensor_id = f"sensor.meshcore_hops_{pubkey}"
            state = self.get_state(sensor_id)
            
            if state and state not in ["unknown", "unavailable", None]:
                try:
                    return int(state)
                except ValueError:
                    pass
            
            # Try name-based sensor
            import re
            safe_name = "".join(c if c.isalnum() or c == " " else "" for c in name.lower())
            safe_name = re.sub(r'\s+', '_', safe_name.strip())
            safe_name = re.sub(r'_+', '_', safe_name)
            safe_name = safe_name.strip('_')
            
            sensor_id = f"sensor.meshcore_hops_{safe_name}"
            state = self.get_state(sensor_id)
            
            if state and state not in ["unknown", "unavailable", None]:
                try:
                    return int(state)
                except ValueError:
                    pass
            
            return None
            
        except Exception as e:
            self.log(f"Error getting hop count: {e}", level="WARNING")
            return None
    
    def send_greeting(self, name, pubkey, hops):
        """Send welcome message on Public channel and notify HA"""
        try:
            # Mark as greeted first (prevent double-greet)
            self.greeted_pubkeys.add(pubkey)
            self.save_greeted()
            
            # Compose greeting message
            message = f"Welcome to the mesh {name}! 👋 from {self.my_name}"
            
            # Send via MeshCore service
            self.call_service(
                "meshcore/send_channel_message",
                channel_idx=self.greet_channel,
                message=message
            )
            
            self.log(f"Greeted {name} ({pubkey}) - {hops} hops - on channel {self.greet_channel}")
            
            # Send HA notification
            self.call_service(
                "notify/persistent_notification",
                title="🆕 New MeshCore Contact",
                message=f"**{name}**\nPubkey: {pubkey}\nHops: {hops}\n\nGreeted on Public channel"
            )
            
        except Exception as e:
            self.log(f"Error sending greeting: {e}", level="ERROR")
            # Remove from greeted if send failed
            self.greeted_pubkeys.discard(pubkey)
            self.save_greeted()

    def test_greeting(self, kwargs=None):
        """Send a test greeting to verify the greeter is working"""
        try:
            test_message = f"Test greeting from {self.my_name}! 👋 This is a test."
            
            self.call_service(
                "meshcore/send_channel_message",
                channel_idx=self.greet_channel,
                message=test_message
            )
            
            self.log(f"Test greeting sent on channel {self.greet_channel}")
            
            # Send HA notification
            self.call_service(
                "notify/persistent_notification",
                title="🧪 Test Greeting Sent",
                message=f"Test message sent on channel {self.greet_channel}:\n\n{test_message}"
            )
            
        except Exception as e:
            self.log(f"Error sending test greeting: {e}", level="ERROR")
