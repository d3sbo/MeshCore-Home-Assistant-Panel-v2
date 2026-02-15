import appdaemon.plugins.hass.hassapi as hass
import time

class MeshCoreCleanup(hass.Hass):
    """
    Cleans up old MeshCore contact entities that haven't been seen in 30 days.
    Checks BOTH last_advert AND last_message - only deletes if BOTH are old.
    Removes from both Home Assistant AND the MeshCore device.
    Runs daily at 3am.
    """

    def initialize(self):
        self.log("MeshCoreCleanup initialized")
        
        # Run daily at 3am
        self.run_daily(self.cleanup_old_contacts, "03:00:00")
        
        # Also run 60 seconds after startup
        self.run_in(self.cleanup_old_contacts, 60)

    def cleanup_old_contacts(self, kwargs=None):
        """Remove contact entities older than 30 days from HA and device"""
        try:
            now_ts = time.time()
            threshold_days = 30
            threshold_sec = threshold_days * 24 * 3600
            
            all_states = self.get_state()
            deleted_count = 0
            checked_count = 0
            skipped_active = 0
            
            for entity_id, state_data in list(all_states.items()):
                if not (entity_id.startswith("binary_sensor.meshcore_") and 
                        entity_id.endswith("_contact")):
                    continue
                
                checked_count += 1
                attrs = state_data.get("attributes", {}) if state_data else {}
                last_advert = attrs.get("last_advert")
                last_message = attrs.get("last_message")
                pubkey_prefix = attrs.get("pubkey_prefix")
                name = attrs.get("friendly_name", entity_id)
                
                # Check last_advert
                advert_old = True
                advert_age_days = None
                if last_advert and isinstance(last_advert, (int, float)):
                    advert_age_days = (now_ts - last_advert) / 86400
                    if (now_ts - last_advert) <= threshold_sec:
                        advert_old = False
                
                # Check last_message
                message_old = True
                message_age_days = None
                if last_message and isinstance(last_message, (int, float)):
                    message_age_days = (now_ts - last_message) / 86400
                    if (now_ts - last_message) <= threshold_sec:
                        message_old = False
                
                # Also check hops sensor for last_message_time
                if pubkey_prefix:
                    hops_sensor = f"sensor.meshcore_hops_{pubkey_prefix}"
                    hops_state = all_states.get(hops_sensor)
                    if hops_state:
                        hops_attrs = hops_state.get("attributes", {})
                        hops_last_msg = hops_attrs.get("last_message_time")
                        if hops_last_msg and isinstance(hops_last_msg, (int, float)):
                            if (now_ts - hops_last_msg) <= threshold_sec:
                                message_old = False
                                message_age_days = (now_ts - hops_last_msg) / 86400
                
                # Only delete if BOTH advert AND message are old (or missing)
                if not (advert_old and message_old):
                    if not advert_old:
                        self.log(f"Keeping {name}: last advert {advert_age_days:.1f} days ago")
                    elif not message_old:
                        self.log(f"Keeping {name}: last message {message_age_days:.1f} days ago (advert: {advert_age_days:.1f}d)")
                    skipped_active += 1
                    continue
                
                # Both are old - safe to delete
                age_info = f"advert: {advert_age_days:.1f}d" if advert_age_days else "no advert"
                if message_age_days:
                    age_info += f", message: {message_age_days:.1f}d"
                
                self.log(f"Deleting {name} (pubkey: {pubkey_prefix}): {age_info}")
                
                # Step 1: Remove from MeshCore device (if pubkey available)
                if pubkey_prefix:
                    try:
                        self.call_service(
                            "meshcore/execute_command",
                            command=f"remove_contact {pubkey_prefix}"
                        )
                        self.log(f"  - Removed from device: {pubkey_prefix}")
                    except Exception as e:
                        self.log(f"  - Failed to remove from device: {e}", level="WARNING")
                
                # Step 2: Remove discovered contact from HA
                if pubkey_prefix:
                    try:
                        self.call_service(
                            "meshcore/remove_discovered_contact",
                            pubkey_prefix=pubkey_prefix
                        )
                        self.log(f"  - Removed discovered contact from HA")
                    except Exception as e:
                        self.log(f"  - Failed to remove discovered contact: {e}", level="WARNING")
                
                deleted_count += 1
            
            # Step 3: Cleanup any unavailable contact sensors
            if deleted_count > 0:
                try:
                    self.call_service("meshcore/cleanup_unavailable_contacts")
                    self.log("Cleaned up unavailable contact sensors")
                except Exception as e:
                    self.log(f"Failed to cleanup unavailable contacts: {e}", level="WARNING")
            
            self.log(f"Cleanup complete: checked {checked_count}, deleted {deleted_count}, kept {skipped_active} active (threshold: {threshold_days} days)")
            
        except Exception as e:
            self.log(f"Error during cleanup: {e}", level="ERROR")
            import traceback
            self.log(traceback.format_exc(), level="ERROR")
