"""Image proxy used by graph export to get around CORS on asset hosts."""

import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from urllib3.exceptions import LocationParseError
from urllib3.util import parse_url

from services.gitee import AVATAR_COOLDOWN, GITEE_FOLDER, http_session

router = APIRouter()


# ---------------------------------------------------
# Asset proxy (used by graph export to bypass CORS on assets.tarkov.dev)
# ---------------------------------------------------

_PROXY_ALLOWED_HOSTS = {"assets.tarkov.dev", "gitee.com", "raw.giteeusercontent.com"}
_PROXY_MAX_BYTES = 20 * 1024 * 1024  # 20 MB cap per proxied asset


_DNS_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def _proxy_host_allowed(host: str) -> bool:
    # Exact match or subdomain of an allowed host (e.g. foruda.gitee.com for gitee.com avatars)
    return host in _PROXY_ALLOWED_HOSTS or any(host.endswith("." + h) for h in _PROXY_ALLOWED_HOSTS)


def _allowed_proxy_url(url: str) -> str | None:
    """Return the exact URL to fetch, or None when it isn't an https URL on an allowed host.

    Check the URL with urllib3's parser, the one requests sends with, and fetch the URL
    that parser rebuilds. urllib.parse reads "https://127.0.0.1\\.gitee.com/" as a
    gitee.com subdomain while urllib3 connects to 127.0.0.1, so validating with one
    parser and fetching with the other let any host through.
    """
    if "\\" in url or any(ord(c) <= 0x20 or ord(c) == 0x7F for c in url):
        return None
    try:
        parsed = parse_url(url)
    except LocationParseError:
        return None
    host = (parsed.host or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.auth
        or parsed.port not in (None, 443)
        or not _DNS_NAME_RE.match(host)
        or not _proxy_host_allowed(host)
    ):
        return None
    return parsed.url


@router.get("/proxy-asset")
def proxy_asset(url: str, request: Request):
    current_url = _allowed_proxy_url(url)
    if current_url is None:
        raise HTTPException(status_code=400, detail="URL not in proxy allowlist")
    try:
        # Follow redirects manually so each hop is validated against the allowlist.
        # Gitee avatar URLs redirect to CDN subdomains (e.g. foruda.gitee.com) which
        # are allowed as subdomains of gitee.com but would escape a naive allow_redirects=True.
        r = None
        for _ in range(5):
            r = http_session.get(current_url, timeout=8, stream=True, allow_redirects=False)
            if 300 <= r.status_code < 400:
                r.close()
                location = r.headers.get("Location", "")
                if not location:
                    raise HTTPException(status_code=502, detail="Redirect with no Location header")
                current_url = _allowed_proxy_url(location)
                if current_url is None:
                    raise HTTPException(status_code=502, detail="Redirect escapes proxy allowlist")
                continue
            r.raise_for_status()
            break
        else:
            raise HTTPException(status_code=502, detail="Too many redirects")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # Only images may be proxied. gitee.com hosts arbitrary user repos, so serving
    # upstream HTML/JS from our origin would be an XSS vector.
    content_type = r.headers.get("content-type", "application/octet-stream").split(";")[0].strip().lower()
    if not (content_type.startswith("image/") or content_type == "application/octet-stream"):
        r.close()
        raise HTTPException(status_code=415, detail="Only image assets may be proxied")

    def _capped_stream():
        sent = 0
        for chunk in r.iter_content(chunk_size=65536):
            sent += len(chunk)
            if sent > _PROXY_MAX_BYTES:
                r.close()
                return
            yield chunk

    if f"/{GITEE_FOLDER}/" in url:
        # build images never change at their URL once published (a new build gets a new
        # filename), so browsers can hold onto them indefinitely.
        cache_control = "public, max-age=604800, immutable"
    else:
        # avatars are overwritten in place on re-upload, so cache lifetime matches the
        # upload cooldown; everything else proxied here (tarkov.dev icons, live preview
        # renders) is also short-lived or one-off.
        cache_control = f"public, max-age={int(AVATAR_COOLDOWN)}"

    return StreamingResponse(
        _capped_stream(),
        media_type=content_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
            "Cache-Control": cache_control,
        },
    )
