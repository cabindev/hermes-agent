#!/usr/bin/env bash
# Deploy Hermes Agent (NousResearch) to an Azure VM, running on Gemini.
#
# Requires: az CLI logged in, GEMINI_API_KEY exported.
# Creates: resource group, Ubuntu 24.04 VM, NSG rules, public DNS name,
#          Docker + official hermes-agent image + Caddy (auto HTTPS).
set -euo pipefail

# ---------------------------------------------------------------- settings
RG="${RG:-hermes-agent-rg}"
LOC="${LOC:-southeastasia}"
VM="${VM:-hermes-vm}"
SIZE="${SIZE:-Standard_B2s}"
DNS_LABEL="${DNS_LABEL:-hermes-sdn}"
ADMIN="${ADMIN:-azureuser}"
IMAGE="Canonical:ubuntu-24_04-lts:server:latest"
FQDN="${DNS_LABEL}.${LOC}.cloudapp.azure.com"

: "${GEMINI_API_KEY:?export GEMINI_API_KEY first}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.7-flash}"

# Dashboard credentials: generated here, printed at the end.
DASH_USER="${DASH_USER:-admin}"
DASH_PASS="${DASH_PASS:-$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)}"
DASH_SECRET="$(openssl rand -hex 32)"

MY_IP="$(curl -fsS https://api.ipify.org)"

echo "==> Deploying to $FQDN (RG=$RG, size=$SIZE, model=$GEMINI_MODEL)"
echo "==> SSH will be restricted to $MY_IP"

# ------------------------------------------------------------- cloud-init
CLOUD_INIT="$(mktemp -t hermes-cloudinit).yaml"
cat > "$CLOUD_INIT" <<CLOUDINIT
#cloud-config
package_update: true
packages: [ca-certificates, curl, gnupg, debian-keyring, debian-archive-keyring, apt-transport-https]

write_files:
  # Hermes data volume: pre-seeded so the container's first-boot seeder
  # (docker/stage2-hook.sh seed_one) leaves our values alone.
  - path: /opt/hermes-data/.env
    permissions: '0600'
    content: |
      GEMINI_API_KEY=${GEMINI_API_KEY}
      GOOGLE_API_KEY=${GEMINI_API_KEY}
      GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai

  - path: /opt/hermes-data/config.yaml
    permissions: '0640'
    content: |
      model:
        default: "${GEMINI_MODEL}"
        provider: "gemini"
      tool_loop_guardrails:
        hard_stop_enabled: true
        hard_stop_after:
          exact_failure: 5
          idempotent_no_progress: 5

  - path: /etc/caddy/Caddyfile
    permissions: '0644'
    content: |
      ${FQDN} {
        encode gzip
        reverse_proxy 127.0.0.1:9119
      }

runcmd:
  # --- Docker (official repo) ---
  - install -m 0755 -d /etc/apt/keyrings
  - curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  - chmod a+r /etc/apt/keyrings/docker.asc
  - echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" > /etc/apt/sources.list.d/docker.list
  # --- Caddy (official repo) ---
  - curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  - curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  - apt-get update -y
  # --force-confold is required: /etc/caddy/Caddyfile is pre-created by
  # write_files above, so dpkg would otherwise stop at an interactive
  # conffile prompt with no stdin, leaving caddy in state `iU` (unconfigured)
  # and the caddy system user never created -> systemd fails 217/USER.
  - apt-get install -y -o Dpkg::Options::=--force-confold docker-ce docker-ce-cli containerd.io docker-compose-plugin caddy
  - systemctl enable --now docker
  - systemctl restart caddy
  # --- Hermes ---
  - chown -R 10000:10000 /opt/hermes-data
  - docker pull nousresearch/hermes-agent:latest
  - |
    docker run -d --name hermes --restart unless-stopped \
      -v /opt/hermes-data:/opt/data \
      -p 127.0.0.1:9119:9119 \
      -e HERMES_UID=10000 -e HERMES_GID=10000 \
      -e HERMES_DASHBOARD=1 \
      -e HERMES_DASHBOARD_HOST=0.0.0.0 \
      -e HERMES_DASHBOARD_PORT=9119 \
      -e HERMES_DASHBOARD_BASIC_AUTH_USERNAME='${DASH_USER}' \
      -e HERMES_DASHBOARD_BASIC_AUTH_PASSWORD='${DASH_PASS}' \
      -e HERMES_DASHBOARD_BASIC_AUTH_SECRET='${DASH_SECRET}' \
      nousresearch/hermes-agent:latest gateway run
  - touch /opt/hermes-data/.deploy-done
CLOUDINIT

# ----------------------------------------------------------------- deploy
az group create -n "$RG" -l "$LOC" -o none

az vm create \
  --resource-group "$RG" --name "$VM" \
  --image "$IMAGE" --size "$SIZE" \
  --admin-username "$ADMIN" --generate-ssh-keys \
  --public-ip-address-dns-name "$DNS_LABEL" \
  --public-ip-sku Standard \
  --os-disk-size-gb 64 \
  --custom-data "$CLOUD_INIT" \
  --nsg-rule NONE \
  -o none

# NSG: HTTPS + HTTP (ACME) open; SSH only from this machine.
az network nsg rule create -g "$RG" --nsg-name "${VM}NSG" -n allow-https \
  --priority 100 --destination-port-ranges 443 --access Allow --protocol Tcp -o none
az network nsg rule create -g "$RG" --nsg-name "${VM}NSG" -n allow-http-acme \
  --priority 110 --destination-port-ranges 80 --access Allow --protocol Tcp -o none
az network nsg rule create -g "$RG" --nsg-name "${VM}NSG" -n allow-ssh-myip \
  --priority 120 --destination-port-ranges 22 --source-address-prefixes "$MY_IP" \
  --access Allow --protocol Tcp -o none

rm -f "$CLOUD_INIT"

cat <<EOF

=======================================================
  Hermes Agent deployed
=======================================================
  URL:      https://${FQDN}
  User:     ${DASH_USER}
  Password: ${DASH_PASS}
  Model:    ${GEMINI_MODEL} (provider: gemini)
  SSH:      ssh ${ADMIN}@${FQDN}
=======================================================
cloud-init takes ~4-6 min (docker pull is ~1 GB).
Watch it:  ssh ${ADMIN}@${FQDN} 'sudo cloud-init status --wait'
EOF
