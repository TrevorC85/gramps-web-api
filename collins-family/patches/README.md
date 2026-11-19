# Local patches to the Gramps Web image

## gramps_webapi/api/file.py (uploads over 10 MiB)

- Image: ghcr.io/gramps-project/grampsweb:latest, gramps-webapi 3.25.2.
- Upstream file sha256: f4ec2906ce6b17fad14aeb68b721bbf7fc2555e1e185617f38ef55f3f6158a9c (`file.py.upstream`).
- Problem: after the 10 MiB rollover, SpooledTemporaryFile.name is an int fd and Werkzeug
  FileStorage.save raises TypeError, so large uploads fail.
- Fix (`file.py.diff`): write the stream with shutil.copyfileobj instead of FileStorage.save.
- Applied by a read-only bind mount in docker-compose.yml for grampsweb and grampsweb_celery
  (added 18 Nov 2026, round 5 item 7). Before pulling a newer image, compare its file.py with
  `file.py.upstream`; if upstream changed, re-apply the diff to the new file or drop the mount
  if upstream fixed the bug.
