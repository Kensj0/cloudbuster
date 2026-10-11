# cloudbuster

Leave Google Photos and Google Drive for self-hosted **Immich** + **Nextcloud**, and give a **local AI agent** access to **one folder only**, isolated by the operating system.

```
 phone ──► Immich (private timeline)
              │  you add a photo to the album "Nextcloud"
              ▼
          sync service ◄──► Nextcloud folder  ◄──► nextcloudcmd ◄──► /home/<agent>/<folder>
          (own accounts,                          (agent's own        │
           sees only the album)                    Nextcloud account) ▼
                                                                AI agent (e.g. Claude Code)
                                                                as its own Linux user
```

**Status:** early, phase 1 of 2. Tested against **Immich v3.3.1** and **Nextcloud 35**. Maintained by one person in spare time: expect issues to be answered, not instantly. Read the security model before you trust it with private photos.

## What you get

- `docker-compose.yml`: Immich (official v3 setup, local machine learning) and Nextcloud (PostgreSQL, Redis, cron), each with its own database, bound to `127.0.0.1` only. HTTPS through **Tailscale Serve** (default) or **Caddy** with your own domain.
- **Two-way album sync** (`sync/`): photos you add to one Immich album appear in one Nextcloud folder, and images written into that folder appear in the album. Deletions are restorable by default. SQLite state, loop protection, dry run, and a safety stop for mass deletions. Unit tests run without any server.
- **Agent isolation** (`scripts/`, `agent/`): a dedicated Linux user with no sudo and no Docker, per-user firewall rules (nftables), a Nextcloud account that sees one folder, and Claude Code sandbox settings as a second layer.
- `scripts/verify-isolation.sh`: an automated test that proves the isolation on your machine.
- Backups (`scripts/backup.sh`), an emergency stop (`scripts/revoke-agent.sh`) and a pre-publish secret scan (`scripts/check-before-publish.sh`).

## Security model in one paragraph

Starting an agent in a folder (`cd folder && claude`) is **not** a security boundary: an agent can read outside its start folder and run commands you approve. cloudbuster therefore relies on the operating system. The agent runs as its own Linux user that cannot read the photo, database or config directories (all `root`-only), cannot use Docker (Docker group membership equals root), and cannot open network connections to Immich, the databases or anything else on your machine or LAN, except its own Nextcloud sync client on `127.0.0.1`. Even its Nextcloud account and the sync service's Immich account can only see the one folder and the one album. Full details, including what this does **not** protect against: [docs/security-model.md](docs/security-model.md).

## Install

Follow [docs/install.md](docs/install.md) step by step. The order matters: the isolation is set up and **verified before** any private photo is imported.

1. Prepare the host and private config (outside this repo)
2. Start Immich and Nextcloud, create your accounts
3. Create the agent user, firewall and service accounts
4. Share one folder and one album, start the sync
5. Run `sudo ./scripts/verify-isolation.sh`: everything must pass
6. Only then: migrate from Google ([docs/migration.md](docs/migration.md)) and set up your phone ([docs/clients.md](docs/clients.md))

Day-to-day operation, updates and backups: [docs/operations.md](docs/operations.md).

## Your settings stay out of this repository

All personal values (passwords, keys, domains, user names, paths) live in `/etc/cloudbuster/` on your server (`root`-only). This repository contains only generic code and templates (`.env.example`, `config.example.yaml`, `docker-compose.override.example.yml`). `.gitignore` blocks the private files, and `scripts/check-before-publish.sh` scans the full history before you push.

## Works with other agents

The isolation is agent-agnostic: any local agent run as the agent user gets the same OS-level limits. Claude Code is the documented and tested example (`agent/claude-settings.json`).

## Limitations

- One album and one folder per installation.
- iPhone Live Photos: only the still image is synced (Android motion photos are a single file and sync completely).
- Polling (default every 60 s), not instant.
- Images the agent creates are owned by the sync user in Immich; you see them through the album, not in your own timeline.
- Phase 2 (an interactive terminal installer) is not written yet.

## License

[MIT](LICENSE). Immich and Nextcloud are separate projects under their own licenses; this repository only contains configuration and a small sync tool that talks to their public APIs.
