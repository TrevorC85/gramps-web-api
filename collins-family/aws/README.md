# Family Gramps Web on AWS Lightsail (SAM)

One Lightsail Linux server runs the same Docker stack as the local site, with Caddy in front
for HTTPS. No load balancer, no NAT gateway, no IAM users or access keys.

```
Internet ──80/443──► Lightsail static IP ──► Lightsail instance small_3_0 (Amazon Linux 2023, x86_64)
                                              ├─ caddy        (Let's Encrypt, :80/:443)  network "web"
                                              ├─ grampsweb    (:5000, not published)    "web" + "backend"
                                              ├─ grampsweb_celery                        "backend"
                                              └─ grampsweb_redis (Valkey)                "backend" (internal, no internet)
                                             /srv/gramps on the instance's own 60 GB SSD
                                             nightly 03:30 America/Chicago ──► Lightsail bucket: backups/latest.tgz
                                                                               (one object, overwritten; Retain)
```

## Files

| File | What it is |
|---|---|
| `template.yaml` | SAM/CloudFormation: Lightsail instance + firewall, static IP, Lightsail bucket (resource access for the instance), optional Route 53 A records (apex + www) |
| `docker-compose.aws.yml` | Compose file: bridge networks, Valkey internal only, Caddy on 80/443, upload patch mounted in grampsweb **and** celery, every image pinned by tag + digest |
| `Caddyfile` | Caddy site config (`SITE_HOSTS`, `DOMAIN_NAME`, `ACME_EMAIL` from the environment); www → apex redirect |
| `bootstrap.sh` | First-boot setup, safe to re-run: Docker + compose plugin (checksum-verified), AWS CLI, 2 GiB swap, data dirs, secret key, loads the latest backup or the seed, systemd units, start |
| `gramps-env.sh` | Run before every start: writes `/run/gramps-web/compose.env` (RAM only, mode 600) and blocks containers from the instance metadata service |
| `backup-s3.sh` | Nightly backup: pauses app + worker, tars `data/`, restarts, checks the archive, replaces `backups/latest.tgz`, deletes the seed once a backup exists |
| `restore-s3.sh` | Restore `latest`, `seed`, or any `s3://…tgz` |
| `switch-image.sh` | Switch to our own image from a checked tarball, or roll back (`update.sh image ...`) |
| `update.sh` | Backup, fetch the pinned commit (or a new SHA/branch/tag given as argument), verify it, re-run bootstrap (restarts the stack) |
| `systemd/` | `gramps-web.service`, `gramps-backup.service`, `gramps-backup.timer` |
| `samconfig.toml.example` | Example deploy settings (copy to the git-ignored `samconfig.toml`) |

## Monthly cost (us-west-2)

Prices checked 18 Nov 2026 on the [Lightsail pricing page](https://aws.amazon.com/lightsail/pricing/),
[Lightsail FAQ](https://aws.amazon.com/lightsail/faq/), [Lightsail bucket FAQ](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-faq-buckets.html),
[Route 53 pricing](https://aws.amazon.com/route53/pricing/) and [S3 pricing](https://aws.amazon.com/s3/pricing/).

| Item | Price | Per month |
|---|---|---|
| Lightsail Linux instance, `small_3_0` (2 GB RAM, 2 vCPU, 60 GB SSD, 3 TB transfer, public IPv4) | $12 USD/mo | **$12.00** |
| Lightsail static IP | free while attached to an instance ($0.005/hour if left unattached for more than 1 hour) | $0.00 |
| Lightsail object storage bucket, `small_1_0` (5 GB storage, 25 GB transfer out) | $1 USD/mo | **$1.00** |
| Backup data: one ~0.21 GB `latest.tgz` (+ the ~0.21 GB seed until the first nightly backup) | inside the 5 GB bucket plan | $0.00 |
| Route 53 hosted zone for the domain (exists already, since the domain was bought in Route 53) | $0.50 per hosted zone | **$0.50** |
| Route 53 DNS queries | $0.40 per million | ≈ $0.00 |
| Data transfer | inside the 3 TB instance allowance (in + out count; nightly upload ≈ 6 GB/month) | $0.00 |
| SAM artifacts bucket (`resolve_s3`) | S3 Standard $0.023/GB; holds a few KB | ≈ $0.00 |
| **Total** | | **≈ $13.50/month** (+ tax; the domain's yearly renewal is separate) |

The bucket choice: a plain S3 bucket would cost about half a cent a month for 0.21 GB
($0.023/GB) instead of $1, so the whole site would be ≈ $12.51. But a Lightsail instance
has no IAM role, so S3 would need a long-lived IAM access key copied onto the server by hand
(and port 22 opened to do it). The Lightsail bucket gives the instance access through
**resource access** with no keys at all, for $1/month more. This template uses the Lightsail bucket.

## Security design

- **Bucket access without keys.** The stack lists the instance in the bucket's
  `ResourcesReceivingAccess`. Lightsail then gives the instance temporary credentials through the
  instance metadata service (the same mechanism the [Lightsail WordPress bucket tutorial](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-connecting-buckets-to-wordpress.html)
  uses with "IAM roles"). No access key exists in the template, parameters, outputs, user data, git or logs.
  The containers can't use those credentials: `gramps-env.sh` adds a `DOCKER-USER` firewall rule
  that rejects container traffic to `169.254.169.254` (and `fd00:ec2::254`).
- **Bucket:** private (`GetObject: private`, no public overrides), versioning off, retained when
  the stack is deleted. Your PC uploads the seed with your own `collins` login.
- **Gramps secret key:** generated on the server at first boot into `/etc/gramps-web/secret-key`
  (root only, mode 600), copied into RAM (`/run/gramps-web/compose.env`, mode 600) at each start.
  It is not in the bucket or the backups. A rebuilt server makes a new one; the only effects are
  that everyone has to log in again and browser push-notification subscriptions have to be renewed.
- **Account passwords** exist only as hashes in the user database (which *is* in the backup).
- **Firewall (Lightsail):** TCP 80, TCP 443, UDP 443 (HTTP/3) from anywhere, IPv4 and IPv6.
  SSH 22 only from the Lightsail console's browser SSH (`SshAccess=lightsail-connect`, the
  default) or not at all (`closed`), plus an optional `SshCidr`.
- **Launch script** (user data) has only non-secret settings (domain, ACME email, bucket name).

## Deploy from Trevor's Windows PC (PowerShell)

Tools: AWS CLI 2.41.1, SAM CLI 1.173.0, profile `collins` (`aws login --profile collins --region us-west-2`).

### 0. Push and check the login

The server fetches `https://github.com/TrevorC85/gramps-web-api` on first boot, so push this
folder first. **Pin the deploy to a commit:** set `RepoBranch` in `samconfig.toml` to the full
40-character SHA of the pushed commit (`git rev-parse HEAD` in the clone after `git push`). The
server fetches exactly that commit and refuses to start (`FATAL` in the bootstrap log) if the
checked-out commit differs. A branch name also works but deploys whatever the branch holds at
boot time. Then:

```powershell
$env:AWS_PROFILE = "collins"; $env:AWS_REGION = "us-west-2"
aws sts get-caller-identity
aws route53 list-hosted-zones-by-name --dns-name collinsfamilytree.net --query "HostedZones[0].[Id,Name]" --output text
```

Put the zone ID (the part after `/hostedzone/`, starting with `Z`) into `HostedZoneId` in
`samconfig.toml` (copy of `samconfig.toml.example`; git-ignored, so your email stays out of git).

### 1. Deploy

```powershell
cd <repo>\collins-family\aws
sam validate --lint
sam build
sam deploy            # reads samconfig.toml; review the change set and answer y
```

Stack names must be lower case and at most 33 characters (the bucket is named
`<stack>-<account id>-backup`). Note the outputs: `StaticIpAddress`, `BackupBucketName`,
`SeedUploadCommand`, `BrowserSshUrl`.

### 2. Upload the seed right away

The new server waits (default 180 minutes, `SeedWaitMinutes`) for the seed and does **not**
start the site until it has data, so nobody can claim an empty site.

Use a **data-only** archive (just `data/`): the local backup also contains the local `.env`
(with the local secret key) and build files, which shouldn't go to AWS. On the box it is
`backups/gramps-seed-data-only-2026-11-18_195045.tgz`; copy it to the PC, then:

```powershell
$bucket = aws cloudformation describe-stacks --stack-name collins-family-gramps --query "Stacks[0].Outputs[?OutputKey=='BackupBucketName'].OutputValue" --output text
aws s3 cp C:\path\to\gramps-seed-data-only-2026-11-18_195045.tgz "s3://$bucket/seed/gramps-seed.tgz"
```

That is the only manual step: no key has to be placed on the server.

### 3. Watch the first boot

Open `BrowserSshUrl` (Lightsail console → instance → Connect) and run:

```bash
sudo tail -f /var/log/gramps-bootstrap.log     # ends with "bootstrap done"
sudo docker ps
```

Installing Docker, pulling ~760 MB of images and restoring takes about 5–15 minutes after
the upload. Caddy then gets the certificate once DNS (`collinsfamilytree.net` and `www`) points
at the static IP: `sudo docker logs caddy`. Check `https://collinsfamilytree.net/`.

### 4. First login and accounts

Log in as **trevor** with the existing password (from the seed). Then:

- **Delete or disable the test Member and Contributor accounts** from the local setup, and
  create one account per family member (Member = view, Contributor/Editor = can add).
  Public registration stays disabled.
- **Password-reset e-mails don't work** (no SMTP configured): set initial passwords yourself.
- Command-line alternative (roles: 1 Member, 2 Contributor, 3 Editor, 4 Owner; the password
  lands in shell history):
  ```bash
  cd /opt/gramps-web/collins-family/aws
  sudo docker compose --env-file /run/gramps-web/compose.env -f docker-compose.aws.yml \
    exec grampsweb python3 -m gramps_webapi --config /app/config/config.cfg \
    user add <name> '<password>' --fullname '<Full Name>' --role 1
  ```

## Backups (one copy)

- `gramps-backup.timer` runs `backup-s3.sh` daily at 03:30 America/Chicago (up to 15 min random
  delay; a missed run happens at next boot). Grampsweb and the worker are stopped for the
  seconds tar needs, so the SQLite files are consistent.
- It writes **one object**, `s3://<BackupBucketName>/backups/latest.tgz`, replacing the previous
  one. Versioning is off, so there is no older copy. Because of that, a new archive replaces the
  old one only if: gzip/tar are intact, the user DB and a tree DB are present, every SQLite file
  passes `PRAGMA quick_check`, and it is at least 50% of the size of the current backup
  (override with `sudo FORCE_BACKUP=1 .../backup-s3.sh` after a deliberate big deletion).
- After the first good backup it deletes the seed object, so only one copy remains.
- In it: `data/` minus `tmp`, `cache`, `thumbnail_cache`, `redis`. Not in it: the secret key, Caddy certificates.
- Run one now: `sudo systemctl start gramps-backup.service && journalctl -u gramps-backup -n 20`
- Copy it to your PC: `aws s3 cp "s3://$bucket/backups/latest.tgz" .`
- A failed backup is only visible in `journalctl -u gramps-backup`; nothing emails you.

## Restore

```bash
sudo /opt/gramps-web/collins-family/aws/restore-s3.sh latest
```

It stops the site, moves current data to `/srv/gramps/data.pre-restore-<time>`, unpacks the
archive's `data/` and starts the site. Delete the `data.pre-restore-*` folder when happy.

## Rebuild the server (lost, broken, or a new bundle/OS)

Data lives on the instance's disk; the bucket's `latest.tgz` is the way back.

1. If the old server still works, take a backup on it first (command above).
2. Change `InstanceGeneration` (e.g. `1` → `2`) in `samconfig.toml` — together with `BundleId`
   if you want a bigger plan (changing `BundleId` or `BlueprintId` alone **fails**: Lightsail
   can't change them in place) — and run `sam deploy`.
3. CloudFormation creates `<stack>-2`, moves the static IP and bucket access to it, then deletes
   `<stack>-1`. The new server restores `backups/latest.tgz` before starting; everyone logs in again.

## Update

- **Config / scripts / patch / pinned images** (after pushing to the branch):
  `sudo /opt/gramps-web/collins-family/aws/update.sh <new 40-character commit SHA>` — backup,
  fetch exactly that commit (refuses if it doesn't match), save it as `REPO_BRANCH` in
  `/etc/gramps-web/env`, re-run bootstrap. Without an argument it re-fetches the deployed ref.
  Also put the new SHA in `samconfig.toml` so a rebuilt server uses the same commit.
- **Newer Gramps Web image:** the upload patch replaces `gramps_webapi/api/file.py` in the image;
  compare the new image's file with `../patches/file.py.upstream` first (see `../patches/README.md`),
  then update tag **and** digest in `docker-compose.aws.yml`.
- **OS packages:** `sudo dnf upgrade --releasever=latest -y && sudo reboot`.
- **Template/launch script:** the launch script runs only on the first boot; edits to it only affect a rebuilt server.

## Our own image (tree link lines)

The tree link lines (relation colour, confidence width, up to four parents) need our forks of
the API and the frontend, so they ship as our own image `grampsweb-collins:26.11.2-<backend7>-<frontend7>`
built by `../image/build.sh` from two committed SHAs (see `../image/README.md`). It is not in any
registry; the server loads it from a checked tarball in the stack's bucket.

1. On the build machine (not the server; the build needs ~6 GB and 10-15 minutes):
   ```
   collins-family/image/build.sh <backend-sha> <frontend-sha>        # prints the tag
   docker save grampsweb-collins:26.11.2-xxxxxxx-yyyyyyy | gzip > img.tgz
   sha256sum img.tgz                                               # note the sum
   tar -xzOf img.tgz manifest.json        # "Config": blobs/sha256/<config digest> = the image ID to pass
   ```
   Upload it from the PC as in "Bucket transfers from the PC" below, to
   `s3://<bucket>/images/grampsweb-collins-26.11.2-xxxxxxx-yyyyyyy.tgz`.
2. On the server, after pushing the deploy commit that contains `switch-image.sh` and checking it out
   (see `RUNBOOK-release-image.md` step 4):
   ```
   sudo /opt/gramps-web/collins-family/aws/update.sh image use \
     grampsweb-collins:26.11.2-xxxxxxx-yyyyyyy s3://<bucket>/images/grampsweb-collins-xxxxxxx-yyyyyyy.tgz <sha256> <image-id>
   ```
   It refuses a tarball whose sha256 or image ID differs, takes a backup (`backup-s3.sh`),
   records the previous image as `PREV_GRAMPSWEB_IMAGE` in `/etc/gramps-web/env`, restarts and
   waits for the API; if the site does not come up it switches back by itself.
3. Roll back at any time: `sudo .../update.sh image rollback` (official 26.11.2 image, or whatever
   ran before). No data restore is needed: the database and user-DB migrations are the same as
   26.11.2. To also undo data changes made since, restore the backup taken in step 2 (see Restore).
4. `update.sh image status` shows the current and previous image.

The upload patch mount stays: our image contains the identical `file.py`, and the official image
needs it after a rollback. Rehearsed locally on a copy (see `../image/README.md`).
Step-by-step release with checks: `RUNBOOK-release-image.md`.

**Rebuilt server:** our image is not in a registry, so leave `GrampsWebImage` empty in
`samconfig.toml` (a new server starts on the official image) and run `update.sh image use …`
again on the new server. A non-empty `GrampsWebImage` on a new server makes compose try to pull
it from Docker Hub and the site does not start.

## Bucket transfers from the PC

The bucket is a Lightsail bucket. The server reaches it through resource access (no keys).
From the PC, two ways work:

- **Your `collins` login** (`aws s3 cp ... --profile collins --region us-west-2`), when that
  login has S3 rights on the bucket (it does today: `aws s3api list-objects-v2 --bucket <bucket>`).
  Always pass `--region us-west-2` (or set `$env:AWS_REGION`); without it the CLI may use another
  region and fail with a redirect or "bucket not found".
- **A temporary bucket access key**, when the login has no S3 rights or for an upload you want
  scoped to this bucket only. Create it, use it in the same PowerShell session without printing
  it, and delete it right after (a bucket can have at most two keys):
  ```powershell
  $bucket = "<BackupBucketName>"
  $k = (aws lightsail create-bucket-access-key --bucket-name $bucket --output json | ConvertFrom-Json).accessKey
  $env:AWS_ACCESS_KEY_ID = $k.accessKeyId; $env:AWS_SECRET_ACCESS_KEY = $k.secretAccessKey
  Remove-Item Env:AWS_PROFILE -ErrorAction SilentlyContinue
  aws s3 cp <file> "s3://$bucket/<key>" --region us-west-2      # or the other way round to download
  Remove-Item Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY; $env:AWS_PROFILE = "collins"
  aws lightsail delete-bucket-access-key --bucket-name $bucket --access-key-id $k.accessKeyId
  aws lightsail get-bucket-access-keys --bucket-name $bucket   # must list none
  $k = $null
  ```
  A new key can take a few seconds before S3 accepts it; retry the copy once if it is refused.

Downloads (`backups/latest.tgz`) work the same way in the other direction.

## Teardown

`sam delete --stack-name collins-family-gramps --region us-west-2 --profile collins` removes the
instance (and all data on its disk), the static IP and the DNS records. **Kept on purpose:** the
backup bucket ($1/month until you empty and delete it in the Lightsail console). A new stack with
the same name can't be created while it exists.
