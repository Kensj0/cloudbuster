# Migration from Google Photos, Google Drive and local folders

Do this only after `sudo ./scripts/verify-isolation.sh` passes. cloudbuster contains no migration code; it uses established tools.

Keep everything you download (Takeout archives, logs, notes) **outside** the repository, for example in `~/migration/`. It is personal data.

## Google Photos → Immich

1. Go to <https://takeout.google.com>, deselect all, select **Google Photos**, choose `.zip` and the largest file size, and export. Download **all** parts (`takeout-001.zip`, `takeout-002.zip`, ...).
2. Install **immich-go** (<https://github.com/simulot/immich-go>). It reads Takeout's JSON side files and keeps dates, albums, favourites and descriptions. Check its release notes for compatibility with your Immich version before you start.
3. In Immich, as **your own account** (not `nextcloud-sync`): **Account settings → API keys** → create a key with the permissions listed in immich-go's installation guide.
4. Dry run first, then the real import. As **you**:

   ```bash
   cd ~/migration
   immich-go upload from-google-photos --server=https://myserver.tailnet-name.ts.net:8443 \
     --api-key=YOUR_KEY --dry-run takeout-*.zip
   immich-go upload from-google-photos --server=https://myserver.tailnet-name.ts.net:8443 \
     --api-key=YOUR_KEY --session-tag takeout-*.zip
   ```

   Re-running is safe: files already in Immich are skipped.
5. Delete that API key in Immich when you are done.

### Verify before deleting anything at Google

- Compare counts: Google Photos shows the number of items per album and in total (Takeout's archive browser too); Immich shows totals under **Administration → Server stats** and per album.
- immich-go prints a summary of uploaded, skipped and failed files. Re-run with `--include-unmatched` if many files were not matched to their JSON.
- Spot-check old photos (dates and places), videos, and a few albums.
- Keep the Takeout archives on your backup disk for a while after you delete anything at Google.

## Local photo folders → Immich

Use immich-go (`upload from-folder`) or the official Immich CLI (<https://docs.immich.app/features/command-line-interface>). Both skip files already in Immich (same content), so photos you already imported from Takeout are not duplicated.

```bash
cd ~
immich-go upload from-folder --server=https://myserver.tailnet-name.ts.net:8443 --api-key=YOUR_KEY --dry-run ~/Pictures
immich-go upload from-folder --server=https://myserver.tailnet-name.ts.net:8443 --api-key=YOUR_KEY ~/Pictures
```

## Google Drive → Nextcloud

1. Takeout again, this time only **Drive**. Under "Multiple formats", choose Microsoft Office formats (`.docx`, `.xlsx`, `.pptx`) for Docs, Sheets and Slides; Nextcloud Office and LibreOffice open them. Google Forms, Sites and some Drawings cannot be exported as editable files.
2. Unpack it into a temporary folder in `~/migration/`.
3. Move the files into your **own** Nextcloud folder that the Nextcloud Desktop client syncs (see [clients.md](clients.md)), not into the agent's folder. The client uploads them.
4. Compare the number of files and total size before you delete anything at Google.

## Local file folders → Nextcloud

Add the folders to the Nextcloud Desktop client on your own account (Settings → Add Folder Sync Connection), or move the files into its synced folder.
