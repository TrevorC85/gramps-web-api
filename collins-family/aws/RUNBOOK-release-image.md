# Runbook: switch the live site to our image (tree link lines)

For `https://collinsfamilytree.net` (stack `collins-family-gramps`, instance
`collins-family-gramps-1`, bucket `collins-family-gramps-491141537355-backup`, us-west-2).
Release record: `../image/RELEASES.md`. Run from FROG-PC PowerShell with profile `collins`.
Downtime: the backup (seconds) and one restart (~30-60 s). Nothing is deleted on the server.
Done once on 2026-11-19 (10:50-10:52 CST); see the log at the end.

## 0. Session

```powershell
$env:AWS_PROFILE = "collins"; $env:AWS_REGION = "us-west-2"
aws sts get-caller-identity --query Account --output text        # 491141537355; else: aws login --profile collins --region us-west-2
$bucket   = "collins-family-gramps-491141537355-backup"
$instance = "collins-family-gramps-1"
$tag      = "grampsweb-collins:26.11.2-3103b90-d7bb47e"
$imageId  = "sha256:092bd18b48d9272446590d7816781e3313ef64cf8ae77f66a6d254c5ba89411c"   # config digest (RELEASES.md)
$tgz      = "C:\Users\frog\collins-release\grampsweb-collins-26.11.2-3103b90-d7bb47e.tgz"
$tgzSha   = "0898d7f97f2b6e1d0f4d1838dcbb7f2122a0883ad1fd7f7cb4b9648d2ac2ad63"
$repoSha  = "<40-char SHA of collins-family in TrevorC85/gramps-web-api to deploy>"
$imgKey   = "images/grampsweb-collins-26.11.2-3103b90-d7bb47e.tgz"
(Get-FileHash $tgz -Algorithm SHA256).Hash.ToLower() -eq $tgzSha    # must be True
```

## 1. Nightly backup and site before the change

```powershell
aws s3api head-object --bucket $bucket --key backups/latest.tgz --query "[LastModified,ContentLength]" --output text
```
The timer runs at 03:30 America/Chicago with up to 15 min random delay, so `LastModified` should be
today between 09:30 and 09:45 UTC (03:30-03:45 CST). Older than today = the nightly backup failed:
look at `journalctl -u gramps-backup` on the server (step 3) before going on.

```powershell
curl.exe -s -o NUL -w "%{http_code}`n" https://collinsfamilytree.net/                 # 200
curl.exe -s -o NUL -w "%{http_code}`n" https://collinsfamilytree.net/api/metadata/    # 401 (login needed)
```

## 2. Upload the image with a temporary bucket key

```powershell
$k = (aws lightsail create-bucket-access-key --bucket-name $bucket --output json | ConvertFrom-Json).accessKey
Start-Sleep 10
$env:AWS_ACCESS_KEY_ID = $k.accessKeyId; $env:AWS_SECRET_ACCESS_KEY = $k.secretAccessKey
Remove-Item Env:AWS_PROFILE
aws s3 cp $tgz "s3://$bucket/$imgKey" --region us-west-2 --only-show-errors
aws s3api head-object --bucket $bucket --key $imgKey --region us-west-2 --query ContentLength   # 485268328
Remove-Item Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY; $env:AWS_PROFILE = "collins"
aws lightsail delete-bucket-access-key --bucket-name $bucket --access-key-id $k.accessKeyId
aws lightsail get-bucket-access-keys --bucket-name $bucket --query "length(accessKeys)"      # 0
$k = $null
```
The image tarball (0.49 GB) fits the 5 GB bucket plan; delete it after a later release.

## 3. Get onto the server

Either the browser SSH (Lightsail console → instance → Connect, the documented way), or from the PC
with temporary SSH credentials, opening port 22 to this PC only for the session:

```powershell
$myIp = (Invoke-RestMethod https://checkip.amazonaws.com).Trim()
aws lightsail open-instance-public-ports --instance-name $instance --port-info "fromPort=22,toPort=22,protocol=TCP,cidrs=$myIp/32,cidrListAliases=lightsail-connect"
$d = (aws lightsail get-instance-access-details --instance-name $instance --protocol ssh --output json | ConvertFrom-Json).accessDetails
$kd = New-Item -ItemType Directory -Force "$env:TEMP\ls-ssh"
Set-Content "$kd\key" $d.privateKey -NoNewline; Set-Content "$kd\key-cert.pub" $d.certKey -NoNewline
icacls "$kd\key" /inheritance:r /grant:r "$($env:USERNAME):R" | Out-Null
$ssh = @("-i", "$kd\key", "-o", "CertificateFile=$kd\key-cert.pub", "-o", "StrictHostKeyChecking=accept-new", "$($d.username)@$($d.ipAddress)")
ssh @ssh "sudo docker ps --format '{{.Names}} {{.Image}} {{.Status}}'"
```
The certificate is valid for a short time; run `get-instance-access-details` again if refused.

## 4. On the server: fresh backup, new deploy scripts, switch

`update.sh <sha>` would also re-run the whole bootstrap (packages, restart). The release only needs
the new scripts, so fetch and check out the pinned commit the same way update.sh does, then switch
(one restart):

```bash
set -euo pipefail
SHA=<repoSha>
sudo /opt/gramps-web/collins-family/aws/backup-s3.sh               # fresh backup -> backups/latest.tgz
# core.fileMode=false: bootstrap chmods the scripts to 0755; before commit 9368d53 git saw that
# as local changes and refused the checkout
G="sudo git -C /opt/gramps-web -c core.fileMode=false"
$G fetch -q --depth 1 origin "$SHA"
test "$($G rev-parse FETCH_HEAD)" = "$SHA"
$G checkout -q --detach FETCH_HEAD
test "$($G rev-parse HEAD)" = "$SHA"
sudo chmod 0755 /opt/gramps-web/collins-family/aws/*.sh
sudo sed -i "s|^REPO_BRANCH=.*|REPO_BRANCH='$SHA'|" /etc/gramps-web/env
sudo /opt/gramps-web/collins-family/aws/update.sh image use \
  grampsweb-collins:26.11.2-3103b90-d7bb47e \
  s3://collins-family-gramps-491141537355-backup/images/grampsweb-collins-26.11.2-3103b90-d7bb47e.tgz \
  0898d7f97f2b6e1d0f4d1838dcbb7f2122a0883ad1fd7f7cb4b9648d2ac2ad63 \
  sha256:092bd18b48d9272446590d7816781e3313ef64cf8ae77f66a6d254c5ba89411c
sudo /opt/gramps-web/collins-family/aws/update.sh image status
```
Copy the fresh backup to the PC as a second copy before the switch (the switch's own backup
replaces `latest.tgz`): `aws s3 cp "s3://$bucket/backups/latest.tgz" "C:\Users\frog\collins-release\pre-release-latest.tgz"`.

`switch-image.sh` checks the tarball's sha256 and the loaded image ID (else nothing changes),
backs up again, restarts, waits for the API and checks the container runs the new image; if not,
it switches back to the previous image by itself.

## 5. Checks

From the server: `sudo docker ps`, `sudo docker logs --tail 50 grampsweb`.
From the PC:
```powershell
curl.exe -s -o NUL -w "%{http_code}`n" https://collinsfamilytree.net/                          # 200
curl.exe -s -o NUL -w "%{http_code}`n" https://collinsfamilytree.net/api/metadata/             # 401
curl.exe -s -o NUL -w "%{http_code}`n" "https://collinsfamilytree.net/api/childref-confidence/?person=I0009&generations=2"  # 401 (new endpoint exists, needs login)
```
In the browser:
- **Owner** (trevor): Family Tree → Ancestor Tree of I0009 (the person with a birth and an
  adoptive family): coloured lines, legend, one line to the parent who is in both families; hover
  shows "Also: Birth — 2nd parent family …". Lists and search still work.
- **Guest** (a Guest-role account; create a temporary one under Settings → Users if none, delete it
  afterwards): private people (e.g. living relatives marked private) are not listed or searchable and
  do not appear as cards in the tree; links to them are not drawn.

## 6. Rollback (any time)

```bash
sudo /opt/gramps-web/collins-family/aws/update.sh image rollback   # back to the official 26.11.2 image
sudo /opt/gramps-web/collins-family/aws/update.sh image status
```
No data restore is needed (same database format). To also undo data changes since the release:
`sudo /opt/gramps-web/collins-family/aws/restore-s3.sh latest` restores the backup in the bucket
(the one taken by the switch), or upload `pre-release-latest.tgz` as `backups/latest.tgz` first.

## 7. Close up

```powershell
aws lightsail put-instance-public-ports --instance-name $instance --port-infos `
  "fromPort=80,toPort=80,protocol=TCP,cidrs=0.0.0.0/0,ipv6Cidrs=::/0" `
  "fromPort=443,toPort=443,protocol=TCP,cidrs=0.0.0.0/0,ipv6Cidrs=::/0" `
  "fromPort=443,toPort=443,protocol=UDP,cidrs=0.0.0.0/0,ipv6Cidrs=::/0" `
  "fromPort=22,toPort=22,protocol=TCP,cidrListAliases=lightsail-connect"
aws lightsail get-instance-port-states --instance-name $instance --output table   # 22: lightsail-connect only
icacls "$env:TEMP\ls-ssh\key" /grant:r "$($env:USERNAME):F" | Out-Null   # the key was made read-only
Remove-Item -Recurse -Force "$env:TEMP\ls-ssh"; $d = $null
aws lightsail get-bucket-access-keys --bucket-name $bucket --query "length(accessKeys)"   # 0
```
Put the deployed `$repoSha` in `samconfig.toml` (`RepoBranch`) so a rebuilt server uses the same
scripts; keep `GrampsWebImage` empty there (see README, "Rebuilt server").

## Log of the release on 2026-11-19 (times CST)

- 10:27 nightly backup check: `backups/latest.tgz` written 03:30:58 (timer ran 03:30:24, OK).
- 10:46 image uploaded with a temporary bucket key (485,268,328 bytes); key deleted, 0 keys left.
- 10:47 port 22 opened to the PC's /32 next to `lightsail-connect`; fresh backup 10:47:55
  (sha256 e10a0b17…), copied to the PC as `pre-release-latest-2026-11-19.tgz`.
- 10:48 checkout refused (mode-only local changes from bootstrap's chmod); fixed in 9368d53 and
  checked out with `core.fileMode=false` + chmod.
- 10:49 first `use` refused before any change: the image ID given was the build box's manifest
  digest; the server's ID is the config digest (now in RELEASES.md).
- 10:50-10:51 `use` with the config digest: backup, restart, health OK, container on our image.
- 10:52-10:55 checks: site 200, API 401 without login, owner tree of I0009 (one line to the
  shared parent, legend, no overlaps), people list; temporary Guest account: 993 of 1005 people,
  none of the 12 private people or 3 private families in any API response, private person page
  "Not Found". Temporary account deleted.
- 10:57 port 22 back to `lightsail-connect` only, SSH key files deleted.
