# systemd/

Service unit templates installed by `install.sh`.

| Template | Installed path | Purpose |
|---|---|---|
| `pm2-eve.service.template` | `/etc/systemd/system/pm2-eve.service` | Brings up PM2 (which manages ollama, cloudflared, sharedbrain, whatsapp-bridge) at boot |

## PM2 services per box (registered separately, not in this template)

`install.sh` Phase H-J deploys the source for each PM2 process. The customer-onboarding dashboard (Phase 2.5) calls `pm2 start ...` for each, then `pm2 save` to persist for the next `pm2 resurrect`.

Reference startup commands (run by the dashboard, not by this systemd unit):

```bash
pm2 start /home/eve/.local/bin/ollama --name ollama -- serve
pm2 start "cloudflared tunnel run <tunnel-name>" --name cloudflared --interpreter bash
pm2 start /home/eve/sharedbrain/server.js --name sharedbrain
pm2 start /home/eve/whatsapp-mcp/whatsapp-bridge/whatsapp-bridge --name whatsapp-bridge
pm2 save
```

Tunnel name + tunnel ID are *customer-layer* — the dashboard handles cloudflared auth + tunnel creation per customer.

## Enabling

```bash
sudo cp systemd/pm2-eve.service.template /etc/systemd/system/pm2-eve.service
sudo systemctl daemon-reload
sudo systemctl enable --now pm2-eve
# Also enable linger so PM2 survives logout:
sudo loginctl enable-linger eve
```

`install.sh` does *not* auto-enable this (the dashboard does, after the user confirms their plan and connects credentials).
