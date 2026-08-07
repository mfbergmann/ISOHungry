# Importing DVD special features into Plex

*Fork addition. Not part of upstream ISOHungry.*

Upstream archives a disc as a single ISO. Plex [cannot read ISO, IMG, VIDEO_TS
or BDMV at all](https://support.plex.tv/articles/200264956-iso-img-and-video-ts-movie-files/),
so an archived disc is invisible to the library it was ripped for.

`extras-import` turns that archive into per-extra MKVs inside the movie folder
Radarr already manages:

```
/data/media/movies/Alien³ (1992) {tmdb-8077}/
├── Alien³ (1992) {tmdb-8077} Bluray-1080p.mkv     <- untouched
└── Featurettes/
    ├── Optical Fury.mkv                            <- imported from the DVD
    └── Tales of the Wooden Planet.mkv
```

The ISO stays where it is. This reads from it; it never modifies or replaces it.

## The main feature is not imported by default

The common case is already owning a better copy of the film — a Blu-ray remux or
a 4K release — and wanting only the featurettes the DVD carries. The feature is
recorded in the plan with `include: false`; flip it to `true` if you do want it,
and it lands beside the existing file rather than inside `Featurettes/`.

## Two steps, because DVD titles have no names

A DVD title is a number and a duration. Nothing on the disc says which one is
"Deleted Scenes". No tool can label them correctly on its own, so naming is a
step you do once, in a file, before anything is encoded.

```bash
docker exec -it isohungry extras-import scan "/output/movies/ALIEN_3.iso"
```

That writes `ALIEN_3.iso.extras.json` next to the ISO and prints what it found:

```
Reading titles from ALIEN_3.iso ...
  14 titles: feature 1h54m22s, 6 candidate extras, 7 skipped
  Radarr match: Alien³ (1992) [tmdb-8077] confidence 1.00
  -> /data/media/movies/Alien³ (1992) {tmdb-8077}/Featurettes/

   title  3     4m12s   1 ch  -> Featurette 01 (4m12s)
   title  4    15m01s   3 ch  -> Featurette 02 (15m01s)
  skipped:
   title  2  1h54m22s  close to feature length (alternate cut or play-all)
   title  9     0m12s  under 60s (logo or menu loop)
```

Edit the `name` fields to the real titles (from the disc's menu or its back
cover), set `include: false` on anything unwanted, then:

```bash
docker exec -it isohungry extras-import apply "/output/movies/ALIEN_3.iso.extras.json"
```

`auto` does both in one pass with generated `Featurette NN` names — fine when
the names genuinely do not matter, useless when they do.

Re-running `apply` skips files that already exist, so an interrupted run is
safe to repeat. `--force` re-encodes over them. `--dry-run` prints the target
paths and writes nothing.

## When the film cannot be identified

Matching refuses rather than guesses. Character similarity alone is not enough
to separate real titles — *matrix* and *master* score 0.67 against each other,
which is close enough to have filed a disc under the wrong film. A match needs
both a high similarity score and at least one whole word in common; short of
that you get the five closest candidates and a prompt:

```
no confident Radarr match for 'THE MATRIX'.
Closest candidates:
  0.67  The Master (2012) [tmdb-68722]  (no word in common)
Re-run with --movie "Exact Title" or --tmdb-id N.
```

Discs whose volume label was too generic to keep (`DVD_VIDEO`, `WB_DVD`) carry
no recoverable title at all, so they always need `--movie` or `--tmdb-id`.

## What gets skipped, and why

| Reason | What it catches |
|---|---|
| under 60s | Studio logos, menu loops, copyright cards |
| close to feature length | Director's cuts, open-matte versions, "play all" chains |
| over 60m | Second features and documentary-length discs |
| duplicate of an earlier title | The same title repeated across VTS boundaries |

Every skipped title is listed in the plan with its reason. Nothing is dropped
silently — if something is missing, the plan says why, and the thresholds are
env-tunable (`EXTRAS_MIN_SECS`, `EXTRAS_MAX_SECS`, `EXTRAS_FEATURE_RATIO`).

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `RADARR_URL` | `http://radarr:7878` | Source of truth for movie folder names |
| `RADARR_API_KEY` | — | Required |
| `PLEX_URL` / `PLEX_TOKEN` | — | Targeted rescan after import; skipped if unset |
| `EXTRAS_SUBDIR` | `Featurettes` | Any [Plex extras folder name](https://support.plex.tv/articles/local-files-for-trailers-and-extras/) |
| `EXTRAS_ENCODER` | auto | `nvenc_h265` when the GPU is reachable, else `x265` |
| `EXTRAS_QUALITY` | `22` | HandBrake RF |
| `EXTRAS_MATCH_THRESHOLD` | `0.78` | Below this, refuse rather than guess |
| `EXTRAS_UID` / `EXTRAS_GID` | `99` / `100` | Ownership of written files |

Radarr must be mounted the same way in both containers — this one uses the
`path` Radarr returns over the API verbatim, with no rewriting, so
`/mnt/user/data:/data` in both means no path mapping to keep in sync.

Encoder selection probes at runtime: HandBrake only lists the nvenc encoders
when it can actually reach the card, so a broken GPU passthrough degrades to
software x265 instead of failing the import.
