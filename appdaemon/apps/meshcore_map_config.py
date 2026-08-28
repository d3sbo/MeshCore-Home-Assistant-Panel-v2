import appdaemon.plugins.hass.hassapi as hass
import json


class MeshCoreMapConfig(hass.Hass):
    """
    Writes shared map configuration from apps.yaml to
    /config/www/meshcore_map_config.json so the static map HTML files
    (nodemap, heatmap, direct links, heatmap playback) can read it.

    Currently used for the CARTO basemap API key, which CARTO now
    requires for its public tile service. Get a free key (5M tiles/month,
    non-commercial) from https://carto.com/basemaps/apikey/ and add it
    to this app's config in apps.yaml:

        meshcore_map_config:
          module: meshcore_map_config
          class: MeshCoreMapConfig
          carto_api_key: "your-key-here"

    The maps fall back to keyless tile URLs if this file or the key
    is missing.
    """

    OUTPUT_PATH = "/homeassistant/www/meshcore_map_config.json"

    def initialize(self):
        config = {}
        key = self.args.get("carto_api_key")
        if key:
            config["carto_api_key"] = str(key)
        else:
            self.log("No carto_api_key set in apps.yaml - maps will use keyless tile URLs, which CARTO may reject")

        try:
            with open(self.OUTPUT_PATH, "w") as f:
                json.dump(config, f, indent=2)
            self.log(f"Map config written to {self.OUTPUT_PATH}")
        except Exception as e:
            self.error(f"Failed to write map config: {e}")
