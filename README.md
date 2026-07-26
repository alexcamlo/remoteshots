# RemoteShots

RemoteShots is a small, dependency-free screenshot upload page for trusted LAN or VPN use.

It provides a mobile-friendly browser interface with camera/photo selection, desktop drag-and-drop, and clipboard image paste. Timestamped originals are retained while a stable `latest.png` is updated atomically.

## Features

- Mobile camera and photo-library selection
- Desktop drag-and-drop
- Screenshot paste with `Cmd+V` / `Ctrl+V`
- PNG, JPEG, WebP, HEIF, HEIC, and AVIF original validation
- Strict decoding and validation of `latest.png`
- Timestamped, sanitized original filenames
- Atomic writes with restrictive permissions
- Upload, decoded-image, connection, concurrency, task, and memory limits
- Hardened persistent systemd user service
- Python standard library only

## Security model

RemoteShots has **no application-level authentication**. Anyone who can reach its listener can upload files and replace `latest.png`.

Use it only on a trusted LAN or encrypted private network such as Tailscale:

- Bind to one specific private or VPN address.
- Never bind it to `0.0.0.0` on an internet-facing host.
- Never publicly port-forward the service.
- Restrict reverse-proxy DNS, firewall rules, and access lists to LAN/VPN clients.

Publishing this source code does not make a private deployment public, but deploying the listener on a public interface will.

## Requirements

- Python 3.10 or newer
- systemd user manager for the included persistent service
- No third-party Python or system packages

## Run directly

Find a private address assigned to the machine:

```bash
ip -brief address
```

Create the output directory and start the server, replacing the example address:

```bash
mkdir -p ~/screenshots
chmod 700 ~/screenshots

./server.py \
  --bind 192.168.1.50 \
  --port 8484 \
  --output-dir ~/screenshots
```

Open:

```text
http://192.168.1.50:8484/
```

The server rejects wildcard, loopback, and public bind addresses.

## Install the systemd user service

Install the application and unit:

```bash
install -Dm755 server.py \
  ~/.local/lib/remoteshots/server.py

install -Dm644 remoteshots.service \
  ~/.config/systemd/user/remoteshots.service

install -Dm600 remoteshots.env.example \
  ~/.config/remoteshots/environment

mkdir -p ~/screenshots
chmod 700 ~/screenshots
```

Edit the environment file and set an address assigned to the machine:

```bash
${EDITOR:-vi} ~/.config/remoteshots/environment
```

Example:

```ini
REMOTESHOTS_BIND=192.168.1.50
REMOTESHOTS_PORT=8484
```

Enable the service:

```bash
systemctl --user daemon-reload
systemctl --user enable --now remoteshots.service
```

Check whether the user manager persists without an interactive login:

```bash
loginctl show-user "$USER" -p Linger
```

If lingering is disabled, enabling it requires administrator access:

```bash
sudo loginctl enable-linger "$USER"
```

### Changing the port

The service hardening rules allow TCP port `8484`. If `REMOTESHOTS_PORT` is changed, update `SocketBindAllow` in `remoteshots.service` to match, reinstall the unit, and reload systemd.

## Nginx Proxy Manager

For a friendly private hostname such as `screenshots.example.com`, create a private DNS record pointing to Nginx Proxy Manager's LAN or VPN address.

Configure the proxy host with:

- Scheme: `http`
- Forward host: the RemoteShots private address
- Forward port: `8484`
- WebSockets: off
- Asset caching: off
- HTTPS: enabled with a private, wildcard, or DNS-challenge certificate

Advanced configuration:

```nginx
client_max_body_size 52m;
proxy_connect_timeout 10s;
proxy_send_timeout 120s;
proxy_read_timeout 120s;
proxy_set_header X-Forwarded-Proto $scheme;
```

If Nginx Proxy Manager is otherwise internet-accessible, restrict this proxy host with a firewall or access list even when its DNS record is private.

## Output

Uploaded files are stored under the configured output directory:

```text
~/screenshots/YYYYMMDDTHHMMSS.ffffffZ-<name>-<random>.<type>
~/screenshots/latest.png
```

`latest.png` is written through a same-directory temporary file and atomic replacement. Uploads use mode `0600`; the output directory uses mode `0700`.

## Limits

- Original image: 25 MiB
- Generated latest PNG: 25 MiB
- Total request: approximately 51 MiB
- Decoded PNG: 128 MiB
- Concurrent connections: 2
- Per-connection deadline: 90 seconds

The browser converts non-PNG images to PNG for `latest.png`, scaling the longest side to at most 2560 pixels. The timestamped original remains unchanged.

## Service management

```bash
systemctl --user status remoteshots.service
journalctl --user -u remoteshots.service -f
systemctl --user restart remoteshots.service
```

Verify that the listener uses the intended private address:

```bash
ss -lntp | grep ':8484'
```

## Validation

Compile-check the server and validate the unit:

```bash
python -m py_compile server.py
systemd-analyze --user verify remoteshots.service
```

For a PNG upload test, replacing the example address:

```bash
curl -f \
  -F "original=@screenshot.png;type=image/png" \
  http://192.168.1.50:8484/upload

file ~/screenshots/latest.png
```
