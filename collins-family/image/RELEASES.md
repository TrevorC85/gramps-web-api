# Releases of our image

## 26.11.2-3103b90-d7bb47e (tree link lines, one line per parent)

| | |
|---|---|
| Tag | `grampsweb-collins:26.11.2-3103b90-d7bb47e` |
| Image ID (config digest, what `switch-image.sh use` needs) | `sha256:092bd18b48d9272446590d7816781e3313ef64cf8ae77f66a6d254c5ba89411c` |
| ID on the build box (Docker 29, containerd store: manifest digest) | `sha256:f56cbb99c5c91f005050d3279613176fecf1c336733eb8a8dbe69186555de8a1` |
| Size | 1,844,302,527 bytes unpacked (official 26.11.2: 2,811,824,555) |
| Tarball | `grampsweb-collins-26.11.2-3103b90-d7bb47e.tgz`, 485,268,328 bytes (`docker save \| gzip -1`) |
| Tarball sha256 | `0898d7f97f2b6e1d0f4d1838dcbb7f2122a0883ad1fd7f7cb4b9648d2ac2ad63` |
| API fork commit | `3103b90e6bfb37409afebc779a30a32d5d821f22` (TrevorC85/gramps-web-api, in branch collins-family) |
| Frontend commit | `d7bb47ed20b2d1f63a2fb122df682d90e9fca959` (TrevorC85/gramps-web, branch collins-family) |
| Gramps addons | `5c0f1a7b4bf9aa91d71cd19be21e07496924822c` (FilterRules, JSON) |
| Deployed | 2026-11-19 10:51 CST to collinsfamilytree.net, scripts at API fork commit `9368d53410c6a0ecf194fe00b27379ed0ad7107d` |
| Bucket object | `s3://<BackupBucketName>/images/grampsweb-collins-26.11.2-3103b90-d7bb47e.tgz` |

Build again from the pushed commits (labels `collins.backend.sha`, `collins.frontend.sha` must match):

```
git clone -b collins-family https://github.com/TrevorC85/gramps-web-api.git api
git clone -b collins-family https://github.com/TrevorC85/gramps-web.git web
FRONTEND_REPO=$PWD/web api/collins-family/image/build.sh 3103b90e6bfb37409afebc779a30a32d5d821f22 d7bb47ed20b2d1f63a2fb122df682d90e9fca959
```

The rebuilt image has the same sources and pinned Python packages, base images and addons, but
not a byte-identical image ID (file times, apt package updates in Debian trixie between builds).
Ship the recorded tarball above, not a rebuild, unless you re-test the rebuild.

**Image ID:** Docker with the containerd image store (the build box) shows the manifest digest
as the ID; Docker 25 on the server shows the config digest. Pass the config digest: it is
`Config` in the tarball's `manifest.json` (`tar -xzOf <tarball> manifest.json`), which the
sha256 of the tarball already covers.
