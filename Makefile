SHELL := /bin/bash
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))

.PHONY: vault-init keycloak-init certs-init deploy deploy-agents test-unit test-integration test-load lint security-scan bundle-export bundle-verify bundle-import rollback

vault-init:
	cd security/vault && ./setup-pki.sh

keycloak-init:
	cd security/keycloak && ./bootstrap.sh --url $${KEYCLOAK_URL:-http://keycloak.vw-control.svc:8080} --admin-user $${KEYCLOAK_ADMIN:-admin} --admin-pass $${KEYCLOAK_PASS:-admin}

certs-init:
	cd security/certs && ./generate-lab-certs.sh --domain $${VW_DOMAIN:-videowall.local}

deploy:
	helm upgrade --install vw-obs charts/vw-observability -n vw-obs --create-namespace

deploy-agents:
	cd security/ansible && ansible-playbook playbooks/deploy-wall-controllers.yml
	cd security/ansible && ansible-playbook playbooks/deploy-tile-players.yml
	cd security/ansible && ansible-playbook playbooks/deploy-source-agents.yml

test-unit:
	@echo "Unit tests live in the service modules (policy/bundlectl/audit)."

test-integration:
	cd tests/integration && docker compose up -d
	cd tests/integration && ./run-tests.sh
	cd tests/integration && docker compose down -v

test-load:
	@echo "Load tests are environment-specific; see docs/testing.md."

lint:
	helm lint charts/vw-observability

security-scan:
	@echo "Offline trivy scan requires mirrored DB; integrate into CI with local cache."

bundle-export:
	@echo "Bundle export implemented in bundlectl module."

bundle-verify:
	@echo "Bundle verify implemented in bundlectl module."

bundle-import:
	@echo "Bundle import implemented in bundlectl module."

rollback:
	@echo "Use helm rollback + runbook docs/runbooks/rollback.md"

# ── Pi Image ──────────────────────────────────────────────────────────────
.PHONY: pi-verify pi-image

pi-verify:
	@bash tools/pi-image/verify.sh

pi-image: pi-verify
	@echo "Build with: sudo tools/pi-image/vw-build-pi-image.sh --tile-id <id> ..."
