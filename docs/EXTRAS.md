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

## From the web UI

The web UI (port 8080 in the container) is the easier route, and the one that
handles a film the library does not have yet.

A ripped movie ISO shows up under *Recently eaten* with a **needs review**
badge. **Identify & import** reads the disc, ranks it against the films Radarr
manages, and shows what it thinks with the extras it found:

- **A confident match** arrives pre-selected. Confirm it and the extras are
  encoded into that film's `Featurettes/`; the existing film file is untouched.
- **No confident match** pre-selects nothing and shows candidates plus a search
  box. Nothing is written until a film is picked.
- **Not in the library at all** — search finds it on TMDB, marked *not in
  library*. Confirming **adds it to Radarr first**, so Radarr computes the
  folder name, then the main title is encoded in alongside the extras. Radarr
  monitors it from then on and upgrades it whenever a better release appears.

The film is added on the quality profile the library already uses most (across
552 films here that is *01 HD Bluray + WEB (Upgrade)*, not whichever profile
Radarr happens to list first). `RADARR_QUALITY_PROFILE` overrides.

Encoding runs in the background — closing the panel does not stop it, and the
badge tracks the disc through to **in library**.

### Squashed disc labels

ISO9660 volume labels cannot contain spaces, so discs arrive as
`BENDITLIKEBECKHAM_4X3`. Matching compares space-stripped forms too, so a film
already in the library still resolves (`ACLOCKWORKORANGE` → *A Clockwork
Orange*, 0.97).

TMDB's search has no such tolerance — it returns nothing for
`benditlikebeckham` — so for a film **not** in the library the search box is
prefilled with the label and the spaces have to be put back by hand. No
heuristic reliably guesses word breaks, so the UI asks instead of pretending.

## Real names, from TheDiscDb

A DVD title is a number and a duration. The names printed on the box exist
nowhere on the disc in machine-readable form, so left alone this tool can only
call them *Featurette 01*. [TheDiscDb](https://thediscdb.com) is a community
catalogue of exactly that missing information, MIT-licensed at
[github.com/TheDiscDb/data](https://github.com/TheDiscDb/data).

Download it once from the **Disc catalogue** panel (or `discdb sync`). It keeps
a blobless sparse checkout — ~290 MB rather than the full 2.1 GB, since the
cover art and MakeMKV logs are not needed — and condenses it to a ~335 KB index
of the 574 DVDs that carry named titles.

When a disc matches, its extras arrive already named **and already categorised**,
so deleted scenes land in `Deleted Scenes/` and trailers in `Trailers/` rather
than everything being swept into `Featurettes/`.

### Identification is exact, not fuzzy

TheDiscDb keys every disc on a `ContentHash` that is simply an MD5 over the
sizes of the files in `VIDEO_TS`, sorted by name:

```
md5( int64le(size) for each file in sorted(VIDEO_TS) )
```

That is reproducible from a ripped ISO with no disc in the drive, and it is the
disc's own filesystem talking — two rips of the same pressing agree, two
different pressings do not. Before relying on it, the algorithm was checked
against every catalogued disc that ships its file listing: **3639 of 3644
reproduced exactly, including 378 of 378 DVDs.** The five misses are all
Blu-ray/UHD, and two of them are Game of Thrones discs 29 and 30, whose stored
hashes are simply swapped — a data-entry slip, not an algorithm one.

A duration-fingerprint fallback exists for discs whose files were altered in
transit. It is labelled a guess in the UI, because it is one.

Names are mapped onto titles by **runtime, assigned globally best-fit first** —
not by title number, since lsdvd and MakeMKV number discs differently, and not
in scan order, since a 60-second menu loop sitting beside a 61-second featurette
will otherwise steal its name.

### Coverage, and contributing back

TheDiscDb is Blu-ray-first: 574 DVDs against thousands of Blu-rays. Most discs
will miss, and a miss is not a failure — it is the case for filling the gap.

When a disc is not in the catalogue, **Contribute names to TheDiscDb** writes a
submission in the project's own layout under
`/output/.discdb/submissions/data/movie/…`:

```
Bend It Like Beckham (2002)/
├── metadata.json
└── 2003-dvd/
    ├── release.json
    ├── disc01.json          <- ContentHash + named, typed titles
    └── disc01-files.txt     <- the file sizes the hash was taken over
```

Fork the data repo, copy that `data/` tree in, open a pull request. The export
refuses to run while the extras are still called *Featurette 01* — the value
being contributed is the names, and a wrong name in a shared catalogue is worse
than a gap.

## Reading the disc's own menu

When TheDiscDb has nothing, the disc itself still does: every disc with extras
has a menu naming them — that is what a menu is for. **Read names from disc
menu** renders those menus and reads the labels off them.

The names are pixels, not text, so this is OCR and it is a *suggestion*. Names
it produces are badged `ocr` and always ranked below a TheDiscDb hit.

### How the labels are found

A DVD's buttons are not in the IFO. They live in the highlight information
(HLI) of each menu VOBU's navigation pack, and every button carries a screen
rectangle plus an 8-byte VM command. The rectangle is the useful part: it says
where that button's label is drawn, so each label can be cropped and read on its
own instead of OCRing a whole menu and guessing which words belong to which
item.

The crop runs from the left edge of the screen to the button's **right** edge.
Labels are drawn left-aligned and the highlight rectangle tends to sit over the
tail of the text, so anchoring on its right edge keeps the label and excludes
whatever busy video sits beside it.

### Extras are told from setup menus by register, not by reading the words

An extras menu rarely jumps straight to a title. A button stashes *which* extra
was chosen in a general-purpose register and links to a dispatcher that reads
it — a `SetLink` command whose bytes 2–3 are the register and 4–5 the value.

That register is what separates content from furniture. A disc uses one register
for its extras and different ones for audio, subtitle and setup. On the disc
tested:

| Register | Menu | Values |
|---|---|---|
| 2 | Language / setup | 1, 2, 3, 4 |
| 10 | Audio | 10, 20 |
| **7** | **Special features** | 11, 12, 21–24, 31–33, 51–54 |

So the register most content buttons write to is taken as the extras register
and everything else is dropped. This matters more than it sounds: setup menus
are the worst possible contamination, because their options — *YES*, *STOP*,
*Spanish* — are short confident words that survive OCR beautifully, score well,
and shove the real names out of order.

The values are a bonus worth more than the filtering. They are the disc's own
index for each extra, so sorting by them gives the disc's intended order rather
than the order its menus happened to be scanned in.

Two more details cost real debugging time and are worth knowing if you touch
this:

- **`btn_ns` is at HL_GI offset 17, not 16.** Offset 16 is `btn_ofn`, the index
  the group starts at. On a paged menu that reads as 4, 8, 12, 16 … — a
  plausible-looking button count that silently truncates every menu and hides
  the main menu entirely.
- **Scene-selection pages are dropped before rendering.** They are chapter jumps
  into the feature (`JumpVTS_PTT`), they name chapters rather than extras, and a
  paged one carries fifteen buttons — by far the most expensive thing on a disc
  to OCR for no benefit.

### What it is good at, and what it is not

Single-line labels on a quiet background come out clean — *Deleted Scenes*,
*Music Video*. Labels that wrap to two lines over a bright, busy frame do not;
*International Trailer #1* has come back as `International ... ier 1`. Menu text
sits on top of moving video, which is close to the worst case for OCR, and no
amount of thresholding fixes all of it.

### Why not read the subpicture instead?

Tempting, and it was tried: a DVD's subpicture layer is a clean 4-colour bitmap,
which would OCR far better than text sitting on video. The menu VOB does carry
subpicture streams with real content — sixteen packets of 2–3 KB on the disc
tested.

It does not work. A DVD *menu* subpicture is the button **highlight**, displayed
only for the currently selected button and only when the highlight command in
the PCI activates it. ffmpeg's `dvdsub` decoder does not apply menu highlight
palettes, so overlaying the stream renders an empty frame — forty consecutive
frames came back byte-identical and blank. Getting anything out of it would mean
implementing DVD menu subpicture decoding against the HLI palette, and on the
disc tested the labels are drawn in the background video anyway, where the
highlight would not help.

It is also slow: every menu has to be rendered to frames and every button
region OCRed, which is minutes per disc. That is why it is an explicit button
and a background job rather than part of inspecting a disc.

Names are applied to extras **in menu order**, which is the order a disc lists
them and usually — not always — the order of their title numbers. Check the
order before importing; that is what the confirm step is for.

## Two steps from the CLI, because DVD titles have no names

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
| `EXTRAS_FILTERS` | `--detelecine --deinterlace` | Deinterlacing chain — see below |
| `EXTRAS_QUALITY` | `22` | HandBrake RF |
| `EXTRAS_MATCH_THRESHOLD` | `0.78` | Below this, refuse rather than guess |
| `EXTRAS_UID` / `EXTRAS_GID` | `99` / `100` | Ownership of written files |

Radarr must be mounted the same way in both containers — this one uses the
`path` Radarr returns over the API verbatim, with no rewriting, so
`/mnt/user/data:/data` in both means no path mapping to keep in sync.

Encoder selection probes at runtime: HandBrake only lists the nvenc encoders
when it can actually reach the card, so a broken GPU passthrough degrades to
software x265 instead of failing the import.

## Why the GPU looks idle, and why the filters matter more

Measured on one NTSC DVD title, RTX 3060, `nvenc_h265`:

| Filters | Speed | 112 min feature |
|---|---|---|
| `--comb-detect --decomb` | 15.2 fps | ~3.7 h |
| `--decomb` alone | 18.0 fps | ~3.1 h |
| `--comb-detect` alone | 22.7 fps | ~2.5 h |
| **`--detelecine --deinterlace`** (default) | **39.5 fps** | **~85 min** |
| `--detelecine` alone | 55.8 fps | ~60 min |
| no filters | 94.7 fps | ~35 min |

`nvidia-smi` shows the encoder at 0–2% throughout, which looks like the GPU is
not being used. It is — it is just never the bottleneck. Debian's HandBrake
reports `nvdec: is not compiled into this build`, so MPEG-2 decode and every
filter run on the CPU and only the final encode is offloaded. Throwing a bigger
card at this changes nothing; the filter chain is the whole story.

The default inverts telecine rather than masking it. A film shot at 24 fps and
pressed to an NTSC DVD carries 3:2 pulldown, and `--detelecine` undoes that
exactly, restoring true 23.976p — cheaper *and* more faithful than `--decomb`,
which only smooths the combing it finds. `--deinterlace` (yadif) then handles
extras shot on video, which telecine does not describe.

Set `EXTRAS_FILTERS="--detelecine"` for a disc you know is entirely film, or
`EXTRAS_FILTERS="--comb-detect --decomb"` to trade 2.6× the time for
HandBrake's most thorough adaptive handling.
