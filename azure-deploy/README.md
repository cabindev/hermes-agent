# Azure deployment

Hermes runs on a single Azure VM as a Docker container behind Caddy.

```
GitHub push to main
  -> build image from this repo        (.github/workflows/deploy-azure.yml)
  -> ghcr.io/cabindev/hermes-agent:<sha>
  -> az vm run-command  (no inbound SSH is opened for CI)
  -> install hermes-deploy.sh from this repo onto the VM
  -> /usr/local/bin/hermes-deploy.sh on the VM
       wait for the agent to go idle -> pull -> swap container
       -> health check -> prune old images
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
- `hermes-deploy.sh` — the per-release script, and the source of truth: every
  deploy installs this file onto the VM before running it, so the two cannot
  drift. Tunables: `KEEP` (images retained, default 2), `IDLE_QUIET` /
  `IDLE_MAX_WAIT` (how long to wait for an idle agent, default 45s / 600s).
- `fix-ssh-ip.sh` — repoint the NSG SSH rule at your current public IP and
  verify the connection. Run it whenever ssh starts hanging.

## Things that will bite

- **SSH is pinned to one IP.** The NSG rule `allow-ssh-myip` holds whichever
  address ran the last update, and consumer ISPs rotate addresses. When ssh
  hangs, that is why — run `./azure-deploy/fix-ssh-ip.sh`. CI is unaffected; it
  goes through `az vm run-command`, not SSH.

- **CI on this fork fails on a test unrelated to it.**
  `tests/tui_gateway/test_slash_worker_mcp_discovery.py` fails here (3 of 4
  runs; the other 2581 tests pass) even though the fork changes no Python. The
  deploy deliberately does not wait for CI: what protects production is the
  health check on the real container, which rolls back to the previous image
  within seconds if the new one does not come up. CI on a runner is a weaker
  proxy for that, and gating on it only blocked every release.

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
