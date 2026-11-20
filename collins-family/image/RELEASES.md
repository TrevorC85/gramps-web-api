# Releases of our image

## 26.11.2-518761c-0698365 (map: dots sized by the number of events, with counts)

| | |
|---|---|
| Tag | `grampsweb-collins:26.11.2-518761c-0698365` |
| Image ID (config digest, what `switch-image.sh use` needs) | `sha256:2939d202ef9d2ca6febba6f33de0974c107bcba8d61d704b5c4862d11bfa0f1f` |
| Size | 1,281,282,929 bytes unpacked |
| Tarball | `grampsweb-collins-26.11.2-518761c-0698365.tgz`, 517,959,037 bytes (`docker save \| gzip -1`) |
| Tarball sha256 | `01e2398d07b760c5194ac612ec8412d44e3ca03146f6aa6ba0f537ba81dc7ec0` |
| API fork commit | `518761ca1e4696bad62831558c22b90e46cb5973` (unchanged; TrevorC85/gramps-web-api, branch collins-family) |
| Frontend commit | `0698365608dee3c960b813da1eae7c4fc16b875a` (TrevorC85/gramps-web, branch collins-family) |
| Gramps addons | `5c0f1a7b4bf9aa91d71cd19be21e07496924822c` |
| Config | unchanged (`GRAMPSWEB_GUEST_HOME_PERSON: "I0016"`, `GRAMPSWEB_LIVING_FIRST_NAME_ONLY: "True"`) |
| Deployed | 2026-11-20 12:40 CST to collinsfamilytree.net, scripts at API fork commit `518761ca1e4696bad62831558c22b90e46cb5973` |
| Bucket object | `s3://<BackupBucketName>/images/grampsweb-collins-26.11.2-518761c-0698365.tgz` |

What it does (frontend only): one dot per place whose radius grows with the square root of the
number of events there that match the active filters (9 px for 0-1 events, 26 px at 100+); from
two events the count is drawn in white with a font of the map style itself (Noto Sans Bold on
the base maps, OpenHistorical Bold on the historical map; no counts if a style has no glyphs).
Larger dots are drawn under smaller ones so small dots stay clickable. Tapping a dot opens a
popup listing who had which event there and when: on the regular map loaded from
`/api/events/` for the signed-in user (so guests only see and count what the API shows guests),
with a "Show place" button for the old place card; in Show ancestors grouped by generation. The
list scrolls inside the popup and the map pans so the popup is not under the search panel.

## 26.11.2-518761c-6ae7bc0 (map: "Show ancestors" of the selected person)

| | |
|---|---|
| Tag | `grampsweb-collins:26.11.2-518761c-6ae7bc0` |
| Image ID (config digest, what `switch-image.sh use` needs) | `sha256:0011e96cc892d14f81ddd8352ac11a0b54fa8bab81f1648f17e67b830261a704` |
| Size | 1,281,274,756 bytes unpacked |
| Tarball | `grampsweb-collins-26.11.2-518761c-6ae7bc0.tgz`, 517,983,775 bytes (`docker save \| gzip -1`) |
| Tarball sha256 | `ca88bd7b0c241410c74230b425e0174f144b7345119ca1a195ec27365c1c2782` |
| API fork commit | `518761ca1e4696bad62831558c22b90e46cb5973` (TrevorC85/gramps-web-api, branch collins-family) |
| Frontend commit | `6ae7bc07645ba6b4fac7e96af57cf9c66debda3b` (TrevorC85/gramps-web, branch collins-family) |
| Gramps addons | `5c0f1a7b4bf9aa91d71cd19be21e07496924822c` |
| Config | unchanged (`GRAMPSWEB_GUEST_HOME_PERSON: "I0016"`, `GRAMPSWEB_LIVING_FIRST_NAME_ONLY: "True"`) |
| Deployed | 2026-11-20 10:18 CST to collinsfamilytree.net, scripts at API fork commit `518761ca1e4696bad62831558c22b90e46cb5973` |
| Bucket object | `s3://<BackupBucketName>/images/grampsweb-collins-26.11.2-518761c-6ae7bc0.tgz` |

What it does: new read-only endpoint `GET /api/people/<handle>/ancestor-places?generations=N`
(N = 0..30, default 30 = all) walks the parent families (two per person, loops and repeats
counted once at the nearest generation) and returns each ancestor event's place, coordinates,
type, year, people and generation, plus counts of ancestors, events without a place and places
without coordinates. It reads through the same restricted database as the rest of the API:
private people/events/places are left out, and for guests living people are first-name stubs
without events. On the map, the selected-person card has a "Show ancestors" switch with a
generations picker (2, 3, 5, 10, All); pins are coloured by generation with a legend, popups list
the people and events per place, the map fits to the pins (folding the card on phones), and the
year and event-type filters still apply. No data is changed.

## 26.11.2-ad586bb-1c78d2f (guest home person: guests always start at James, I0016)

| | |
|---|---|
| Tag | `grampsweb-collins:26.11.2-ad586bb-1c78d2f` |
| Image ID (config digest, what `switch-image.sh use` needs) | `sha256:31212a44e1265547aa3f55399374b5fa82146da4210fe9ddbef516c20a9472e5` |
| Size | 1.28 GB unpacked |
| Tarball | `grampsweb-collins-26.11.2-ad586bb-1c78d2f.tgz`, 517,984,677 bytes (`docker save \| gzip -1`) |
| Tarball sha256 | `27712df4d55f054568cdca14bd30da65a2707863f74382ddd34e0929e9c51fcf` |
| API fork commit | `ad586bb57364ed7e16c5c1c9a908feccaf81575a` (TrevorC85/gramps-web-api, branch collins-family) |
| Frontend commit | `1c78d2fbe56f32686a8ab446dd253e8f9ed43de1` (TrevorC85/gramps-web, branch collins-family) |
| Config | `GRAMPSWEB_GUEST_HOME_PERSON: "I0016"` (with `GRAMPSWEB_LIVING_FIRST_NAME_ONLY: "True"`) in `aws/docker-compose.aws.yml` |
| Deployed | 2026-11-19 19:16 CST to collinsfamilytree.net, scripts at API fork commit `ad586bb57364ed7e16c5c1c9a908feccaf81575a` |
| Bucket object | `s3://<BackupBucketName>/images/grampsweb-collins-26.11.2-ad586bb-1c78d2f.tgz` |

What it does: `/api/metadata/` returns `guest_home_person` only to Guest-role users (empty
setting = off). The frontend applies it as a forced setting: guests' dashboard, tree and home
button open on it, the "Set Home Person" button and pencil are hidden, and it is never written to
their stored settings. Owner, Editors and Members are unchanged. No data or server-side user
settings are changed.

## 26.11.2-5c640ab-0066f1c (guests see living people by first name only, "Hidden" relation)

| | |
|---|---|
| Tag | `grampsweb-collins:26.11.2-5c640ab-0066f1c` |
| Image ID (config digest) | `sha256:07ff9711d778791c0b7dc0d3b6d713e6d54d0c76ccb6f852c183e7f07276f270` |
| Tarball | `grampsweb-collins-26.11.2-5c640ab-0066f1c.tgz`, 471,702,615 bytes |
| Tarball sha256 | `0ae33440e7d691451c6dc134a42738f9d360679b3688776c787205a22dfeea5b` |
| API fork commit | `5c640ab` (image); deploy scripts at `7d37048644fc751d173d2b86a15707f52dfc1845` (adds `GRAMPSWEB_LIVING_FIRST_NAME_ONLY`) |
| Frontend commit | `0066f1ca` (TrevorC85/gramps-web, branch collins-family) |
| Deployed | 2026-11-19 13:32 CST to collinsfamilytree.net |
| Bucket object | `s3://<BackupBucketName>/images/grampsweb-collins-26.11.2-5c640ab-0066f1c.tgz` |

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
