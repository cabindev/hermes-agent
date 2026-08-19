# Azure deployment

Hermes runs on a single Azure VM as a Docker container behind Caddy.

```
GitHub push to main
  -> build image from this repo        (.github/workflows/deploy-azure.yml)
  -> ghcr.io/cabindev/hermes-agent:<sha>
  -> az vm run-command  (no inbound SSH is opened for CI)
  -> /usr/local/bin/hermes-deploy.sh on the VM
       pull -> swap container -> health check -> prune old images
       (rolls back to the previous image if the new one does not come up)
```

| | |
|---|---|
| URL | https://hermes-sdn.southeastasia.cloudapp.azure.com |
| Resource group | `hermes-agent-rg` (southeastasia) |
| VM | `hermes-vm`, Standard_B2s, Ubuntu 24.04 |
| Data (survives redeploys) | `/opt/hermes-data` on the VM -> `/opt/data` in the container |

## Files here

- `deploy-azure.sh` — one-shot provisioning of the VM. Already run; it is **not
  idempotent** (`az vm create` fails if `hermes-vm` exists). Only needed to
  rebuild from scratch, and only after deleting the resource group.
- `hermes-deploy.sh` — the per-release script. This is a **copy** of what runs;
  the live one is `/usr/local/bin/hermes-deploy.sh` on the VM. Editing it here
  does not update the VM. Push it with:

  ```bash
  ssh azureuser@hermes-sdn.southeastasia.cloudapp.azure.com \
    'sudo tee /usr/local/bin/hermes-deploy.sh >/dev/null && sudo chmod 755 /usr/local/bin/hermes-deploy.sh' \
    < azure-deploy/hermes-deploy.sh
  ```

## Things that will bite

- **SSH is pinned to one IP.** The NSG rule `allow-ssh-myip` holds whichever
  address ran the last update, and consumer ISPs rotate addresses. When ssh
  hangs, that is why:

  ```bash
  az network nsg rule update -g hermes-agent-rg --nsg-name hermes-vmNSG \
    -n allow-ssh-myip --source-address-prefixes "$(curl -fsS https://api.ipify.org)"
  ```

  CI is unaffected — it goes through `az vm run-command`, not SSH.

- **GitHub's OIDC subject carries numeric IDs.** Entra needs a federated
  credential for `repo:cabindev@157673534/hermes-agent@1338571221:ref:refs/heads/main`,
  not the documented `repo:cabindev/hermes-agent:ref:refs/heads/main`. The plain
  form alone fails the login step with `AADSTS700213`.

- **`az vm run-command invoke` exits 0 even when the script it ran failed.** The
  workflow greps the output for `[hermes-deploy] deploy OK` instead of trusting
  the exit code.

- **Dashboard credentials live on the VM**, in `/etc/hermes/deploy.env` (0600,
  root), and are injected with `--env-file`. Redeploys reuse them, so the
  password does not change under you. Rotate by editing that file and
  re-running `hermes-deploy.sh`.

- **Image retention is `KEEP` (default 2).** Each release pulls ~3.9 GB, so old
  tagged builds are removed down to the live image plus one to roll back to.

## Deploying

Push to `main`, or run the workflow manually from the Actions tab. Paths that
cannot affect the image are filtered out in `paths-ignore` — see the comment
there before adding to it, because `COPY --link . .` means most of the repo
really is part of the image.
