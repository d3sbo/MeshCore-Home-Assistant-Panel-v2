# MeshCore Home Assistant Panel v2

A comprehensive Home Assistant dashboard for [MeshCore](https://meshcore.co.uk/) mesh networking, featuring interactive maps, heatmaps, signal tracking, and automated contact management.

![Heatmap Example](docs/images/heatmap.png)

## Features

### 📍 Interactive Maps
- **Node Map** - Shows all nodes by type (Repeater 📡, Client 📱, Room Server 💬)
- **Hop Frequency Heatmap** - Visualizes which repeaters handle the most traffic
- **Direct Links Heatmap** - Shows 1-hop direct connections between nodes

### 📊 Signal Tracking
- SNR/RSSI monitoring per contact
- Hop count tracking with path visualization
- Multiple reception paths displayed
- Message history with timestamps

### 🤖 Automation
- **Auto-greeting** - Welcomes new companions on Public channel
- **Auto-cleanup** - Removes old contacts (30+ days) from HA and device
- **Persistence** - Survives HA reboots (hops data, last messages, greeted list)

### 🗺️ Path Visualization
- Click node names to highlight message paths
- Dashed lines show routing through mesh
- Color-coded by hop count or traffic intensity

## Requirements

- Home Assistant 2024.1+
- [MeshCore Integration](https://github.com/meshcore-dev/meshcore-ha) v2.3.0+
- [AppDaemon](https://appdaemon.readthedocs.io/) 4.4+
- HACS (for optional cards)

### Optional HACS Cards
- `mushroom-cards` - For styled controls
- `auto-entities` - For dynamic entity lists
- `config-template-card` - For template-based cards

## Installation

### 1. Install AppDaemon Add-on

1. Go to **Settings → Add-ons → Add-on Store**
2. Search for **AppDaemon** and install it
3. Start the add-on

### 2. Install AppDaemon Scripts

Download all `.py` files from this repo's `appdaemon/apps/` folder and copy them to your AppDaemon apps folder.

**For Home Assistant OS (Add-on):**
```
/addon_configs/a0d7b954_appdaemon/apps/
```

**For Home Assistant Container/Core:**
```
/config/appdaemon/apps/
```

Files to copy:
```
meshcore_hops.py              # Signal & hop tracking
meshcore_paths.py             # Path visualization & hop markers
meshcore_cleanup.py           # Auto-cleanup old contacts
meshcore_greeter.py           # Auto-greet new contacts
meshcore_heatmap_export.py    # Heatmap data export
meshcore_nodemap_export.py    # Node map data export
meshcore_directlinks_export.py # Direct links data export
```

You can copy files using:
- **File Editor add-on** - navigate to the folder and upload
- **Samba share** - if enabled
- **SSH/Terminal** - command line access

### 3. Configure AppDaemon

Edit `apps.yaml` in the same folder as the Python files:

**For Home Assistant OS:** `/addon_configs/a0d7b954_appdaemon/apps/apps.yaml`

Add this content:

```yaml
meshcore_hops:
  module: meshcore_hops
  class: MeshCoreHops

meshcore_paths:
  module: meshcore_paths
  class: MeshCorePathMap

meshcore_cleanup:
  module: meshcore_cleanup
  class: MeshCoreCleanup

meshcore_greeter:
  module: meshcore_greeter
  class: MeshCoreGreeter

meshcore_heatmap_export:
  module: meshcore_heatmap_export
  class: MeshCoreHeatmapExport

meshcore_nodemap_export:
  module: meshcore_nodemap_export
  class: MeshCoreNodeMapExport

meshcore_directlinks_export:
  module: meshcore_directlinks_export
  class: MeshCoreDirectLinksExport
```

### 4. Install HTML Map Pages

Copy files from this repo's `www/` folder to your Home Assistant www folder:

**Important:** Always use `/config/www/` (not the AppDaemon folder!)

```
/config/www/meshcore_heatmap.html
/config/www/meshcore_nodemap.html
/config/www/meshcore_directlinks.html
```

### 5. Create Input Helpers

Go to **Settings → Devices & Services → Helpers → Create Helper**

#### Number Helpers

Create 3 number helpers with these settings:

| Name | Entity ID | Min | Max | Step | Initial |
|------|-----------|-----|-----|------|---------|
| `meshcore_advert_threshold_hours` | `input_number.meshcore_advert_threshold_hours` | 1 | 720 | 1 | 12 |
| `meshcore_messages_threshold_hours` | `input_number.meshcore_messages_threshold_hours` | 1 | 720 | 1 | 24 |
| `meshcore_heatmap_threshold_hours` | `input_number.meshcore_heatmap_threshold_hours` | 1 | 720 | 1 | 168 |

**Example Number Helper:**

![Number Helper Example](docs/images/helper_number_example.png)

#### Dropdown Helper

Create 1 dropdown helper:

| Name | Entity ID | Options |
|------|-----------|---------|
| `meshcore_sort_by` | `input_select.meshcore_sort_by` | `Last Advert`, `Last Message`, `Direct Links` |

**Example Dropdown Helper:**

![Dropdown Helper Example](docs/images/helper_dropdown_example.png)

### 6. Configure Your Pubkey

Edit `meshcore_paths.py` line 21 to set your MeshCore device's pubkey:

```python
self.my_repeater_pubkey = "YOUR_PUBKEY_HERE"
```

**To find your pubkey:**
1. Go to **Developer Tools → States**
2. Search for `binary_sensor.meshcore_`
3. Find your device's contact sensor
4. Copy the `pubkey_prefix` attribute (first 12 characters)

This works for both Repeaters and Companion clients.

### 7. Restart AppDaemon

Go to **Settings → Add-ons → AppDaemon → Restart**

Check the logs for any errors: **Settings → Add-ons → AppDaemon → Log**

### 8. Add Dashboard Cards

Test with a simple iframe card first:

```yaml
type: iframe
url: /local/meshcore_heatmap.html
aspect_ratio: "4:3"
```

If you see a map, the HTML files are working!

See `dashboards/` folder for full dashboard examples.

## Configuration

### meshcore_greeter.py

Edit these settings:

```python
self.max_hops = 5           # Max hops to greet
self.greet_channel = 0      # 0 = Public
self.my_name = "MyRepeater" # Your name in greeting
```

### meshcore_cleanup.py

Default is 30 days. Edit line 25:

```python
threshold_days = 30
```

## Data Persistence

The following data survives HA reboots:

| File | Data |
|------|------|
| `/config/www/meshcore_hops_sensors.json` | Full hops sensor data |
| `/config/www/meshcore_last_messages.json` | Last message times |
| `/config/www/meshcore_hops_data.json` | Hop node use counts |
| `/config/www/meshcore_greeted.json` | Greeted contacts list |
| `/config/www/meshcore_directlinks_persist.json` | Direct link connections |

Data older than 7 days is automatically cleaned up.

## Entities Created

### Sensors
- `sensor.meshcore_hops_<pubkey>` - Per-contact hop/signal data
- `sensor.meshcore_map_entities` - List of map entities
- `sensor.meshcore_path_entities` - List of path entities
- `sensor.meshcore_hop_entities` - List of hop node entities

### Device Trackers
- `device_tracker.meshcore_path_<n>` - Message path trackers
- `device_tracker.meshcore_hop_<n>` - Hop node markers

## Screenshots

### Hop Frequency Heatmap
![Heatmap](docs/images/heatmap.png)

### Node Type Map
![Node Map](docs/images/nodemap.png)

### Direct Links Map
![Direct Links](docs/images/directlinks.png)

## Troubleshooting

### Maps not showing data
1. Check AppDaemon logs for errors: **Settings → Add-ons → AppDaemon → Log**
2. Verify JSON files exist in `/config/www/`
3. Clear browser cache or hard refresh (Ctrl+Shift+R)
4. Check browser console for JavaScript errors (F12)

### HTML files not found
- Make sure files are in `/config/www/` (not `/addon_configs/.../www/`)
- Restart Home Assistant after adding files
- Try accessing directly: `http://YOUR_HA_IP:8123/local/meshcore_heatmap.html`

### Input helpers not working
- Check the Entity ID matches exactly (case sensitive)
- Entity ID should be `input_number.meshcore_advert_threshold_hours` not `input_number.input_number.meshcore_...`

### Paths not appearing
1. Ensure `meshcore_hops.py` is receiving `meshcore_raw_event` events
2. Check that contacts have coordinates (lat/lon)
3. Verify `path_nodes` attribute exists on hops sensors

### Cleanup not working
1. Check AppDaemon logs at 3am
2. Verify MeshCore services are available
3. Check `last_advert` and `last_message` attributes

## Credits

- [MeshCore](https://meshcore.co.uk/) - Mesh networking protocol
- [MeshCore HA Integration](https://github.com/meshcore-dev/meshcore-ha) - Home Assistant integration
- [Leaflet](https://leafletjs.com/) - Interactive maps
- [Leaflet.heat](https://github.com/Leaflet/Leaflet.heat) - Heatmap plugin

## License

MIT License - See [LICENSE](LICENSE) file

## Contributing

Contributions welcome! Please open an issue or pull request.
