# Perch

A small GTK 4 / libadwaita client for X — timelines, post pages with reply threads and edit history, user profiles with follow / unfollow and followers / following lists, editing your own profile (name, bio,
location, website, photo, header), your likes, highlights, removing followers, muting and blocking people (with
muted / blocked account lists), who to follow, who reposted or quoted a post (and who liked
your own — X keeps likes private otherwise), search (posts and people), bookmarks, lists, like / repost / quote / bookmark, posting
and replying with images (with alt text), threads, drafts, translating posts, scheduling posts (and cancelling them), deleting your own posts, swipeable galleries for multi-media posts, an in-app viewer for photos (with zoom), videos and GIFs, notifications (all, or just mentions), a "new posts" pill on Following, closing to the dock (the window minimizes; Perch keeps running), an unread count on the dock icon (docks that show counts, such as Dash to Dock), remembering the window size and last tab, following the system's light / dark switch live (including theme tools such as Rewaita that rewrite `~/.config/gtk-4.0/gtk.css`), several accounts (switch from the main menu), lists pinned as tabs, muted words, direct messages (with replies, reactions, GIFs, an unread filter, photos, earlier
history and deleting conversations), keyboard navigation (j / k / l / r, Ctrl+? for the list), desktop notifications (polled every
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
  chat-xdk, not bundled). A conversation opens with its newest ~200 messages; "Load Earlier
  Messages" pages back through X's REST history (`dm/conversation/<id>.json`). Reading in Perch
  marks the conversation read on X (sends a read receipt if you have them enabled).
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
never log out of. It is stored with libsecret (Secret portal inside Flatpak), one entry per account
(keyed by the X user id). Main menu → **Add Account…** signs in to another account; the menu then
offers **Switch to @…**, and **Sign Out** forgets the current account only. Drafts, pinned lists and
the notifications "seen" marker are kept per account.

Treat the key like a password: it is full access to the account. Using it is against X's terms; the
account can be rate limited or locked.

## Caching

The sidecar keeps read results in memory for a short while (about a minute for timelines, profiles
and posts; 15 s for DMs and notifications; longer for older pages), so moving around the app doesn't
refetch from X. Refresh (Ctrl+R / F5, the refresh buttons, Try Again) and the 2-minute poller always
ask X. Your own likes, reposts and bookmarks are applied to cached copies; other writes drop the
cached results they affect. If X rate limits or the network is down, Perch shows its last copy (up to
30 minutes old) and says so. That cache is memory only; downloaded images (avatars, photos, DM
photos) are kept on disk in `~/.cache/perch/images`, trimmed to 300 MB. Rules and tests: `sidecar/src/cache.js`,
`npm --prefix sidecar test`.

## Development

```sh
npm --prefix sidecar ci && npm --prefix sidecar run build
PERCH_DEMO=1 python3 -m perch            # offline fixtures, no account needed
PERCH_API_KEY=... python3 -m perch       # skip the keyring
PERCH_AUDIO_SINK="fakesink sync=true"    # play Spaces silently while testing
PERCH_DEBUG_RPC=1                        # log sidecar calls (method + ids only) to stderr
PERCH_KEYRING=memory                     # keep keys in memory only (with PERCH_DEMO=1: keys demo-1, demo-2 for account tests)
```

Flathub note: the sidecar module uses network during the build (`npm ci`). Publishing to Flathub would
need `flatpak-node-generator` to turn `package-lock.json` into offline sources.
