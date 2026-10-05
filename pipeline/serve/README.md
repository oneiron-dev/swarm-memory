# pipeline/serve: the public server

`serve.py` is a stdlib HTTP server on `127.0.0.1:8790`. It serves `--data` (default `$SWARM_WORK/bundle/data`) and `--dist` (default `$SWARM_WORK/web-dist`; `--work <dir>` sets both). The web app reads `./data/` beside it.

```bash
export SWARM_WORK=$W
python3 pipeline/serve/serve.py --check     # serve on a free port, fetch /api/health, / and /data/bundle.json, exit 0 only when all are good
python3 pipeline/serve/serve.py             # http://127.0.0.1:8790/
```

`/api/health` answers 200 even with no bundle; look at its body: `"ok": true` only when `bundle.json` has a manifest, and `"dist": true` only when the app is built. `--check` checks both.

Our own deployment (below) is public at https://swarm-hack-2026.oneiron.dev through a Cloudflare Tunnel. The address it first went out under, a Tailscale Funnel name, still answers: `--moved OLD-HOST=NEW-URL` turns every request for the old name into a 301 to the new one (same path and query; browsers keep the `#` route).

| path | serves |
|---|---|
| `/` | `web-dist/` (the built frontend). A path without a file extension that is not a file falls back to `index.html`. `/assets/*` is cached for a year (Vite hashes the names); everything else is `no-cache`. Until `web-dist/index.html` exists, `/` is a placeholder page with the bundle counts and the manifest's credit line. |
| `/data/...` | `bundle/data/`, read-only. JSON gets `Cache-Control: public, max-age=60`. Responses are gzipped when the client accepts gzip. |
| `/api/health` | `{"ok", "generated", "counts", "dist"}` from `bundle.json`'s manifest. |
| any path, when the request's `Host` (or `X-Forwarded-Host`) is a `--moved` name | `301` to the new address + path + query. |

Only GET and HEAD. Paths are decoded, normalised and resolved, symlinks included, and must stay inside their folder; the check runs on the final path of every read, including a directory's `index.html`, the SPA fallback and the manifest behind `/api/health`. Anything else returns 404 (tested in `tests/test_serve.py`).

## Arch layout (our deployment)

- code: `/home/lexi/calc-nvme/wt/swarm-serve/` (a git checkout of this repo)
- frontend: `/home/lexi/calc-nvme/hackathon-20261004/web-dist/`
- bundle: `/home/lexi/calc-nvme/hackathon-20261004/bundle/data`, a symlink to the current generation in `bundle/gen/`. The server resolves it on every request, so a rebuild swaps it without a restart.
- unit: `~/.config/systemd/user/hk-serve.service` (copy of `hk-serve.service` here; Restart=always, Nice=10). Linger is on for lexi, so it runs without a login.

## Commands (on Arch)

```bash
# install or update the unit, start it
cp /home/lexi/calc-nvme/wt/swarm-serve/pipeline/serve/hk-serve.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now hk-serve
# stop / start / logs
systemctl --user stop hk-serve
systemctl --user start hk-serve
journalctl --user -u hk-serve -n 50
# update the code
git -C /home/lexi/calc-nvme/wt/swarm-serve pull --ff-only && systemctl --user restart hk-serve
# update the frontend: copy a fresh `web/dist` build in place (no restart needed)
rsync -a --delete <checkout>/web/dist/ /home/lexi/calc-nvme/hackathon-20261004/web-dist/
# update the bundle: rebuild (one-rename symlink swap, no restart needed)
/home/lexi/calc-nvme/hackathon-20261004/venv-bundle/bin/python /home/lexi/calc-nvme/wt/swarm-serve/pipeline/bundle/build_bundle.py
# health
curl -s http://127.0.0.1:8790/api/health
```

## Public address (our deployment)

Two front doors, both ending at `127.0.0.1:8790`:

1. **Cloudflare Tunnel** `swarm-hack-2026` → https://swarm-hack-2026.oneiron.dev (DNS: a proxied CNAME to `<tunnel-id>.cfargotunnel.com` in the oneiron.dev zone; ingress set on the Cloudflare side). On Arch it runs as the user unit `~/.config/systemd/user/cloudflared-swarm.service`: `cloudflared tunnel --no-autoupdate run --token-file ~/.cloudflared/swarm-hack-2026.token` (the token file is mode 600 and never leaves the machine).
2. **Tailscale Funnel** https://arch.tail87c1d7.ts.net, the address the submission form carries. `hk-serve.service` passes `--moved arch.tail87c1d7.ts.net=https://swarm-hack-2026.oneiron.dev`, so this name only redirects.

```bash
systemctl --user status cloudflared-swarm hk-serve          # both active
journalctl --user -u cloudflared-swarm -n 20                 # "Registered tunnel connection" x4
curl -sI https://arch.tail87c1d7.ts.net/ | grep -i location  # the redirect
curl -s https://swarm-hack-2026.oneiron.dev/api/health       # the site
# taking it down (after the results): stop the tunnel, then the Funnel
systemctl --user disable --now cloudflared-swarm
tailscale funnel --https=443 off; tailscale serve reset
```

Setting the Funnel up from nothing: `tailscale funnel --bg 8790`; `tailscale funnel status` shows it.

Only the derived bundle and the app are behind either door. The dataset and the pipeline work area are never served.

## Tests

`python3 -m unittest discover -s pipeline/serve/tests`
