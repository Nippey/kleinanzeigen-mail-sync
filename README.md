# Kleinanzeigen Mail Sync — v0.4.2
Initial local archive, no IMAP writes. Unofficial API; automated account access may violate terms and lead to suspension. Reading chats may alter read status. Review upstream https://github.com/monkrel/kleinanzeigen-api before enabling. Authentication and sample payload fields were verified on a live account; full backup remains untested. API pagination and actual payload fields need validation; completeness is not guaranteed. Raw snapshots contain the library-returned data, not necessarily full server responses or attachment binaries.

## Unraid
Create a GitHub repository named `kleinanzeigen-mail-sync` and upload the project files (including hidden directories). Set the default branch to `main`. The workflow in `.github/workflows/docker-build.yml` builds and tests the image and publishes it to GHCR on pushes to main or version tags. No personal credentials are required in GitHub Actions. Check the Actions tab for success. GHCR packages may initially be private even when the repository is public: explicitly change package visibility to public for anonymous pulls, or authenticate on Unraid with a read:packages token using `docker login ghcr.io`. Never put the token in Compose or GitHub.

In Unraid Compose Manager create a stack and paste `compose.yaml`, replacing `DEIN-GITHUB-NAME` with the lowercase GitHub owner (and repository name if different). No checkout is needed. The commands below are run from the Compose stack directory. Copy only the example configuration to Unraid or create it manually from its contents.

```sh
mkdir -p /mnt/user/appdata/kleinanzeigen-mail-sync/data/auth
cp config.example.env /mnt/user/appdata/kleinanzeigen-mail-sync/config.env
chmod 600 /mnt/user/appdata/kleinanzeigen-mail-sync/config.env
chown -R 99:100 /mnt/user/appdata/kleinanzeigen-mail-sync/data
chmod 700 /mnt/user/appdata/kleinanzeigen-mail-sync/data /mnt/user/appdata/kleinanzeigen-mail-sync/data/auth
docker compose pull
docker compose run --rm archive kleinanzeigen-api login
```
Open the printed URL in your browser and paste the resulting redirect URL into the local terminal, never GitHub or chat. Edit the external config.env locally: set ENABLE_LIVE_API=yes only if you accept the risk. Then `docker compose up -d` or start the stack in Compose Manager. `docker compose logs --tail=50 archive` shows counts, not messages. If an error occurs the worker waits for a manual restart. To stop: `docker compose down`.

Data are stored outside the checkout in SQLite. Back up appdata securely; it contains private messages and authentication tokens. Restrict access to the Unraid share. Never commit the database, configuration or tokens. No ports are exposed. The container runs as UID 99/GID 100. The upstream version is pinned; transitive packages and base image are not digest-locked.

## Local preview
Stop the stack, set OWN_EMAIL in external config, then `docker compose run --rm -e MODE=preview archive`. Generated EML files are under data/preview. This is NOT an IMAP comparison. Message IDs are read from raw.messageId; records without IDs remain in snapshots. Attachment metadata is saved; supported HTTPS attachment URLs are downloaded in backup mode. Pseudo-addresses use .invalid to prevent accidental sending. Outlook thread grouping is experimental. Preview EML does not yet embed attachments. IMAP writes and historical deduplication are intentionally disabled pending the GDPR export and real sample data. No messages are sent or deleted by this code.

## Update
After a successful GitHub Actions run: `docker compose pull` then `docker compose up -d`. For reproducible deployments, replace `latest` with a published version tag or image digest. Inspect changes before deploying. Do not enable an unattended update mechanism for this service.

## v0.4 incremental backup
Existing database is migrated additively. The first run scans all chats. Later runs skip chats with unchanged latest-message fingerprints until FULL_SCAN_SECONDS (default 86400) has elapsed since their last fetch. Missing fingerprints trigger full fetches. No remote deletes are propagated. Existing configs require no changes. DOWNLOAD_ATTACHMENTS defaults to yes; set no to disable binary downloads. Files are stored by SHA-256 under data/attachments; metadata and links reside in SQLite. Only direct HTTPS kleinanzeigen.de URLs are accepted; redirects, HTML and files over 50 MiB are rejected. Authenticated requests use the library headers only for the exact api.kleinanzeigen.de and gateway.kleinanzeigen.de hosts; redirects are refused. gatewayUrl is preferred, with url as fallback for HTTP errors other than transient server/rate-limit errors. Pending attachments in visible chats are retried on subsequent runs using fresh chat metadata. The two authenticated endpoints were verified on one live JPEG; the integrated downloader has only been tested offline. No IMAP writes.

## Startup diagnostics

Each startup logs the application version, Git commit (injected by CI at build time), mode, effective scan intervals and attachment download setting. Local builds without the build argument show `unknown`. No credentials are logged. Compare the commit with the GitHub Actions run after pulling and recreating the container.
