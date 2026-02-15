# Troubleshooting Guide

## Common Issues

### Maps Show No Data

**Symptoms:**
- Heatmap/nodemap is blank
- "No data file found" in browser console

**Solutions:**
1. Check AppDaemon is running
2. Check logs for export errors:
   ```
   Settings → Add-ons → AppDaemon → Log
   ```
3. Verify JSON files exist in `/config/www/`:
   - `meshcore_heatmap_data.json`
   - `meshcore_nodemap_data.json`
4. Check threshold isn't too restrictive (try 168 hours)
5. Hard refresh browser: `Ctrl+Shift+R`

### Paths Not Appearing on Heatmap

**Symptoms:**
- Nodes show but no path lines
- Click on legend does nothing

**Solutions:**
1. Check `sensor.meshcore_hops_*` entities have `path_nodes` attribute
2. Verify contacts have coordinates (lat/lon)
3. Check AppDaemon logs for path matching errors
4. Ensure threshold includes recent messages

### Contact Cleanup Not Working

**Symptoms:**
- Old contacts remain after 30 days
- No cleanup log entries

**Solutions:**
1. Cleanup runs at 3am daily - check logs after that time
2. Verify MeshCore services are available:
   ```
   Developer Tools → Services → meshcore.remove_contact
   ```
3. Check both `last_advert` AND `last_message` - both must be old
4. Manual test:
   ```yaml
   service: meshcore.execute_command
   data:
     command: remove_contact PUBKEY_PREFIX
   ```

### Greeter Not Sending Messages

**Symptoms:**
- New contacts detected but no greeting sent
- No notification in HA

**Solutions:**
1. Check if contact is a Client (not Repeater)
2. Verify hop count ≤ 5
3. Check `/config/www/meshcore_greeted.json` - may already be greeted
4. Verify MeshCore channel service works:
   ```yaml
   service: meshcore.send_channel_message
   data:
     channel: 0
     text: "Test"
   ```

### Sensors Not Persisting After Reboot

**Symptoms:**
- `sensor.meshcore_hops_*` gone after restart
- Heatmap data reset

**Solutions:**
1. Check persistence files exist:
   - `/config/www/meshcore_hops_sensors.json`
   - `/config/www/meshcore_last_messages.json`
   - `/config/www/meshcore_hops_data.json`
2. Check file permissions (should be readable by HA)
3. Check AppDaemon logs for load errors on startup
4. Data older than 7 days is auto-cleaned

### Map Tiles Not Loading

**Symptoms:**
- Map shows but tiles are gray/missing
- "Failed to load resource" errors

**Solutions:**
1. Check internet connectivity
2. OpenStreetMap might be rate-limiting - wait and retry
3. Try different browser
4. Check for ad blockers blocking tile requests

### Incorrect Node Coordinates

**Symptoms:**
- Nodes appear in wrong location
- All nodes clustered at 0,0

**Solutions:**
1. Check MeshCore contact sensors have `adv_lat`/`adv_lon` attributes
2. Nodes without GPS won't appear on map
3. Check coordinate cache is building:
   ```
   AppDaemon logs: "Coordinate cache built with X entries"
   ```

## Debug Logging

### Enable Debug Logs

In `apps.yaml`, you can add logging:

```yaml
meshcore_hops:
  module: meshcore_hops
  class: MeshCoreHops
  log_level: DEBUG
```

### Key Log Messages

**meshcore_hops.py:**
- `RX_LOG: {name} - {hops} hops, SNR: {snr}` - Message received
- `Loaded X last message times` - Persistence loaded
- `Saved X hops sensors` - Persistence saved

**meshcore_paths.py:**
- `Path check: {name} - max_hops={X}, path_nodes=[...]` - Path processing
- `Found coords for node {prefix}` - Node matched
- `No coords for node {prefix}` - Node not found

**meshcore_cleanup.py:**
- `Keeping {name}: last message X days ago` - Contact kept
- `Deleting {name}: advert: Xd, message: Yd` - Contact removed

### Browser Console

Open browser Developer Tools (F12) → Console to see:
- JSON fetch errors
- JavaScript errors
- Data refresh logs

## Getting Help

1. Check AppDaemon logs first
2. Verify MeshCore integration is working independently
3. Test with simple iframe before complex dashboard
4. Open GitHub issue with:
   - AppDaemon log snippets
   - Browser console errors
   - HA version
   - MeshCore integration version
