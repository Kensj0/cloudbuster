# Security model

## Goal

A local AI agent can read and write **one folder**. It must not be able, directly or indirectly, to see the rest of your photos, Immich, other Nextcloud files, the databases, Docker, or any keys, **even if you approve a command that tries to**.

## Why "start the agent in the folder" is not enough

Claude Code (like most coding agents) runs shell commands with the rights of the Linux user that started it, and can read files outside its start folder. If that user can read your photos, so can the agent. If that user is in the `docker` group, the agent can start a container that mounts `/` and read everything: Docker group membership is equivalent to root. The boundary must be one the operating system enforces.

## Layers

| Layer | What enforces it | Protects against |
|---|---|---|
| **1. Linux user** (`AGENT_USER`) | kernel file permissions | Reading photos, databases, `.env`, your home. No `sudo`, no `docker` group, password locked. |
| **2. Directory ownership** | `root:root 0700` on `/etc/cloudbuster` and `/srv/cloudbuster` | Every non-root user, including your own login, can't even list the data. |
| **3. Network** (`scripts/agent-firewall.sh`) | nftables, matched on the socket owner's uid | The agent connecting to Immich, the databases, Docker networks, Tailscale Serve, Caddy, SSH, your LAN. Only `127.0.0.1:NEXTCLOUD_PORT` (its sync client), DNS and the public internet are allowed. |
| **4. Nextcloud account** (`AGENT_NEXTCLOUD_USER`) | Nextcloud | It sees only the one shared folder. It can't share, create public links or look up other users (it is in a group excluded from sharing). |
| **5. Immich account** (sync user) | Immich | The sync service sees only the album and its own uploads. Partner sharing must be off. Its API key has only the 7 permissions it needs. |
| **6. Sync container** | Docker | Only on an internal network with the two web APIs: no internet, no databases. Read-only filesystem, no capabilities, unprivileged uid. |
| **7. Claude Code settings** | Claude Code (bubblewrap sandbox) | A second line: shell commands sandboxed with no network and no reads outside the folder; risky tools denied; secret files in the agent's home (`.env`, `*.db`, `token.json`) denied to Claude's read and edit tools, e.g. for a service that runs as the agent user; bypass and auto modes disabled. |

Layers 1 to 3 are the real boundary. Layer 7 is useful but not relied on: the agent user owns its home directory and could change its own settings, and the sandbox covers shell commands only.

## Why the sync user owns the album

In Immich v3, an **editor** of a shared album can only remove photos *it owns* from that album. If the album were yours and the sync user only an editor, deleting a file in Nextcloud could not remove your photo from the album. So the sync user creates and owns the album and shares it with **you** as editor. You add your photos as before; the sync user still sees nothing else of your library.

Consequence: images that arrive from the folder (for example something the agent created) are uploaded by the sync user. They appear in the album for you, not in your own timeline.

## API key permissions (Immich, sync user)

| Permission | Why |
|---|---|
| `album.read` | Find the album by name |
| `asset.read` | List the album's photos (search confined to the album) |
| `asset.download` | Download a photo to put it in Nextcloud |
| `asset.upload` | Upload a file from the folder |
| `albumAsset.create` | Add an uploaded photo to the album |
| `albumAsset.delete` | Remove a photo from the album when its file was deleted |
| `user.read` | Used by the isolation test to know its own user id |

Not granted, on purpose: `asset.delete` (it can never delete a photo from anyone's library), `album.create`, `albumUser.*`, `partner.*`, anything admin.

## What this does NOT protect against

- **Anything in the shared folder.** The agent can read, change, delete and send anywhere (it has internet access, which it needs for its AI service) everything you put in the folder. Only put things there you are willing to hand to the AI service. Nextcloud's trash bin and versions help you recover from deletions.
- **Root.** Anyone with `sudo` on the machine can read everything. Keep your own account's `sudo` for yourself; see "Your other agent sessions" below.
- **Kernel or container escapes.** A vulnerability in the kernel, bubblewrap or Docker could break the boundary. Keep the system updated.
- **The AI provider.** What the agent reads from the folder is sent to its provider (for Claude Code: Anthropic).
- **Your phone and your accounts.** Phone theft, a weak Immich password, or a compromised Tailscale account are outside this project's scope.
- **A misconfigured Immich.** If you turn partner sharing on for the sync user, it sees your library. `verify-isolation.sh` checks for this.

## Your other agent sessions

If you also run Claude Code (or any agent) under your **normal** login for other projects, that login should **not** be in the `docker` group. With cloudbuster's layout your normal user can't read `/srv/cloudbuster` either. Administer Docker with `sudo ./bin/cb-compose ...` instead. A `sudo` password prompt cannot be answered by an agent, so those sessions also stay away from your photos. Ubuntu caches a `sudo` password per terminal for a few minutes; run agents in a different terminal from your admin work, or set `Defaults timestamp_timeout=0` with `sudo visudo` to turn the cache off.

## Agent in a container instead?

Running the agent in a container that only mounts the folder is also a valid boundary. We chose a dedicated Linux user because:

- the agent works with normal tools and files, without a container image to build and update,
- Claude Code's own sandbox (bubblewrap) works inside a normal user session but needs extra privileges inside a container,
- a container still needs a runtime the user can't control: with Docker that means root-equivalent group membership for whoever starts it, unless you set up rootless Docker or Podman.

If you prefer a container, use rootless Podman under the agent user, mount only the folder, and keep the firewall rules.

## Why not Immich's "External Library" instead of a sync service?

Immich can index a folder on disk as an external library. Pointing it at the Nextcloud folder would be simpler, but:

- it is one-way: adding a photo to an album in the Immich app would not put it in the folder,
- Immich would need read access to Nextcloud's data directory, and files written there directly bypass Nextcloud (it would need a rescan to notice them),
- deleting in one place doesn't map cleanly to "remove from album" in the other.

The sync service talks only to the two public APIs with two restricted accounts, so neither application gets access to the other's data on disk, and the agent's folder stays a normal Nextcloud folder with trash bin and versions.
