# Incident: Stream loss

## Symptoms
- `StreamLost` alert
- Player shows black frame or stalled stream

## Troubleshooting (path-based)
1. Source health:
   - mgmt UI: source health_status
   - on agent: `systemctl status vw-source-agent`
2. Gateway ingest:
   - inspect gateway metrics and logs (Loki)
   - verify RTSP/SRT endpoints reachable
3. SFU:
   - verify sessions and packet loss metrics
4. Player:
   - `journalctl -u vw-tile-player -n 200 --no-pager`
   - validate playback URL / token freshness
5. Restart sequence:
   - restart player -> restart SFU session -> restart gateway pipeline -> restart source agent

## Recovery
- Re-assign source to wall (forces new subscribe token)
- Fallback to alternate source
