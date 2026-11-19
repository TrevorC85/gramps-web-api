"""Web-ready derivatives for oversized scans (Collins Family Gramps Web).

Gramps Web uploads are kept at or below LIMIT (10 MiB) so the stock server image
works locally and on AWS without any in-container upload patch.

prepare_upload(path) returns (upload_path, info):
  * files <= LIMIT are returned unchanged (info=None)
  * larger images -> JPEG (quality 85, full resolution first; then quality 80;
    then downscale in steps, never below MIN_LONG_SIDE px on the long side)
  * larger PDFs -> Ghostscript /printer then /ebook if `gs` exists; otherwise
    pikepdf re-encodes embedded images as JPEG q85 (downscaled to MIN_LONG_SIDE)
The original is never modified or deleted. Derivatives go to
/workspace/gramps-web/web-ready/<parent-folder>/<stem>.web.<ext> and are reused
when newer than the original.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

class OversizeError(RuntimeError):
    pass


LIMIT = 10 * 1024 * 1024
TARGET = int(9.5 * 1024 * 1024)
MIN_LONG_SIDE = 2500
WEB_READY = Path(os.environ.get("GRAMPSWEB_WEB_READY_DIR", "/workspace/gramps-web/web-ready"))
VENV_PY = "/workspace/.venv-ocr/bin/python"  # has pikepdf; optional


def _dest(path: Path, ext: str) -> Path:
    d = WEB_READY / path.parent.name
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{path.stem}.web{ext}"


def _image(path: Path) -> Path:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    out = _dest(path, ".jpg")
    im = Image.open(path)
    im.seek(0)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB") if im.mode not in ("RGB", "L") else im
    w, h = im.size
    long0 = max(w, h)
    attempts = [(1.0, 85), (1.0, 80)]
    scale = 1.0
    while long0 * scale > MIN_LONG_SIDE:
        scale = max(scale * 0.85, MIN_LONG_SIDE / long0)
        attempts.append((scale, 85))
    attempts.append((MIN_LONG_SIDE / long0 if long0 > MIN_LONG_SIDE else 1.0, 75))
    for sc, q in attempts:
        cand = im if sc >= 0.999 else im.resize((round(w * sc), round(h * sc)), Image.LANCZOS)
        buf = io.BytesIO()
        cand.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        if buf.tell() <= TARGET:
            out.write_bytes(buf.getvalue())
            return out
    raise OversizeError(f"could not get {path} under {TARGET} bytes")


def _pdf(path: Path) -> Path:
    out = _dest(path, ".pdf")
    gs = shutil.which("gs")
    if gs:
        for dpi in (300, 200, 150):
            args = [gs, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pdfwrite",
                    "-dCompatibilityLevel=1.6", "-dPDFSETTINGS=/printer", "-dDetectDuplicateImages=true",
                    "-dAutoFilterColorImages=false", "-dColorImageFilter=/DCTEncode",
                    "-dAutoFilterGrayImages=false", "-dGrayImageFilter=/DCTEncode", "-dJPEGQ=85",
                    "-dDownsampleColorImages=true", "-dDownsampleGrayImages=true", "-dDownsampleMonoImages=true",
                    f"-dColorImageResolution={dpi}", f"-dGrayImageResolution={dpi}",
                    f"-dMonoImageResolution={max(dpi, 300)}",
                    f"-sOutputFile={out}", str(path)]
            subprocess.run(args, check=True, timeout=1800)
            if out.stat().st_size <= TARGET:
                return out
    py = sys.executable
    try:
        import pikepdf  # noqa: F401
    except ImportError:
        py = VENV_PY if Path(VENV_PY).exists() else None
    if py:
        code = PIKE_SNIPPET
        subprocess.run([py, "-c", code, str(path), str(out), str(MIN_LONG_SIDE)], check=True, timeout=1800)
        if out.stat().st_size <= TARGET:
            return out
    out.unlink(missing_ok=True)  # never leave an over-limit "web-ready" copy behind (original untouched)
    raise OversizeError(f"{path} is still over {TARGET} bytes after Ghostscript; attach a page extract instead")


PIKE_SNIPPET = r'''
import sys, io, pikepdf
from pikepdf import PdfImage, Name
from PIL import Image
Image.MAX_IMAGE_PIXELS = None
src, dst, minside = sys.argv[1], sys.argv[2], int(sys.argv[3])
pdf = pikepdf.open(src)
for page in pdf.pages:
    for name, raw in list(page.images.items()):
        try:
            im = PdfImage(raw).as_pil_image()
        except Exception:
            continue
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > minside:
            s = minside / max(w, h)
            im = im.resize((round(w * s), round(h * s)), Image.LANCZOS)
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=85, optimize=True)
        raw.write(buf.getvalue(), filter=Name.DCTDecode)
        raw.Width, raw.Height = im.size
        raw.ColorSpace = Name.DeviceGray if im.mode == "L" else Name.DeviceRGB
        raw.BitsPerComponent = 8
        for k in ("/DecodeParms", "/SMask", "/Mask", "/Decode"):
            if k in raw: del raw[k]
pdf.save(dst, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
'''


def prepare_upload(path: Path | str):
    path = Path(path)
    size = path.stat().st_size
    if size <= LIMIT:
        return path, None
    ext = ".pdf" if path.suffix.lower() == ".pdf" else ".jpg"
    out = _dest(path, ext)
    if not (out.exists() and out.stat().st_mtime >= path.stat().st_mtime and out.stat().st_size <= LIMIT):
        out = _pdf(path) if ext == ".pdf" else _image(path)
    info = {"original": str(path), "original_bytes": size, "derivative": str(out),
            "derivative_bytes": out.stat().st_size}
    return out, info


if __name__ == "__main__":
    for a in sys.argv[1:]:
        try:
            p, i = prepare_upload(a)
            print(a, "->", p, i, flush=True)
        except OversizeError as e:
            print("OVERSIZE", e, flush=True)


def derivative_attributes(info: dict) -> list[dict]:
    if not info:
        return []
    def a(t, v):
        return {"_class": "Attribute", "type": t, "value": str(v), "private": False,
                "citation_list": [], "note_list": []}
    return [
        a("Original file", info["original"]),
        a("Original size (bytes)", info["original_bytes"]),
        a("Web-ready derivative", f'{info["derivative"]} ({info["derivative_bytes"]} bytes)'),
    ]


def set_derivative_metadata(media: dict, info: dict) -> dict:
    """Add/refresh the original-file attributes and a short desc suffix."""
    if not info:
        return media
    keep = [x for x in media.get("attribute_list") or []
            if x.get("type") not in ("Original file", "Original size (bytes)", "Web-ready derivative")]
    media["attribute_list"] = keep + derivative_attributes(info)
    tag = f" [web-ready copy; original: {info['original']}]"
    desc = media.get("desc") or ""
    if "[web-ready copy; original:" not in desc:
        media["desc"] = desc + tag
    return media


def ensure_media_small(api, handle: str, original: Path | str) -> dict | None:
    """If a Gramps media object's file is > LIMIT, replace it with the web-ready
    derivative of `original` (PUT /api/media/<h>/file) and record the original."""
    media = api.get(f"/api/media/{handle}")
    up, info = prepare_upload(original)
    if not info:
        return None
    import hashlib
    if media.get("checksum") != hashlib.md5(up.read_bytes()).hexdigest():
        mime = "application/pdf" if up.suffix == ".pdf" else "image/jpeg"
        api.request("PUT", f"/api/media/{handle}/file", data=up.read_bytes(), content_type=mime, timeout=300)
        media = api.get(f"/api/media/{handle}")
    media = set_derivative_metadata(media, info)
    api.put(f"/api/media/{handle}", media)
    return info
