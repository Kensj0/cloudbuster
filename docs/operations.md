# Operations

All commands run as **you** (with `sudo`) unless stated otherwise.

## Update cloudbuster

```bash
cd /opt/cloudbuster
sudo git pull
sudo ./bin/cb-compose --profile sync build sync
sudo ./bin/cb-compose --profile sync up -d
sudo ./scripts/create-agent-user.sh      # re-applies agent settings if they changed
sudo ./scripts/verify-isolation.sh
```

## Update Immich and Nextcloud

1. Run a backup first (`sudo ./scripts/backup.sh`).
2. **Immich:** read the release notes for breaking changes. Set `IMMICH_VERSION` in `/etc/cloudbuster/.env`. If the release changes the Redis or Postgres images, cloudbuster's `docker-compose.yml` must follow; check this repository for an update first. The sync service uses only a few API endpoints (listed in `sync/cloudbuster_sync/immich.py`); after a major Immich update, run the sync once with `--dry-run`.
3. **Nextcloud:** upgrade one major version at a time (e.g. 35 → 36, never 35 → 37). Set `NEXTCLOUD_VERSION`.
4. Apply:

   ```bash
   cd /opt/cloudbuster
   sudo ./bin/cb-compose pull
   sudo ./bin/cb-compose --profile sync up -d
   sudo ./bin/cb-compose logs --tail 50 nextcloud immich-server sync
   sudo ./scripts/verify-isolation.sh
   ```

Also keep the operating system updated (`sudo apt update && sudo apt upgrade`): the isolation depends on the kernel.

## Backups (3-2-1)

Three copies, on two kinds of media, one off-site:

1. the live data in `/srv/cloudbuster`,
2. `scripts/backup.sh` → `CLOUDBUSTER_BACKUP_DIR` on a **different disk** (database dumps + files + your private config),
3. off-site: set `RESTIC_REPOSITORY` and `RESTIC_PASSWORD_FILE` in `.env` (e.g. a restic repository on a friend's machine, a NAS elsewhere, or cloud storage) and `backup.sh` also runs restic. Keep the restic password somewhere outside the server too, or the backup is useless when the server is gone.

Run it nightly with root's crontab (`sudo crontab -e`):

```
30 3 * * * /opt/cloudbuster/scripts/backup.sh >> /var/log/cloudbuster-backup.log 2>&1
```

Test a restore now and then: load a dump into a throwaway Postgres container and open a few photos from the backup copy.

## Check that the sync works

```bash
cd /opt/cloudbuster
sudo ./bin/cb-compose logs --tail 50 sync
```

Then repeat step 13 of [install.md](install.md). Useful messages:

- `safety stop: N deletions planned`: more than `max_deletions_per_run` files/photos would be removed in one go. Nothing was deleted. If it is intended:

  ```bash
  cd /opt/cloudbuster
  sudo ./bin/cb-compose --profile sync run --rm sync --config /config/config.yaml --dry-run
  sudo ./bin/cb-compose --profile sync run --rm sync --config /config/config.yaml --allow-mass-delete
  ```

- `album ... not found`: the album was renamed or is no longer owned by / shared with the sync user.
- `the album id changed`: you recreated the album. Stop the sync, move `/srv/cloudbuster/sync/sync.db` away, start again; the first run then merges both sides without deleting anything.
- `remove from album refused: no_permission`: the album is not owned by the sync user.

## Emergency: cut the agent off

```bash
cd /opt/cloudbuster
sudo ./scripts/revoke-agent.sh
```

This kills the agent's processes, locks the Linux user, revokes its Nextcloud app passwords and disables the account. Remove the folder share in the web UI too if you want it gone for good.

### Give the agent access again

```bash
cd /opt/cloudbuster
sudo usermod --expiredate '' --shell /bin/bash agent         # your AGENT_USER
sudo ./bin/cb-compose exec -T -u www-data nextcloud php occ user:enable ai-agent
sudo rm /home/agent/.netrc                                     # forces a new app password
sudo ./scripts/nextcloud-setup.sh
sudo ./scripts/create-agent-user.sh
sudo ./scripts/verify-isolation.sh
```

## Before pushing to GitHub, and before making the repository public

As **you**, in your working copy:

```bash
cd ~/code/cloudbuster
mkdir -p ~/.config/cloudbuster
nano ~/.config/cloudbuster/personal-strings.txt   # one per line: your name, domains, Tailscale name, e-mail, IPs, user names
./scripts/check-before-publish.sh
```

It scans the entire git history with gitleaks (<https://github.com/gitleaks/gitleaks>) and for your personal strings, and checks that no private file is tracked. If something is found in the history, do not make the repository public; rewriting history is the only fix.

Deployment is always manual: `git pull` on the server, then the commands above. Nothing deploys automatically.
