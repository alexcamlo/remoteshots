#!/usr/bin/env python3
"""Small private screenshot uploader using only the Python standard library."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import secrets
import signal
import socket
import struct
import tempfile
import threading
import zlib
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

MAX_ORIGINAL_BYTES = 25 * 1024 * 1024
MAX_LATEST_BYTES = 25 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_ORIGINAL_BYTES + MAX_LATEST_BYTES + 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_DECODED_BYTES = 128 * 1024 * 1024
MAX_CONCURRENT_CONNECTIONS = 2
CONNECTION_DEADLINE_SECONDS = 90
NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
WRITE_LOCK = threading.Lock()

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light">
<title>RemoteShots</title>
<style>
:root { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; color: #111; background: #fff; }
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; padding: max(2rem, env(safe-area-inset-top)) clamp(1.25rem, 4vw, 3rem) max(2rem, env(safe-area-inset-bottom)); display: flex; flex-direction: column; background: #fff; }
header { display: flex; align-items: center; justify-content: space-between; font-size: .875rem; }
.brand { font-weight: 700; letter-spacing: -.02em; }
.online { display: flex; align-items: center; gap: .5rem; color: #5e5e59; }
.online::before { width: .5rem; height: .5rem; border-radius: 50%; background: #247a4a; content: ""; }
main { width: min(100%, 640px); margin: auto; padding: 5rem 0; }
h1 { margin: 0; font-size: 2rem; line-height: 38px; letter-spacing: -.035em; }
.intro { margin: .625rem 0 1.875rem; color: #5e5e59; font-size: .9375rem; line-height: 23px; }
.drop { min-height: 9rem; padding: 1.5rem; border: 1px dashed #a8a8a3; border-radius: .25rem; display: flex; align-items: center; gap: 1rem; transition: border-color .15s; }
.drop.active { border-color: #111; background: #fff; }
.upload-icon { width: 1.75rem; flex: 0 0 1.75rem; }
.file-info { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: .2rem; }
#name { overflow-wrap: anywhere; }
.formats { color: #5e5e59; font-size: .8125rem; }
.actions { display: flex; flex-wrap: wrap; gap: .5rem; }
.drop .actions { margin-left: auto; }
button, .button {  appearance: none; min-height: 44px; padding: .75rem 1.125rem; border: 1px solid #111; border-radius: .25rem; background: #111; color: #fff; font: inherit; font-size: .875rem; font-weight: 650; cursor: pointer; text-align: center; }
button.secondary, .button.secondary { background: #fff; color: #111; }
button:focus-visible, .button:focus-visible, .drop:focus-visible { outline: 3px solid #247a4a; outline-offset: 3px; }
button:disabled { opacity: .4; cursor: wait; }
button.btn--full { width: 100%; }
input[type=file] { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0,0,0,0); }
.image__wrapper { width: 100%; display: flex; justify-content: center; }
#preview { display: none; max-width: 100%; max-height: 40vh; margin-top: 1.25rem; border-radius: .25rem; object-fit: contain; transform-origin: center; }
@keyframes preview-suck {
  0% { opacity: 1; transform: translate3d(0, 0, 0) scale(1) rotate(0deg); filter: blur(0); }
  100% { opacity: 0; transform: translate3d(var(--suck-x), var(--suck-y), 0) scale(.06) rotate(8deg); filter: blur(2px); }
}
#preview.preview--sucking { animation: preview-suck 650ms cubic-bezier(.22, .61, .36, 1) forwards; pointer-events: none; }
#uploadActions { flex-direction: column; align-items: stretch; margin-top: .75rem; }
#status { min-height: 1.5rem; margin: .75rem 0 0; font-weight: 650; line-height: 1.5; }
#saved { display: none; margin-top: 1rem; }
#uploadActions > #saved { margin-top: 0; width: 100%; }
#saved.visible { display: block; }
#saved > label { display: block; margin-bottom: .4rem; font-size: .875rem; font-weight: 650; }
.path { display: flex; gap: .5rem; align-items: center; }
#savedPath { flex: 1; min-width: 0; min-height: 44px; padding: .75rem; border: 1px solid #a8a8a3; border-radius: .25rem; background: #fff; color: #111; font: inherit; }
.success { color: #247a4a; } .error { color: #a51d2d; }
footer { color: #5e5e59; font-size: .75rem; text-align: center; }
@media (prefers-reduced-motion: reduce) {
  #preview.preview--sucking { animation: none; }
}
@media (max-width: 40rem) {
  body { padding-top: max(1.25rem, env(safe-area-inset-top)); }
  .online { font-size: .75rem; }
  main { padding: 3.5rem 0; }
  .drop { align-items: flex-start; flex-wrap: wrap; }
  .file-info { flex-basis: calc(100% - 2.75rem); }
  .drop .actions { width: 100%; margin: .5rem 0 0; }
  .drop .button { flex: 1; }
}
</style>
</head>
<body>
<header>
<div class="brand">RemoteShots</div>
<div class="online">Listener online</div>
</header>
<main>
<h1>Upload a screenshot</h1>
<p class="intro">Originals are retained. latest.png updates automatically.</p>
<section id="drop" class="drop" aria-labelledby="name" aria-describedby="dropFormats">
<svg class="upload-icon" viewBox="0 0 28 28" fill="none" aria-hidden="true"><path d="M14 19V5M14 5L9 10M14 5L19 10M5 18V23H23V18" stroke="currentColor" stroke-width="1.8"/></svg>
<div class="file-info">
<strong id="name">Drop an image here</strong>
<span id="dropFormats" class="formats">PNG, JPEG, WebP, HEIF or AVIF</span>
</div>
<div class="actions">
<label class="button" for="picker">Browse</label>
</div>
<input id="picker" type="file" accept="image/png,image/jpeg,image/webp,image/heic,image/heif,image/avif">
</section>
<div class="image__wrapper">
<img id="preview" alt="Selected image preview">
</div>
<div id="uploadActions" class="actions" hidden>
<button id="upload" class="btn--full" type="button" disabled>Upload</button>
<div id="saved">
<label for="savedPath">Local path</label>
<div class="path">
<input id="savedPath" readonly>
<button id="copyPath" type="button">Copy</button>
</div>
</div>
</div>
<p id="status" aria-live="polite"></p>
</main>
<footer>Maximum size 25 MiB · Stored with private permissions</footer>
<script>
const maxBytes = 25 * 1024 * 1024;
const drop = document.querySelector('#drop');
const picker = document.querySelector('#picker');
const uploadActions = document.querySelector('#uploadActions');
const upload = document.querySelector('#upload');
const status = document.querySelector('#status');
const nameEl = document.querySelector('#name');
const preview = document.querySelector('#preview');
const saved = document.querySelector('#saved');
const savedPath = document.querySelector('#savedPath');
const copyPath = document.querySelector('#copyPath');
let selected = null;
let previewUrl = null;
let selectionToken = 0;
let cancelPreviewAnimation = null;

function setStatus(message, kind = '') {
  status.textContent = message;
  status.className = kind;
}
function clearSelection() {
  selectionToken += 1;
  if (cancelPreviewAnimation) cancelPreviewAnimation();
  selected = null;
  saved.classList.remove('visible');
  upload.hidden = false;
  uploadActions.hidden = true;
  upload.disabled = true;
  nameEl.textContent = 'Drop an image here';
  preview.removeAttribute('src');
  preview.style.display = 'none';
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = null;
}
function selectFile(file) {
  if (!file) return;
  if (file.size < 1 || file.size > maxBytes) {
    clearSelection();
    setStatus('Image must be between 1 byte and 25 MiB.', 'error'); return;
  }
  if (file.type && !file.type.startsWith('image/')) {
    clearSelection();
    setStatus('Please select an image file.', 'error'); return;
  }
  if (cancelPreviewAnimation) cancelPreviewAnimation();
  selectionToken += 1;
  selected = file;
  saved.classList.remove('visible');
  upload.hidden = false;
  uploadActions.hidden = false;
  nameEl.textContent = `${file.name} (${(file.size / 1048576).toFixed(2)} MiB)`;
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = URL.createObjectURL(file);
  preview.src = previewUrl;
  preview.style.display = 'block';
  upload.disabled = false;
}
function suckPreviewIntoUpload() {
  const reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reducedMotion) return Promise.resolve();
  const imageRect = preview.getBoundingClientRect();
  const buttonRect = upload.getBoundingClientRect();
  if (!imageRect.width || !imageRect.height || !buttonRect.width || !buttonRect.height) return Promise.resolve();
  const imageCenterX = imageRect.left + imageRect.width / 2;
  const imageCenterY = imageRect.top + imageRect.height / 2;
  const buttonCenterX = buttonRect.left + buttonRect.width / 2;
  const buttonCenterY = buttonRect.top + buttonRect.height / 2;
  const suckX = buttonCenterX - imageCenterX;
  const suckY = buttonCenterY - imageCenterY;
  return new Promise(resolve => {
    let settled = false;
    let timeoutId;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timeoutId);
      preview.removeEventListener('animationend', finish);
      preview.classList.remove('preview--sucking');
      preview.style.removeProperty('--suck-x');
      preview.style.removeProperty('--suck-y');
      if (cancelPreviewAnimation === finish) cancelPreviewAnimation = null;
      resolve();
    };
    cancelPreviewAnimation = finish;
    preview.style.setProperty('--suck-x', `${suckX}px`);
    preview.style.setProperty('--suck-y', `${suckY}px`);
    preview.addEventListener('animationend', finish);
    preview.classList.add('preview--sucking');
    timeoutId = setTimeout(finish, 800);
  });
}
picker.addEventListener('change', () => selectFile(picker.files[0]));
drop.addEventListener('click', event => { if (!event.target.closest('label')) picker.click(); });
['dragenter', 'dragover'].forEach(type => drop.addEventListener(type, event => { event.preventDefault(); drop.classList.add('active'); }));
['dragleave', 'drop'].forEach(type => drop.addEventListener(type, event => { event.preventDefault(); drop.classList.remove('active'); }));
drop.addEventListener('drop', event => selectFile(event.dataTransfer.files[0]));
document.addEventListener('paste', event => {
  const items = Array.from(event.clipboardData?.items || []);
  const imageItem = items.find(item => item.kind === 'file' && item.type.startsWith('image/'));
  if (imageItem) {
    event.preventDefault();
    const image = imageItem.getAsFile();
    if (image) selectFile(new File([image], image.name || `pasted-${Date.now()}.png`, {type: image.type}));
    return;
  }
  const text = event.clipboardData?.getData('text/plain')?.trim();
  if (text) {
    setStatus('The clipboard contains text or a file path, not image data. Configure your screenshot tool to copy the image, or drag the file here.', 'error');
  } else {
    setStatus('No image was found in the clipboard.', 'error');
  }
});

copyPath.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(savedPath.value);
  } catch {
    savedPath.select();
    document.execCommand('copy');
  }
  setStatus('Path copied to clipboard.', 'success');
});

async function imageToPng(file) {
  if (file.type === 'image/png') return file;
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    const maxDimension = 2560;
    const scale = Math.min(1, maxDimension / Math.max(image.naturalWidth, image.naturalHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    canvas.getContext('2d', {alpha: false}).drawImage(image, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    if (!blob || blob.size > maxBytes) throw new Error('PNG conversion failed or exceeded 25 MiB');
    return blob;
  } finally { URL.revokeObjectURL(url); }
}

upload.addEventListener('click', async () => {
  if (!selected) return;
  const file = selected;
  const token = selectionToken;
  upload.disabled = true;
  setStatus('Preparing image…');
  try {
    const latest = await imageToPng(file);
    const form = new FormData();
    form.append('original', file, file.name);
    form.append('latest', latest, 'latest.png');
    setStatus('Uploading…');
    const response = await fetch('upload', {method: 'POST', body: form, credentials: 'same-origin'});
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `Upload failed (${response.status})`);
    if (token !== selectionToken || selected !== file) return;
    savedPath.value = result.original;
    await suckPreviewIntoUpload();
    if (token !== selectionToken || selected !== file) return;
    preview.style.display = 'none';
    upload.hidden = true;
    saved.classList.add('visible');
    setStatus(`Saved ${result.filename}`, 'success');
  } catch (error) {
    setStatus(error.message || 'Upload failed.', 'error');
  } finally { upload.disabled = false; }
});
</script>
</body>
</html>
"""


def validate_png(data: bytes) -> tuple[str, int, int]:
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        raise ValueError("latest image is not PNG")
    pos = len(signature)
    width = height = bit_depth = color_type = interlace = 0
    saw_ihdr = saw_plte = saw_idat = saw_iend = idat_ended = False
    compressed = bytearray()
    legal_depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
    known_critical = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}

    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        end = pos + 12 + length
        if length > MAX_LATEST_BYTES or end > len(data):
            raise ValueError("malformed PNG chunk")
        payload = data[pos + 8 : pos + 8 + length]
        expected_crc = struct.unpack(">I", data[pos + 8 + length : end])[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
            raise ValueError("PNG checksum failed")
        if not saw_ihdr:
            if kind != b"IHDR" or length != 13:
                raise ValueError("PNG is missing IHDR")
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            if not width or not height or width * height > MAX_PIXELS:
                raise ValueError("PNG dimensions are invalid or too large")
            if bit_depth not in legal_depths.get(color_type, set()):
                raise ValueError("PNG bit depth and color type are invalid")
            if compression != 0 or filter_method != 0 or interlace not in (0, 1):
                raise ValueError("unsupported PNG encoding")
            saw_ihdr = True
        elif kind == b"IHDR":
            raise ValueError("PNG contains multiple IHDR chunks")
        elif kind == b"PLTE":
            if saw_plte or saw_idat or color_type in (0, 4) or not length or length % 3 or length > 768:
                raise ValueError("malformed PNG palette")
            if color_type == 3 and length // 3 > 2**bit_depth:
                raise ValueError("PNG palette exceeds bit depth")
            saw_plte = True
        elif kind == b"IDAT":
            if idat_ended or (color_type == 3 and not saw_plte):
                raise ValueError("malformed PNG IDAT ordering")
            saw_idat = True
            compressed.extend(payload)
            if len(compressed) > MAX_LATEST_BYTES:
                raise ValueError("PNG compressed stream is too large")
        elif kind == b"IEND":
            if length != 0 or not saw_idat:
                raise ValueError("malformed PNG IEND")
            saw_iend = True
            if end != len(data):
                raise ValueError("unexpected data after PNG IEND")
            break
        else:
            if saw_idat:
                idat_ended = True
            if kind[:1].isalpha() and kind[:1].isupper() and kind not in known_critical:
                raise ValueError("PNG contains an unknown critical chunk")
        pos = end

    if not (saw_ihdr and saw_idat and saw_iend):
        raise ValueError("PNG is incomplete")

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    bits_per_pixel = channels * bit_depth
    passes = ((0, 0, 1, 1),) if interlace == 0 else (
        (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
        (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2),
    )
    layouts: list[tuple[int, int]] = []
    decoded_size = 0
    for start_x, start_y, step_x, step_y in passes:
        pass_width = max(0, (width - start_x + step_x - 1) // step_x)
        pass_height = max(0, (height - start_y + step_y - 1) // step_y)
        if not pass_width or not pass_height:
            continue
        row_bytes = (pass_width * bits_per_pixel + 7) // 8
        layouts.append((row_bytes, pass_height))
        decoded_size += (row_bytes + 1) * pass_height
    if decoded_size > MAX_DECODED_BYTES:
        raise ValueError("PNG expands beyond the decoded-size limit")

    decompressor = zlib.decompressobj()
    try:
        decoded = decompressor.decompress(bytes(compressed), decoded_size + 1)
        if decompressor.unconsumed_tail or len(decoded) > decoded_size:
            raise ValueError("PNG expands beyond its declared dimensions")
        decoded += decompressor.flush()
    except zlib.error as error:
        raise ValueError("PNG image stream is invalid") from error
    if not decompressor.eof or decompressor.unused_data or len(decoded) != decoded_size:
        raise ValueError("PNG image stream is incomplete or malformed")
    offset = 0
    for row_bytes, rows in layouts:
        for _ in range(rows):
            if decoded[offset] > 4:
                raise ValueError("PNG uses an invalid row filter")
            offset += row_bytes + 1
    return "png", width, height


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError("malformed JPEG")
    pos = 2
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while pos + 4 <= len(data):
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if pos + 2 > len(data):
            break
        length = struct.unpack(">H", data[pos : pos + 2])[0]
        if length < 2 or pos + length > len(data):
            raise ValueError("malformed JPEG segment")
        if marker in sof:
            if length < 7:
                raise ValueError("malformed JPEG dimensions")
            height, width = struct.unpack(">HH", data[pos + 3 : pos + 7])
            if not width or not height or width * height > MAX_PIXELS:
                raise ValueError("JPEG dimensions are invalid or too large")
            return width, height
        if marker == 0xDA:
            break
        pos += length
    raise ValueError("JPEG dimensions not found")


def webp_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("malformed WebP container")
    if struct.unpack("<I", data[4:8])[0] + 8 != len(data):
        raise ValueError("malformed WebP size")
    pos = 12
    dimensions: tuple[int, int] | None = None
    while pos + 8 <= len(data):
        kind = data[pos : pos + 4]
        length = struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        start = pos + 8
        end = start + length
        padded_end = end + (length & 1)
        if end > len(data) or padded_end > len(data):
            raise ValueError("malformed WebP chunk")
        payload = data[start:end]
        if kind == b"VP8X" and length >= 10:
            dimensions = (1 + int.from_bytes(payload[4:7], "little"), 1 + int.from_bytes(payload[7:10], "little"))
        elif kind == b"VP8 " and length >= 10 and payload[3:6] == b"\x9d\x01\x2a":
            dimensions = (struct.unpack("<H", payload[6:8])[0] & 0x3FFF, struct.unpack("<H", payload[8:10])[0] & 0x3FFF)
        elif kind == b"VP8L" and length >= 5 and payload[0] == 0x2F:
            bits = int.from_bytes(payload[1:5], "little")
            dimensions = ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
        pos = padded_end
    if pos != len(data) or dimensions is None:
        raise ValueError("WebP image data is missing or malformed")
    width, height = dimensions
    if not width or not height or width * height > MAX_PIXELS:
        raise ValueError("WebP dimensions are invalid or too large")
    return width, height


def heif_type_and_dimensions(data: bytes) -> tuple[str, int, int]:
    if len(data) < 24 or data[4:8] != b"ftyp":
        raise ValueError("malformed HEIF/AVIF container")
    first_size = struct.unpack(">I", data[:4])[0]
    if first_size < 16 or first_size > len(data):
        raise ValueError("malformed HEIF/AVIF file-type box")
    brands = {data[i : i + 4] for i in range(8, first_size, 4)}
    if brands & {b"avif", b"avis"}:
        extension = "avif"
    elif brands & {b"heic", b"heix", b"hevc", b"hevx"}:
        extension = "heic"
    elif brands & {b"mif1", b"msf1"}:
        extension = "heif"
    else:
        raise ValueError("unsupported HEIF/AVIF brand")

    top_level: set[bytes] = set()
    pos = 0
    while pos + 8 <= len(data):
        size = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > len(data):
                raise ValueError("malformed extended HEIF/AVIF box")
            size = struct.unpack(">Q", data[pos + 8 : pos + 16])[0]
            header = 16
        elif size == 0:
            size = len(data) - pos
        if size < header or pos + size > len(data):
            raise ValueError("malformed HEIF/AVIF box")
        top_level.add(kind)
        pos += size
    if pos != len(data) or b"meta" not in top_level or b"mdat" not in top_level:
        raise ValueError("HEIF/AVIF metadata or image data is missing")

    width = height = 0
    search_from = 0
    while True:
        marker = data.find(b"ispe", search_from)
        if marker < 4:
            break
        size = struct.unpack(">I", data[marker - 4 : marker])[0]
        if size >= 20 and marker - 4 + size <= len(data):
            width, height = struct.unpack(">II", data[marker + 8 : marker + 16])
            if width and height:
                break
        search_from = marker + 4
    if not width or not height or width * height > MAX_PIXELS:
        raise ValueError("HEIF/AVIF dimensions are missing, invalid, or too large")
    return extension, width, height


def validate_original(data: bytes) -> tuple[str, int | None, int | None]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return validate_png(data)
    if data.startswith(b"\xff\xd8"):
        width, height = jpeg_dimensions(data)
        return "jpg", width, height
    if data.startswith(b"RIFF"):
        width, height = webp_dimensions(data)
        return "webp", width, height
    if len(data) >= 16 and data[4:8] == b"ftyp":
        return heif_type_and_dimensions(data)
    raise ValueError("unsupported or invalid image type")


def safe_stem(filename: str) -> str:
    stem = Path(filename).stem[:48]
    stem = NAME_RE.sub("-", stem).strip("-._")
    return stem or "image"


def atomic_write(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def parse_multipart(content_type: str, body: bytes) -> dict[str, tuple[str, bytes]]:
    if not content_type.lower().startswith("multipart/form-data"):
        raise ValueError("content type must be multipart/form-data")
    envelope = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii", "strict") + body
    )
    message = BytesParser(policy=policy.default).parsebytes(envelope)
    if message.defects or not message.is_multipart():
        raise ValueError("invalid or incomplete multipart request")
    fields: dict[str, tuple[str, bytes]] = {}
    for part in message.iter_parts():
        if part.defects:
            raise ValueError("invalid multipart upload field")
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename() or "upload"
        if name not in {"original", "latest"} or name in fields:
            raise ValueError("unexpected or duplicate upload field")
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            raise ValueError("invalid upload payload")
        fields[name] = (filename, payload)
    if "original" not in fields:
        raise ValueError("original image is required")
    return fields


class UploadHandler(BaseHTTPRequestHandler):
    server_version = "PrivateScreenshotUpload"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(20)
        self._deadline_timer = threading.Timer(CONNECTION_DEADLINE_SECONDS, self._expire_connection)
        self._deadline_timer.daemon = True
        self._deadline_timer.start()

    def _expire_connection(self) -> None:
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def finish(self) -> None:
        self._deadline_timer.cancel()
        super().finish()

    @property
    def app(self) -> "UploadServer":
        return self.server  # type: ignore[return-value]

    def security_headers(self, content_type: str, length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src 'self' blob: data:; connect-src 'self'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )

    def send_bytes(self, status: HTTPStatus, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.security_headers(content_type, len(data))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        self.send_bytes(status, json.dumps(payload).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if urlsplit(self.path).path != "/":
            self.send_bytes(HTTPStatus.NOT_FOUND, b"Not found\n", "text/plain; charset=utf-8")
            return
        self.send_bytes(HTTPStatus.OK, PAGE.encode(), "text/html; charset=utf-8")

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/upload":
            self.send_bytes(HTTPStatus.NOT_FOUND, b"Not found\n", "text/plain; charset=utf-8")
            return
        try:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                self.send_json(HTTPStatus.LENGTH_REQUIRED, {"error": "Content-Length is required"})
                return
            length = int(raw_length)
            if length < 1 or length > MAX_REQUEST_BYTES:
                self.send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "upload request is too large"})
                return
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("incomplete upload request")
            fields = parse_multipart(self.headers.get("Content-Type", ""), body)
            original_name, original = fields["original"]
            if not 0 < len(original) <= MAX_ORIGINAL_BYTES:
                raise ValueError("original image must be between 1 byte and 25 MiB")
            extension, width, height = validate_original(original)
            latest = fields.get("latest", ("latest.png", original if extension == "png" else b""))[1]
            if not 0 < len(latest) <= MAX_LATEST_BYTES:
                raise ValueError("a PNG latest image is required and must not exceed 25 MiB")
            _, latest_width, latest_height = validate_png(latest)

            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            filename = f"{timestamp}-{safe_stem(original_name)}-{secrets.token_hex(3)}.{extension}"
            original_path = self.app.output_dir / filename
            latest_path = self.app.output_dir / "latest.png"
            with WRITE_LOCK:
                atomic_write(original_path, original)
                atomic_write(latest_path, latest)
            self.send_json(
                HTTPStatus.CREATED,
                {
                    "ok": True,
                    "filename": filename,
                    "original": str(original_path),
                    "latest": str(latest_path),
                    "originalDimensions": [width, height] if width and height else None,
                    "latestDimensions": [latest_width, latest_height],
                },
            )
        except (ValueError, TypeError) as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except TimeoutError:
            self.send_json(HTTPStatus.REQUEST_TIMEOUT, {"error": "upload timed out"})
        except Exception as error:
            self.log_error("internal upload error: %s", type(error).__name__)
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal upload error"})

    def do_HEAD(self) -> None:
        self.send_bytes(HTTPStatus.METHOD_NOT_ALLOWED, b"", "text/plain; charset=utf-8")


class UploadServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 8
    busy_response = (
        b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n"
        b"Content-Type: text/plain\r\nContent-Length: 16\r\n\r\nServer is busy.\n"
    )

    def __init__(self, address: tuple[str, int], handler: type[UploadHandler], output_dir: Path):
        self.output_dir = output_dir
        self.connection_slots = threading.BoundedSemaphore(MAX_CONCURRENT_CONNECTIONS)
        super().__init__(address, handler)

    def process_request(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        if not self.connection_slots.acquire(blocking=False):
            try:
                request.sendall(self.busy_response)
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.connection_slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connection_slots.release()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    address = ipaddress.ip_address(args.bind)
    tailscale_range = ipaddress.ip_network("100.64.0.0/10")
    if address.is_unspecified or address.is_loopback or not (address.is_private or address in tailscale_range):
        raise SystemExit("--bind must be a specific private or Tailscale address")
    if not 1024 <= args.port <= 65535:
        raise SystemExit("--port must be an unprivileged TCP port")
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(args.output_dir, 0o700)

    server = UploadServer((args.bind, args.port), UploadHandler, args.output_dir.resolve())
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    print(f"Listening on http://{args.bind}:{args.port}/", flush=True)
    server.serve_forever(poll_interval=0.5)
    server.server_close()


if __name__ == "__main__":
    main()
