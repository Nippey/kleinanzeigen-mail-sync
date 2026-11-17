# Kleinanzeigen Mail Sync — v0.1
Initial local archive, no IMAP writes. Unofficial API; automated account access may violate terms and lead to suspension. Reading chats may alter read status. Review upstream https://github.com/monkrel/kleinanzeigen-api before enabling. No live test has been performed. API pagination and actual payload fields need validation; completeness is not guaranteed. Raw snapshots contain the library-returned data, not necessarily full server responses or attachment binaries.

## Unraid
Clone this repository to a persistent stack directory. Add that directory as a stack in Compose Manager (or run these commands in the terminal from the checkout).

```sh
mkdir -p /mnt/user/appdata/kleinanzeigen-mail-sync/data/auth
cp config.example.env /mnt/user/appdata/kleinanzeigen-mail-sync/config.env
chmod 600 /mnt/user/appdata/kleinanzeigen-mail-sync/config.env
chown -R 99:100 /mnt/user/appdata/kleinanzeigen-mail-sync/data
chmod 700 /mnt/user/appdata/kleinanzeigen-mail-sync/data /mnt/user/appdata/kleinanzeigen-mail-sync/data/auth
docker compose build
docker compose run --rm archive kleinanzeigen-api login
```
Open the printed URL in your browser and paste the resulting redirect URL into the local terminal, never GitHub or chat. Edit the external config.env locally: set ENABLE_LIVE_API=yes only if you accept the risk. Then `docker compose up -d` or start the stack in Compose Manager. `docker compose logs --tail=50 archive` shows counts, not messages. If an error occurs the worker waits for a manual restart. To stop: `docker compose down`.

Data are stored outside the checkout in SQLite. Back up appdata securely; it contains private messages and authentication tokens. Restrict access to the Unraid share. Never commit the database, configuration or tokens. No ports are exposed. The container runs as UID 99/GID 100. The upstream version is pinned; transitive packages and base image are not digest-locked.

## Local preview
Stop the stack, set OWN_EMAIL in external config, then `docker compose run --rm -e MODE=preview archive`. Generated EML files are under data/preview. This is NOT an IMAP comparison. IDs and dates must be supported by the library; unrecognized records remain in snapshots. Pseudo-addresses use .invalid to prevent accidental sending. Outlook thread grouping is experimental. Attachments are not downloaded. IMAP writes and historical deduplication are intentionally disabled pending the GDPR export and real sample data. No messages are sent or deleted by this code.

## Update
`git pull --ff-only` then `docker compose up -d --build`. Inspect changes before deploying. Do not enable an unattended update mechanism for this service.
