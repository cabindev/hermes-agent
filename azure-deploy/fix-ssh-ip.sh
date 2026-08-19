#!/usr/bin/env bash
# Repoint the VM's SSH allow-rule at this machine's current public IP.
#
# The NSG rule allow-ssh-myip holds a single address, so ssh starts hanging
# whenever the ISP hands out a new one. Run this, wait a moment, ssh again.
# CI is unaffected either way: it reaches the VM through `az vm run-command`.
set -euo pipefail

RG="${RG:-hermes-agent-rg}"
NSG="${NSG:-hermes-vmNSG}"
RULE="${RULE:-allow-ssh-myip}"
HOST="${HOST:-hermes-sdn.southeastasia.cloudapp.azure.com}"

command -v az >/dev/null || { echo "az CLI not found"; exit 1; }
az account show >/dev/null 2>&1 || { echo "not logged in; run: az login"; exit 1; }

NEW="$(curl -fsS https://api.ipify.org)"
OLD="$(az network nsg rule show -g "$RG" --nsg-name "$NSG" -n "$RULE" --query sourceAddressPrefix -o tsv)"

if [ "$NEW" = "$OLD" ]; then
  echo "already allows $NEW - if ssh still hangs the cause is something else"
else
  echo "$OLD -> $NEW"
  az network nsg rule update -g "$RG" --nsg-name "$NSG" -n "$RULE" \
    --source-address-prefixes "$NEW" -o none
  echo "rule updated; Azure takes a few seconds to apply it"
fi

printf 'testing ssh... '
for i in $(seq 1 6); do
  if ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new \
       "azureuser@$HOST" true 2>/dev/null; then
    echo "ok"; exit 0
  fi
  sleep 5
done
echo "still failing after 30s - check that the VM is running: az vm show -g $RG -n hermes-vm -d --query powerState"
exit 1
