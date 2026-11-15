# Perch

A small GTK 4 / libadwaita client for X — timelines, like / repost / quote / bookmark, posting
and replying with images, swipeable galleries for multi-media posts, an in-app viewer for photos (with zoom), videos and GIFs, notifications, direct messages, desktop notifications (polled every
2 minutes while the app is open), and listening to Spaces (live and replays) — built on
[rettiwt-api](https://github.com/Rishikant181/Rettiwt-API) (the same unofficial library
`mfh-dashboard` shells out to via `npx rettiwt-api`).

```
GTK4 + libadwaita UI (Python)  ⇄  JSON lines over stdio  ⇄  Node sidecar (rettiwt-api 7.2.0, esbuild bundle)
```

- `perch/` — the Python app (`window.py`, `timeline.py`, `tweet_row.py`, `sidecar.py` client).
- `sidecar/` — `src/main.js` RPC server (`init`, `me`, `timeline`, `tweet.action`, `tweet.post`,
  `space.details`, `space.stream`, …) + offline demo fixtures.
- DMs: inbox + conversation via rettiwt; sending via the web client's `dm/new2.json` (rettiwt has no
  send). Encrypted XChat messages show as a placeholder (decrypting needs the chat PIN + X's
  chat-xdk, not bundled). Only the newest page (~150 messages) of a conversation loads, and reading
  in Perch marks the conversation read on X (sends a read receipt if you have them enabled).
- Spaces: details come from rettiwt; the HLS audio URL comes from `live_video_stream/status` (signed
  with rettiwt's credential helpers + a browser user agent — X returns 404 otherwise) and plays through
  GStreamer `playbin3` (`perch/spaces.py`). Open one from a tweet's Space card or menu → Open Space….
- `io.github.eboye.Perch.yml` — Flatpak manifest (GNOME 51 runtime; Node 24 copied from the SDK extension).

## Install

```sh
build-aux/build-flatpak.sh --install   # builds Perch.flatpak and installs it for your user
flatpak run io.github.eboye.Perch
```

`Perch.flatpak` is a single installable file (`flatpak install --user Perch.flatpak`); the GNOME 51
runtime is pulled from Flathub if missing.

## Signing in

Perch needs a rettiwt **API key** — a base64 bundle of your X session cookies. Generate one with the
X Auth Helper extension (see the rettiwt README, "Authentication"), ideally from a private window you
never log out of. It is stored with libsecret (Secret portal inside Flatpak).

Treat the key like a password: it is full access to the account. Using it is against X's terms; the
account can be rate limited or locked.

## Development

```sh
npm --prefix sidecar ci && npm --prefix sidecar run build
PERCH_DEMO=1 python3 -m perch            # offline fixtures, no account needed
PERCH_API_KEY=... python3 -m perch       # skip the keyring
PERCH_AUDIO_SINK="fakesink sync=true"    # play Spaces silently while testing
PERCH_DEBUG_RPC=1                        # log sidecar calls (method + ids only) to stderr
```

Flathub note: the sidecar module uses network during the build (`npm ci`). Publishing to Flathub would
need `flatpak-node-generator` to turn `package-lock.json` into offline sources.
