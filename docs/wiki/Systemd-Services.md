# Systemd Services & Autostart

The system relies on five systemd user units to guarantee zero-downtime background execution across system reboots.

## Service Roster

| Service Unit | Type | Description |
|---|---|---|
| `omniroute.service` | Simple | Local OpenAI API Router (`:20128`) |
| `hermes-gateway.service` | Simple | Hermes Discord Bot Gateway |
| `mission-control.service` | Simple | Mission Control FastAPI Backend (`:51763`) |
| `hermes-memory-sync.service` | Oneshot | Memory synchronization execution script |
| `hermes-memory-sync.timer` | Timer | 15-minute background memory sync trigger |

## Managing User Services

```bash
# Check status of user services
systemctl --user status mission-control.service omniroute.service

# Restart all services
systemctl --user restart mission-control.service omniroute.service
```
