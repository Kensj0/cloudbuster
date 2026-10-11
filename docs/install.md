# Installation

Follow the steps in order. Each command block says **which Linux user** runs it. "you" means your normal login (with `sudo`); `<agent>` means the agent user (`AGENT_USER` in `.env`).

Do not import any private photos until step 12 has passed.

Example values used below (choose your own in `.env`): agent Linux user `agent`, folder `agent-folder`, Nextcloud accounts `ai-agent` and `nextcloud-sync`, Immich album `Nextcloud`, Tailscale name `myserver.tailnet-name.ts.net`.

## 0. Requirements

- A Linux server or desktop that stays on. Tested on Ubuntu 24.04. 4 GB RAM minimum, 8 GB recommended (Immich's machine learning is the heavy part).
- Enough disk for your photos plus room to grow. A second disk (or USB disk) for backups.
- An Android phone.

## 1. System packages and Docker

As **you**:

```bash
cd ~
sudo apt update
sudo apt install -y git curl openssl rsync nftables python3-yaml nextcloud-desktop-cmd bubblewrap socat
```

Install Docker Engine with the Compose plugin from Docker's own repository: <https://docs.docker.com/engine/install/ubuntu/>.

**Skip** the post-install step "Manage Docker as a non-root user". Your login must **not** be in the `docker` group (see [security-model.md](security-model.md#your-other-agent-sessions)). Check, and remove yourself if needed:

```bash
cd ~
id -nG
sudo gpasswd -d "$USER" docker   # only if "docker" was listed; then log out and in again
```

## 2. Tailscale (access from outside your home)

As **you**:

```bash
cd ~
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

In the Tailscale admin console, enable **MagicDNS** and **HTTPS certificates**. Install the Tailscale app on your phone and log in with the same account. Note your machine's name, e.g. `myserver.tailnet-name.ts.net`.

Prefer your own domain? See "Own domain with Caddy" at the end of this file.

## 3. Get cloudbuster

The repository is owned by `root`, so no agent running as you can change scripts that you later run with `sudo`.

As **you**:

```bash
cd /opt
sudo git clone https://github.com/<owner>/cloudbuster.git
cd /opt/cloudbuster
```

## 4. Private configuration

As **you**:

```bash
cd /opt/cloudbuster
sudo ./scripts/prepare-host.sh          # creates /etc/cloudbuster/.env from the template
sudo nano /etc/cloudbuster/.env
```

In `.env`, replace every `change-me` with its own random value (`openssl rand -hex 24` gives one), and set at least: `TZ`, `NEXTCLOUD_PUBLIC_HOST` (your Tailscale name), `AGENT_USER`, `AGENT_SYNC_DIR`, `OWNER_USER` (your login name). Leave `SYNC_IMMICH_API_KEY` and `SYNC_NEXTCLOUD_APP_PASSWORD` empty for now.

```bash
cd /opt/cloudbuster
sudo ./scripts/prepare-host.sh          # creates the data directories, root-only
```

## 5. Start Immich and Nextcloud

As **you**:

```bash
cd /opt/cloudbuster
sudo ./bin/cb-compose pull
sudo ./bin/cb-compose up -d
sudo ./bin/cb-compose ps                # wait until everything is "healthy" or "running"
sudo tailscale serve --bg --https=443 http://127.0.0.1:8080     # Nextcloud
sudo tailscale serve --bg --https=8443 http://127.0.0.1:2283    # Immich
sudo tailscale serve status
```

(Use your `NEXTCLOUD_PORT` / `IMMICH_PORT` if you changed them.)

## 6. Nextcloud: your account and the folder

1. Open `https://myserver.tailnet-name.ts.net` and log in with `NEXTCLOUD_ADMIN_USER` / `NEXTCLOUD_ADMIN_PASSWORD`.
2. Optional but recommended: create a normal (non-admin) account for daily use and use the admin only for administration.
3. As your daily account, create the folder the agent will get, e.g. `agent-folder` (any name and location).
4. Remove the two `NEXTCLOUD_ADMIN_*` lines from `/etc/cloudbuster/.env` (`sudo nano /etc/cloudbuster/.env`).

## 7. Immich: the sync user and the album

1. Open `https://myserver.tailnet-name.ts.net:8443` and create the admin account (the first account is the admin; this can be you).
2. **Administration → Users → Create user**: `nextcloud-sync`, any e-mail-like login (it is never mailed), a long random password.
3. In a **private browser window**, log in as `nextcloud-sync`:
   - **Account settings → API keys → New API key**, name `cloudbuster`, and tick only: `album.read`, `asset.read`, `asset.download`, `asset.upload`, `albumAsset.create`, `albumAsset.delete`, `user.read`. Copy the key.
   - **Albums → Create album** named exactly as `album_name` in `config.yaml` (default `Nextcloud`).
   - **Share** the album with **your** account and set your role to **Editor**.
   - Do **not** set up partner sharing for this user.
   - Log out.
4. Put the key into `.env`:

```bash
cd /opt/cloudbuster
sudo nano /etc/cloudbuster/.env         # SYNC_IMMICH_API_KEY=<the key>
```

Why the sync user owns the album: [security-model.md](security-model.md#why-the-sync-user-owns-the-album).

## 8. The agent user and the Nextcloud service accounts

As **you**:

```bash
cd /opt/cloudbuster
sudo ./scripts/create-agent-user.sh     # Linux user, folder, Claude Code settings
sudo ./scripts/nextcloud-setup.sh       # ai-agent + nextcloud-sync accounts, sharing locked down
```

`nextcloud-setup.sh` writes the two app passwords directly into the agent's `~/.netrc` and into `.env`. They are never shown.

## 9. Share the folder

In Nextcloud, as **your daily account**: open the folder's **Sharing** tab and share it with `ai-agent` and with `nextcloud-sync`. For both: allow **edit** (create, change, delete) and **untick "Allow resharing"**.

The folder now appears at the top level of both accounts, with its own name (e.g. `/agent-folder`). Put that name in two places:

```bash
cd /opt/cloudbuster
sudo nano /etc/cloudbuster/.env          # AGENT_REMOTE_FOLDER=/agent-folder
sudo nano /etc/cloudbuster/config.yaml   # nextcloud_folder: "/agent-folder"
sudo ./scripts/create-agent-user.sh      # again: now starts the folder sync timer
sudo ./scripts/agent-firewall.sh         # network rules for the agent user
```

## 10. Claude Code for the agent user

As **you**, check that the sandbox can start on Ubuntu 24.04+:

```bash
cd ~
sysctl kernel.apparmor_restrict_unprivileged_userns
```

If it prints `= 1`, add the AppArmor profile for bubblewrap described in the Claude Code docs: <https://code.claude.com/docs/en/sandboxing> ("Ubuntu 24.04 and later").

Then as **`<agent>`** (enter with `sudo -iu agent`):

```bash
cd ~
curl -fsSL https://claude.ai/install.sh | bash   # official installer, see code.claude.com/docs
exit
```

The first time you start `claude` as the agent user it asks you to log in. Use the agent's own login; never copy credentials from your account.

## 11. Start the sync

As **you**, first a dry run (shows what would happen, changes nothing):

```bash
cd /opt/cloudbuster
sudo ./bin/cb-compose --profile sync build sync
sudo ./bin/cb-compose --profile sync run --rm sync --config /config/config.yaml --once --dry-run
sudo ./bin/cb-compose --profile sync up -d sync
sudo ./bin/cb-compose logs -f sync       # Ctrl+C to stop watching
```

## 12. Verify the isolation

As **you**:

```bash
cd /opt/cloudbuster
sudo ./scripts/verify-isolation.sh
```

Every line must say `PASS`. Fix each `FAIL` (the message says what is wrong) and run it again. Warnings mean a check was skipped; read them.

## 13. Test the whole workflow

1. Upload a test screenshot to Immich (web or phone) and add it to the album `Nextcloud`.
2. Within about a minute it appears in your Nextcloud folder, and within another 30 s in the agent folder:

   ```bash
   cd ~
   sudo -iu agent
   cd ~/agent-folder && ls
   claude
   ```

3. Ask the agent to create an image (for example a chart) and a text file in the folder. The image appears in the album; the text file stays in Nextcloud only.
4. Delete the test file in Nextcloud: it leaves the album but stays in your Immich library.
5. Remove a photo from the album in Immich: its file goes to the Nextcloud trash bin.

Now you can migrate your data: [migration.md](migration.md), and set up your phone and computer: [clients.md](clients.md).

## Own domain with Caddy (instead of Tailscale)

Point two DNS names at your home IP, forward ports 80 and 443 to the server, and set in `/etc/cloudbuster/.env`:

```
CLOUDBUSTER_EXTRA_COMPOSE=compose/caddy.yml
NEXTCLOUD_DOMAIN=cloud.example.com
IMMICH_DOMAIN=photos.example.com
NEXTCLOUD_PUBLIC_HOST=cloud.example.com
NEXTCLOUD_OVERWRITE_PROTOCOL=https
```

Then `sudo ./bin/cb-compose up -d`. This exposes both services to the internet: use strong passwords and keep them updated. Tailscale avoids that exposure entirely.
