# Phone and computer

## Android: Immich app (private automatic backup)

1. Install **Tailscale** and log in, then install **Immich** from Google Play or F-Droid.
2. Server URL: `https://myserver.tailnet-name.ts.net:8443`. Log in with **your** account.
3. **Backup** (cloud icon) → select the albums/folders to back up: usually *Camera*, *Screenshots*, and folders of apps whose pictures you want (for example a scanner app). Everything you upload is private to your account.
4. In the backup settings: enable background backup, and choose **Wi-Fi only** and, if you like, **only while charging**.
5. Android settings → Apps → Immich → Battery → **Unrestricted** (otherwise Android stops the background backup). Do the same for Tailscale.

### Sending a photo to the agent

Open the photo in the Immich app → **⋯** or the share/add menu → **Add to album** → `Nextcloud`. It appears in the agent's folder within about a minute and a half. To take it back, remove it from the album.

## Android: Nextcloud app

Install it for your files if you want them on the phone, but **turn off "Auto upload"** in its settings: Immich already backs up your photos, and two backups of the same pictures would waste space.

## Your computer, normal account

- **Nextcloud Desktop** (`sudo apt install nextcloud-desktop`): log in with your daily account at `https://myserver.tailnet-name.ts.net` and choose which folders to sync. Don't sync the agent's folder here unless you want a copy; you can see it in the web UI anyway.
- **Immich CLI or immich-go** for uploading existing photo archives: see [migration.md](migration.md).

## Your computer, agent user

`scripts/create-agent-user.sh` sets this up; nothing to do by hand.

- `nextcloudcmd` (the command-line version of the Nextcloud Desktop client, same sync engine) runs every 30 seconds as a systemd **user** timer of the agent user, also when nobody is logged in (lingering is enabled). It logs in as `ai-agent` with the app password in `~agent/.netrc` and syncs only the shared folder, in both directions.
- Check it: `sudo -iu agent`, then `systemctl --user status cloudbuster-agent-sync.timer` and `journalctl --user -u cloudbuster-agent-sync -n 20`.

### Starting the agent

```bash
cd ~
sudo -iu agent
cd ~/agent-folder
claude
```

`sudo -iu` starts a clean login shell as the agent user: none of your environment variables, groups or rights come along. The `claude` alias in the agent's `.bashrc` loads the root-owned settings in `/etc/cloudbuster-agent/claude-settings.json`. Type `exit` to return to your own user.
