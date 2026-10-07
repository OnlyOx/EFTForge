"""Gitee asset repo client: community build card images and profile avatars."""

import base64
import logging

# Shared session: connection pooling for proxy + Gitee API calls.
# Identifying user agent so upstreams can tell what
# the traffic is instead of seeing a generic python-requests default.
import requests as _requests

_logger = logging.getLogger(__name__)


AVATAR_COOLDOWN = 90.0

http_session = _requests.Session()
http_session.headers["User-Agent"] = "EFTForge (+https://eftforge.com; +https://github.com/SouthHorizons76/EFTForge)"


_GITEE_API = "https://gitee.com/api/v5"
_GITEE_OWNER = "morph1ne"
_GITEE_REPO = "eftforge-assets"
GITEE_FOLDER = "streaming-assets/build-images"
_GITEE_BRANCH = "master"
GITEE_RAW_PREFIX = f"https://gitee.com/{_GITEE_OWNER}/{_GITEE_REPO}" f"/raw/{_GITEE_BRANCH}/{GITEE_FOLDER}/"

_GITEE_AVATAR_FOLDER = "streaming-assets/avatars"
GITEE_AVATAR_PREFIX = f"https://gitee.com/{_GITEE_OWNER}/{_GITEE_REPO}" f"/raw/{_GITEE_BRANCH}/{_GITEE_AVATAR_FOLDER}/"


def gitee_upload_sync(filename: str, image_bytes: bytes, token: str) -> str:
    """Upload or overwrite a build image in the Gitee asset repo.
    Returns the permanent raw URL for the file."""
    import requests as _req

    path = f"{GITEE_FOLDER}/{filename}"
    api_url = f"{_GITEE_API}/repos/{_GITEE_OWNER}/{_GITEE_REPO}/contents/{path}"
    content = base64.b64encode(image_bytes).decode()

    # fetch existing file SHA so we can update rather than error on duplicate
    sha = None
    r = _req.get(api_url, params={"access_token": token}, timeout=20)
    if r.status_code == 200:
        data = r.json()
        sha = data.get("sha") if isinstance(data, dict) else None

    payload = {
        "access_token": token,
        "message": f"ci: auto-generate build image {filename}",
        "content": content,
        "branch": _GITEE_BRANCH,
    }
    if sha:
        payload["sha"] = sha
        r = _req.put(api_url, json=payload, timeout=30)
    else:
        r = _req.post(api_url, json=payload, timeout=30)

    r.raise_for_status()
    return f"{GITEE_RAW_PREFIX}{filename}"


def gitee_delete_image(card_image_url: str | None, build_id: int) -> None:
    """Best-effort deletion of a build image from the Gitee asset repo.
    Silently logs and returns on any failure - never raises."""
    if not card_image_url or not card_image_url.startswith(GITEE_RAW_PREFIX):
        return  # nothing to delete (no image, dry-run prefix, error sentinel, etc.)

    from config import GITEE_TOKEN, GITEE_DRY_RUN

    if not GITEE_TOKEN or GITEE_DRY_RUN:
        return

    import requests as _req

    filename = card_image_url.removeprefix(GITEE_RAW_PREFIX).split("?", 1)[0]
    path = f"{GITEE_FOLDER}/{filename}"
    api_url = f"{_GITEE_API}/repos/{_GITEE_OWNER}/{_GITEE_REPO}/contents/{path}"

    try:
        r = _req.get(api_url, params={"access_token": GITEE_TOKEN}, timeout=20)
        if r.status_code == 404:
            return  # already gone
        r.raise_for_status()
        sha = r.json().get("sha")
        if not sha:
            _logger.error("gitee-delete: no SHA returned for build %s image %s", build_id, filename)
            return

        r = _req.delete(
            api_url,
            json={
                "access_token": GITEE_TOKEN,
                "message": f"ci: remove build image for deleted build {build_id}",
                "sha": sha,
                "branch": _GITEE_BRANCH,
            },
            timeout=30,
        )
        r.raise_for_status()
        _logger.warning("gitee-delete: removed image %s for build %s", filename, build_id)
    except Exception as exc:
        _logger.error("gitee-delete: failed to delete image for build %s: %s", build_id, exc)


def gitee_wipe_build_images_folder() -> int:
    """Delete every file in the build-images folder via the Contents API.
    Returns the number of images deleted, or -1 on error."""
    from config import GITEE_TOKEN, GITEE_DRY_RUN

    if not GITEE_TOKEN or GITEE_DRY_RUN:
        return 0

    import requests as _req

    tok = {"access_token": GITEE_TOKEN}

    try:
        # list all files in the build-images folder (Gitee Contents API)
        r = _req.get(
            f"{_GITEE_API}/repos/{_GITEE_OWNER}/{_GITEE_REPO}/contents/{GITEE_FOLDER}",
            params={**tok, "ref": _GITEE_BRANCH},
            timeout=30,
        )
        if r.status_code == 404:
            return 0
        r.raise_for_status()

        entries = r.json()
        if not isinstance(entries, list):
            return 0

        file_entries = [e for e in entries if e.get("type") == "file"]
        deleted = 0
        for entry in file_entries:
            dr = _req.delete(
                f"{_GITEE_API}/repos/{_GITEE_OWNER}/{_GITEE_REPO}/contents/{entry['path']}",
                params=tok,
                json={
                    "message": "ci: wipe all build images",
                    "sha": entry["sha"],
                    "branch": _GITEE_BRANCH,
                },
                timeout=30,
            )
            if dr.ok:
                deleted += 1
            else:
                _logger.warning("gitee: failed to delete %s: %s", entry["path"], dr.text)

        _logger.warning("gitee: wiped build-images folder (%d files)", deleted)
        return deleted

    except Exception as exc:
        _logger.error("gitee: wipe build-images folder failed: %s", exc)
        return -1


MAX_AVATAR_BYTES = 2 * 1024 * 1024  # 2 MB raw limit before processing
_AVATAR_RENDER_SIZE = 128  # output square dimension in pixels


def process_and_upload_avatar(image_bytes: bytes, ip_hash: str) -> str:
    """Decode, resize to 128x128 JPEG, upload to Gitee. Returns the raw URL."""
    from PIL import Image
    import io
    from config import GITEE_TOKEN

    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception:
        raise ValueError("Invalid image data - could not decode.")

    if img.format not in ("JPEG", "PNG", "WEBP", "GIF"):
        raise ValueError("Unsupported image format. Use JPEG, PNG, or WebP.")

    img = img.convert("RGB")
    img.thumbnail((_AVATAR_RENDER_SIZE, _AVATAR_RENDER_SIZE), Image.LANCZOS)

    # crop to square from center
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    jpeg_bytes = buf.getvalue()

    filename = f"avatar_{ip_hash[:20]}.jpg"
    path = f"{_GITEE_AVATAR_FOLDER}/{filename}"
    api_url = f"{_GITEE_API}/repos/{_GITEE_OWNER}/{_GITEE_REPO}/contents/{path}"
    content = base64.b64encode(jpeg_bytes).decode()

    import requests as _req

    sha = None
    r = _req.get(api_url, params={"access_token": GITEE_TOKEN}, timeout=20)
    if r.status_code == 200:
        data = r.json()
        sha = data.get("sha") if isinstance(data, dict) else None

    payload = {
        "access_token": GITEE_TOKEN,
        "message": f"ci: avatar upload for {ip_hash[:8]}",
        "content": content,
        "branch": _GITEE_BRANCH,
    }
    if sha:
        payload["sha"] = sha
        r = _req.put(api_url, json=payload, timeout=30)
    else:
        r = _req.post(api_url, json=payload, timeout=30)

    r.raise_for_status()
    return f"{GITEE_AVATAR_PREFIX}{filename}"


def validate_avatar_url(url: str | None) -> str | None:
    if not url:
        return None
    if not url.startswith(GITEE_AVATAR_PREFIX):
        raise ValueError("avatar_url must point to the EFTForge asset repository.")
    return url
