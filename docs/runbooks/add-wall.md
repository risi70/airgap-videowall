# Runbook: Add a new wall

## Goal
Add wall `wall-controller-05` and its associated players, then activate a layout.

## Steps
1. **Create wall resource**
   - `curl -k --cert <client> --key <key> -X POST https://mgmt-api.../api/v1/walls -d '{"wall_id":"wall-05","type":"tiles-1080p","tiles":24}'`
   - Expected: `201 Created`, wall listed.

2. **Issue client cert (Vault)**
   - `vault write pki_int/issue/client-cert common_name=wall-controller-05.videowall.local ttl=720h`
   - Store `client.crt` + `client.key`.

3. **Update Ansible inventory**
   - Add host under `wall_controllers`.
   - Add players under `tile_players_1080p` or `big_screen_players_4k`.

4. **Deploy controller**
   - `ansible-playbook -i inventory/hosts.yml playbooks/deploy-wall-controllers.yml --limit wall-controller-05`

5. **Deploy players**
   - `ansible-playbook -i inventory/hosts.yml playbooks/deploy-tile-players.yml --limit 'tile-*'`

6. **Create layout**
   - Use mgmt UI or API to define mosaic layout and map sources to tiles.
   - Expected: layout is `active=false` initially.

7. **Activate**
   - `POST /api/v1/walls/<id>/layouts/<layout_id>/activate`
   - Expected: players start playback; Grafana shows wall active.

## Rollback
- Deactivate layout.
- Stop controller service; revert inventory changes.
