# Syncpick

Selective sync for Syncthing. A small web UI that turns a shared folder into a
pick-list: you see the full catalogue that your other devices have, tick the
directories you want on this device, and untick them when you are done.

Syncthing itself stays untouched. Syncpick works by managing the folder's
`.stignore` through Syncthing's REST API, which is the mechanism Syncthing
already provides for keeping part of a folder off a device.

## How it works

- The folder is shared normally on the other devices. On this device, Syncpick
  writes a whitelist ignore file: one `!/path` line per selected directory,
  followed by `*`.
- Syncthing still sends this device the folder's full index, so the catalogue
  with sizes comes from `GET /rest/db/browse` on the local Syncthing. No
  access to the other devices is needed.
- Selecting a directory adds its negation and asks Syncthing to rescan; the
  files are pulled. New files that appear inside a selected directory arrive on
  their own.
- Deselecting removes the negation first, waits for the rescan, and then
  deletes the local copy. Ignoring alone never deletes anything in Syncthing.
- Syncpick only edits a clearly marked block at the end of `.stignore`. Any
  patterns you keep above it are preserved, except a bare `*`, which would
  defeat the whitelist.

## Safety

- Set the folder to **Receive Only** on this device. Then nothing deleted
  locally can ever propagate to other devices, even if something goes wrong.
- Before deleting, Syncpick checks that the `.stignore` it reads from disk is
  the one Syncthing just wrote. If the container's mount does not line up with
  Syncthing's, deletions are refused.
- Every apply goes through a review step that lists exactly what will be
  deleted and how much space that frees.
- `DRY_RUN=true` makes the server report what it would do without writing or
  deleting anything.

## Running it

Syncpick is a single container that sits next to your Syncthing container. See
`docker-compose.example.yml` for a complete service definition. It needs:

| Mount / variable | Purpose |
| --- | --- |
| The folder's data, at the same path Syncthing uses | Local presence, sizes, and deletions |
| Syncthing's config directory, read-only | Reads the API key from `config.xml` |
| `SYNCTHING_URL` | Default `http://syncthing:8384` |
| `SYNCTHING_API_KEY` | Instead of mounting the config directory |
| `FOLDERS` | Comma-separated folder IDs to manage. Default: all |
| `PATH_MAP` | `/remote:/local` pairs if the mount paths differ from Syncthing's |
| `MAX_DEPTH` | How deep the pick-list goes. `2` lets you pick subdirectories inside a directory |
| `PUID` / `PGID` | Run as this user so deletions have the right permissions |
| `PORT` | Web UI port. Default `8080` |
| `DRY_RUN` | `true` to change nothing |

Images are published to GitHub Container Registry for `linux/amd64` and
`linux/arm64` on every push to `main` (`latest`) and every `v*` tag:

```bash
docker compose pull syncpick && docker compose up -d syncpick
```

Or build it yourself with `docker compose build syncpick` if you point the
service at `build: ./syncpick` instead of the image.

Then open the UI, pick a folder, and press **Enable selective sync**. The
initial whitelist is seeded from what is already on disk, so enabling changes
nothing until you deselect something.

## Development

No dependencies beyond Python 3.11+. The dev loop runs a fake Syncthing with a
small sample library so the UI can be tried without a real instance:

```bash
sh tests/dev.sh        # UI on http://127.0.0.1:8080
```

Run the tests with:

```bash
python3 -m unittest discover tests
```

## Prior art

Syncthing's maintainers have declined to add selective sync to the core
program, and every existing tool for it takes the same ignore-pattern route:
[Synctrain](https://github.com/pixelspark/sushitrain) on iOS and macOS,
[syncthing-pyselective](https://github.com/galilley/syncthing-pyselective) as a
desktop app, and [stselect](https://github.com/davidlang42/stselect) on the
command line. Syncpick is the web-UI, runs-in-a-container version.
