# Runbook: Onboard a new source

## Steps
1. Deploy source agent host (OS hardening + VLAN) via Ansible:
   - `ansible-playbook playbooks/configure-vlans.yml --limit source-agent-29 -e vlan_id=140 -e parent_interface=ens192 -e ip_address=10.50.40.29/24`

2. Issue client cert (Vault) and place into `/opt/videowall/certs`.

3. Deploy agent:
   - `ansible-playbook playbooks/deploy-source-agents.yml --limit source-agent-29`

4. Register source (API or wizard)
   - Expected: source appears in mgmt inventory, tagged appropriately.

5. Probe input
   - Configure `probe_cmd` and/or media pipeline parameters.
   - Expected: health_status becomes `online` and stays stable.

6. Assign tags
   - Use ABAC tags (e.g., `clearance:confidential`, `zone:source`, `type:vdi`).

## Rollback
- Remove source from mgmt API inventory.
- Stop service `vw-source-agent`.
