import appdaemon.plugins.hass.hassapi as hass
import time
import json

class MeshCoreMapEntities(hass.Hass):

    def initialize(self):
        self.log("MeshCoreMapEntities initialized")

        # Only update when filters change - remove run_every
        self.listen_state(self.update_entities, "input_number.meshcore_advert_threshold_hours")
        self.listen_state(self.update_entities, "input_select.meshcore_type")
        
        # Initial update on startup
        self.run_in(self.update_entities, 5)

    def update_entities(self, *args, **kwargs):
        now_ts = time.time()

        try:
            threshold_hours = float(self.get_state("input_number.meshcore_advert_threshold_hours"))
        except Exception:
            threshold_hours = 12.0

        threshold_sec = threshold_hours * 3600
        selected_type_raw = self.get_state("input_select.meshcore_type") or "All"
        
        # Normalize selected type the same way we normalize node types
        selected_type = selected_type_raw.lower().replace(" ", "")

        type_map = {
            "client": "client",
            "meshclient": "client",
            "repeater": "repeater",
            "meshrepeater": "repeater",
            "roomserver": "roomserver",
            "room_server": "roomserver",
            "room-server": "roomserver",
            "roomsrv": "roomserver",
            "rs": "roomserver",
        }

        entities = []
        all_states = self.get_state()

        for entity_id, data in all_states.items():
            if not entity_id.startswith("binary_sensor.meshcore_"):
                continue

            attrs = data.get("attributes", {})

            if not attrs.get("pubkey_prefix") or not attrs.get("last_advert"):
                continue

            lat = attrs.get("latitude")
            lon = attrs.get("longitude")

            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            if lat == 0 or lon == 0:
                continue
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue

            last_advert = attrs.get("last_advert")
            if not isinstance(last_advert, (int, float)):
                continue
            if (now_ts - last_advert) > threshold_sec:
                continue

            raw_type = (attrs.get("node_type_str") or "").lower().replace(" ", "")
            node_norm = type_map.get(raw_type, raw_type)

            if selected_type != "all" and node_norm != selected_type:
                continue

            entities.append(entity_id)

        # Update sensor with entities list
        self.set_state(
            "sensor.meshcore_map_entities",
            state=str(len(entities)),
            attributes={
                "entities": json.loads(json.dumps(entities)),
                "threshold_hours": threshold_hours,
                "selected_type": selected_type_raw
            }
        )

        # Write YAML card config to file
        yaml_content = f"""type: custom:map-card
default_zoom: 12
auto_fit: false
fit_zones: false
entities:
"""
        for entity in entities:
            yaml_content += f"  - {entity}\n"
        
        # Write to Home Assistant www folder
        config_path = "/homeassistant/www/meshcore_map_card.yaml"
        try:
            with open(config_path, 'w') as f:
                f.write(yaml_content)
            self.log(f"Wrote map card config with {len(entities)} entities to {config_path}")
            
            # Fire event to notify that map has been updated
            self.fire_event("meshcore_map_updated", 
                           entity_count=len(entities),
                           threshold_hours=threshold_hours,
                           selected_type=selected_type_raw)
            
        except Exception as e:
            self.log(f"Error writing card config: {e}", level="ERROR")

        self.log(f"MeshCoreMapEntities updated: {len(entities)} entities (threshold: {threshold_hours}h, type: {selected_type_raw})")
