# Incident: Wall offline

## Symptoms
- Alert `WallOffline` firing
- Wall missing heartbeat for >30s

## Troubleshooting
1. Verify network reachability:
   - `ping wall-controller-XX`
   - Check VLAN tagging and ACLs
2. Check controller service:
   - `systemctl status vw-wallctl`
   - `journalctl -u vw-wallctl -n 200 --no-pager`
3. Verify client cert and CA trust:
   - `openssl s_client -connect mgmt-api.videowall.local:8443 -cert client.crt -key client.key -CAfile ca-chain.pem`
4. Check mgmt API:
   - `curl -k --cert ... --key ... https://mgmt-api.../health`
5. Restart controller:
   - `systemctl restart vw-wallctl`
6. If persists, collect logs + escalate (network team / K8s team).

## Recovery
- Re-deploy controller via Ansible
- Re-issue client cert
