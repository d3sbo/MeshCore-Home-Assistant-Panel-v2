# Installation Guide

## Prerequisites

1. **Home Assistant** 2024.1 or newer
2. **MeshCore Integration** installed via HACS
   - Repository: https://github.com/meshcore-dev/meshcore-ha
3. **AppDaemon** add-on installed and running
4. **HACS** (optional, for mushroom cards)

## Step-by-Step Installation

### Step 1: Install AppDaemon Scripts

1. Connect to your Home Assistant via SSH or File Editor
2. Navigate to `/config/appdaemon/apps/`
3. Copy all `.py` files from the `appdaemon/apps/` folder:
   - `meshcore_hops.py`
   - `meshcore_paths.py`
   - `meshcore_cleanup.py`
   - `meshcore_greeter.py`
   - `meshcore_heatmap_export.py`
   - `meshcore_nodemap_export.py`
   - `meshcore_directlinks_export.py`

### Step 2: Configure AppDaemon

1. Open `/config/appdaemon/apps/apps.yaml`
2. Add the contents from `apps.yaml.example`:

```yaml
meshcore_hops:
  module: meshcore_hops
  class: MeshCoreHops

meshcore_paths:
  module: meshcore_paths
  class: MeshCorePathMap

# ... (see apps.yaml.example for full config)
```

3. Restart AppDaemon

### Step 3: Install HTML Map Files

1. Navigate to `/config/www/`
2. Copy all `.html` files from the `www/` folder:
   - `meshcore_heatmap.html`
   - `meshcore_nodemap.html`
   - `meshcore_directlinks.html`

### Step 4: Create Input Helpers

Go to **Settings → Devices & Services → Helpers → Create Helper**

Create these Number helpers:

| Name | Entity ID | Min | Max | Step |
|------|-----------|-----|-----|------|
| MeshCore Advert Threshold Hours | `input_number.meshcore_advert_threshold_hours` | 1 | 720 | 1 |
| MeshCore Messages Threshold Hours | `input_number.meshcore_messages_threshold_hours` | 1 | 720 | 1 |
| MeshCore Heatmap Threshold Hours | `input_number.meshcore_heatmap_threshold_hours` | 1 | 720 | 1 |

Create this Dropdown helper:

| Name | Entity ID | Options |
|------|-----------|---------|
| MeshCore Sort By | `input_select.meshcore_sort_by` | Last Advert, Last Message, Direct Links |

### Step 5: Configure Your Repeater

Edit `meshcore_paths.py` line 17:

```python
self.my_repeater_pubkey = "YOUR_PUBKEY_HERE"
```

Find your pubkey in the MeshCore contact sensor attributes.

### Step 6: Add Dashboard Cards

1. Go to your dashboard
2. Edit dashboard → Add Card → Manual
3. Paste YAML from `dashboards/meshcore_dashboard.yaml`

Or add a simple iframe card:

```yaml
type: iframe
url: /local/meshcore_heatmap.html
aspect_ratio: "4:3"
```

### Step 7: Verify Installation

1. Check AppDaemon logs for errors:
   - Settings → Add-ons → AppDaemon → Log
2. Verify JSON files are created in `/config/www/`:
   - `meshcore_heatmap_data.json`
   - `meshcore_nodemap_data.json`
   - `meshcore_directlinks_data.json`
3. Open the maps in browser to test

## Optional: Install HACS Cards

For the best dashboard experience, install via HACS:

1. **Mushroom Cards** - Beautiful styled cards
2. **Auto Entities** - Dynamic entity lists
3. **Config Template Card** - Template-based cards

## Updating

1. Download latest release
2. Replace files in `/config/appdaemon/apps/` and `/config/www/`
3. Restart AppDaemon
4. Hard refresh browser (Ctrl+Shift+R)

## Uninstalling

1. Remove entries from `apps.yaml`
2. Delete `.py` files from `/config/appdaemon/apps/`
3. Delete `.html` files from `/config/www/`
4. Delete JSON persistence files from `/config/www/`
5. Remove input helpers if desired
