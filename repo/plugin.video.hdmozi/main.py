from __future__ import unicode_literals

import base64
import binascii
import html
import json
import os
import random
import re
import string
import ssl
import sys
import time
import traceback
import urllib.parse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from resources.lib.pyaes import AESModeOfOperationCBC, Decrypter
from resources.lib import sorozatcc


ADDON = xbmcaddon.Addon()
ADDON_HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]
PROFILE_DIR = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
SEARCHES_FILE = os.path.join(PROFILE_DIR, "saved_searches.json")
UPDATE_STATE_FILE = os.path.join(PROFILE_DIR, "update_state.json")
SITE_URL = "https://hdmozi.hu"
REPO_ADDONS_URL = "https://trn02.github.io/repository.romantv/repo/addons.xml"
REPO_REFRESH_INTERVAL_SECONDS = 6 * 60 * 60
RPM_MAX_ATTEMPTS = 8
RPM_RETRY_DELAY_SECONDS = 1.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
VIDEA_STATIC_SECRET = "xHb0ZvME5q8CBcoQi6AngerDu3FGO9fkUlwPmLVY_RTzj2hJIS4NasXWKy1td7p"

VENDOR_DIR = Path(__file__).resolve().parent / "resources" / "lib" / "vendor"
if str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

sorozatcc.configure(
    base_url=BASE_URL,
    addon_handle=ADDON_HANDLE,
    profile_dir=PROFILE_DIR,
    action_prefix="sc_",
    embed_resolver=lambda url: resolve_embed_url(url),
    source_classifier=lambda url: classify_source_url(url),
)


MEDIA_EXTENSIONS = (".mp4", ".m3u8", ".mpd", ".webm", ".mkv", ".avi", ".mov", ".m4v", ".ts")
QUALITY_BANDWIDTH_LIMITS = {
    480: 1800000,
    720: 3000000,
    1080: 6500000,
    1440: 12000000,
    2160: 20000000,
}


class SourceResolutionError(ValueError):
    def __init__(self, kind, message, url, host=None, cause=None):
        super(SourceResolutionError, self).__init__(message)
        self.kind = kind
        self.url = url
        self.host = host or get_url_host(url)
        self.cause = cause


def ensure_profile_dir():
    if not xbmcvfs.exists(PROFILE_DIR):
        xbmcvfs.mkdirs(PROFILE_DIR)


def build_url(**query):
    return "{}?{}".format(BASE_URL, urllib.parse.urlencode(query))


def log(message):
    xbmc.log("[plugin.video.hdmozi] {}".format(message), xbmc.LOGINFO)


def request(url, data=None, headers=None, return_headers=False):
    final_headers = {
        "User-Agent": USER_AGENT,
        "Referer": SITE_URL + "/",
        "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.8",
    }
    if headers:
        final_headers.update(headers)

    payload = None
    if data is not None:
        payload = urllib.parse.urlencode(data).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers=final_headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read()
            if return_headers:
                return body, response.headers
            return body
    except urllib.error.URLError as exc:
        reason_text = str(getattr(exc, "reason", exc))
        host = urllib.parse.urlparse(url).hostname or ""
        is_ip_host = bool(re.match(r"^\d+\.\d+\.\d+\.\d+$", host))
        if is_ip_host and "CERTIFICATE_VERIFY_FAILED" in reason_text:
            context = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=20, context=context) as response:
                body = response.read()
                if return_headers:
                    return body, response.headers
                return body
        raise


def request_text(url, data=None, headers=None, encoding="utf-8", return_headers=False):
    response = request(url, data=data, headers=headers, return_headers=return_headers)
    if return_headers:
        body, response_headers = response
        return body.decode(encoding, "replace"), response_headers
    return response.decode(encoding, "replace")


def request_json(url, data=None, headers=None):
    return json.loads(request_text(url, data=data, headers=headers))


def strip_tags(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def normalize_url(url):
    if not url:
        return url
    url = html.unescape(url).strip().strip('"\'')
    if url.startswith("direct://"):
        return url[len("direct://"):]
    if url.startswith("//"):
        return "https:" + url
    return url


def strip_url_headers(url):
    return (url or "").split("|", 1)[0]


def get_max_stream_height():
    setting = (ADDON.getSetting("max_stream_height") or "720").strip().lower()
    if setting in ("0", "auto", "adaptive"):
        return 0
    if setting in ("1", "2", "3", "4"):
        return {"1": 480, "2": 720, "3": 1080, "4": 2160}[setting]
    try:
        return int(setting)
    except ValueError:
        return 720


def get_max_stream_bandwidth():
    max_height = get_max_stream_height()
    if not max_height:
        return 0
    return QUALITY_BANDWIDTH_LIMITS.get(max_height, max_height * 4500)


def select_best_format(formats, max_height=None):
    if not formats:
        raise ValueError("Nincs valaszthato videoformatum")
    max_height = get_max_stream_height() if max_height is None else max_height
    sorted_formats = sorted(formats, key=lambda item: item.get("height") or 0, reverse=True)
    if max_height:
        capped = [item for item in sorted_formats if (item.get("height") or 0) <= max_height]
        if capped:
            return capped[0]
        return sorted(formats, key=lambda item: item.get("height") or 0)[0]
    return sorted_formats[0]


def get_url_host(url):
    parsed = urllib.parse.urlparse(strip_url_headers(normalize_url(url)))
    return (parsed.netloc or "").lower().split("@")[-1].split(":")[0]


def is_media_url(url):
    base_url = strip_url_headers(normalize_url(url)).lower()
    if not base_url:
        return False
    if base_url.startswith("plugin://"):
        return True
    parsed = urllib.parse.urlparse(base_url)
    path = parsed.path or ""
    return any(path.endswith(ext) for ext in MEDIA_EXTENSIONS)


def is_special_embed_page(url):
    parsed = urllib.parse.urlparse(strip_url_headers(normalize_url(url)))
    host = (parsed.netloc or "").lower()
    path = parsed.path.lower()
    if host == "sorozatok.net" and (path.endswith("/embed.php") or path.endswith("/watch.php")):
        return True
    return False


def has_resolver_support(url):
    try:
        from resolveurl.hmf import HostedMediaFile

        return bool(HostedMediaFile(url=normalize_url(url)))
    except Exception:
        return False


def classify_source_url(url):
    normalized_url = normalize_url(url)
    host = get_url_host(normalized_url)
    if not normalized_url:
        return {"kind": "unknown", "label": "Ismeretlen", "host": host, "playable_hint": False}
    if is_media_url(normalized_url):
        return {"kind": "direct", "label": "Direkt", "host": host, "playable_hint": True}
    if is_special_embed_page(normalized_url):
        return {"kind": "embed", "label": "Embed oldal", "host": host, "playable_hint": True}
    if host in {"sorozat.cc", "www.sorozat.cc", "hdmozi.hu", "www.hdmozi.hu"}:
        return {"kind": "unknown", "label": "Oldallink", "host": host, "playable_hint": False}
    if has_resolver_support(normalized_url):
        return {"kind": "embed", "label": "Resolveres host", "host": host, "playable_hint": True}
    if host:
        return {"kind": "unsupported", "label": "Nem támogatott host", "host": host, "playable_hint": True}
    return {"kind": "unknown", "label": "Ismeretlen", "host": host, "playable_hint": False}


def ensure_media_result(url, headers=None, input_url=None):
    normalized_url = normalize_url(url)
    if not is_media_url(normalized_url):
        raise SourceResolutionError(
            "embed_page",
            "A forrás nem közvetlen videó link: {}".format(get_url_host(input_url or normalized_url) or normalized_url),
            input_url or normalized_url,
        )
    result = {"url": normalized_url}
    if headers:
        result["headers"] = headers
    return result


def is_hls_like_url(url):
    base_url = strip_url_headers(normalize_url(url)).lower()
    if not base_url:
        return False
    path = urllib.parse.urlparse(base_url).path.lower()
    return ".m3u8" in path or path.endswith(".txt") or "/master." in path


def probe_hls_manifest(url, headers):
    try:
        manifest_text, response_headers = request_text(url, headers=headers, return_headers=True)
    except Exception as exc:
        log("hls probe failed url={} cause={}".format(url, exc))
        return False

    content_type = (response_headers.get("Content-Type") or "").lower()
    if "mpegurl" in content_type or "m3u" in content_type:
        return True
    return manifest_text.lstrip().startswith("#EXTM3U")


def extract_hls_uris(manifest_text):
    return [
        line.strip()
        for line in (manifest_text or "").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def parse_hls_attribute_list(attribute_text):
    attrs = {}
    for match in re.finditer(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', attribute_text or ""):
        value = match.group(2).strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        attrs[match.group(1)] = value
    return attrs


def parse_hls_variants(manifest_text):
    variants = []
    lines = (manifest_text or "").splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        attrs = parse_hls_attribute_list(line.split(":", 1)[1])
        next_uri = ""
        for uri_line in lines[index + 1:]:
            uri_line = uri_line.strip()
            if uri_line and not uri_line.startswith("#"):
                next_uri = uri_line
                break
        if not next_uri:
            continue
        resolution = attrs.get("RESOLUTION") or ""
        height = 0
        if "x" in resolution:
            try:
                height = int(resolution.lower().split("x", 1)[1])
            except ValueError:
                height = 0
        try:
            bandwidth = int(attrs.get("BANDWIDTH") or 0)
        except ValueError:
            bandwidth = 0
        variants.append({
            "uri": next_uri,
            "height": height,
            "bandwidth": bandwidth,
        })
    return variants


def looks_like_png(data):
    return bool(data and data.startswith(b"\x89PNG\r\n\x1a\n"))


def looks_like_html(data):
    sample = (data or b"")[:256].lstrip().lower()
    return sample.startswith(b"<!doctype html") or sample.startswith(b"<html")


def probe_hls_media(url, headers):
    try:
        master_text, master_headers = request_text(url, headers=headers, return_headers=True)
    except Exception as exc:
        log("hls media probe master failed url={} cause={}".format(url, exc))
        return False

    content_type = (master_headers.get("Content-Type") or "").lower()
    if "mpegurl" not in content_type and "m3u" not in content_type and not master_text.lstrip().startswith("#EXTM3U"):
        return False

    master_uris = extract_hls_uris(master_text)
    if not master_uris:
        return False

    child_url = urllib.parse.urljoin(url, master_uris[0])
    try:
        child_text, child_headers = request_text(child_url, headers=headers, return_headers=True)
    except Exception as exc:
        log("hls media probe child failed url={} cause={}".format(child_url, exc))
        return False

    child_type = (child_headers.get("Content-Type") or "").lower()
    if "mpegurl" not in child_type and "m3u" not in child_type and not child_text.lstrip().startswith("#EXTM3U"):
        return False

    media_uris = extract_hls_uris(child_text)
    if not media_uris:
        return False

    segment_url = urllib.parse.urljoin(child_url, media_uris[0])
    try:
        segment_bytes, segment_headers = request(
            segment_url,
            headers=dict(headers, Range="bytes=0-31"),
            return_headers=True,
        )
    except Exception as exc:
        log("hls media probe segment failed url={} cause={}".format(segment_url, exc))
        return False

    segment_type = (segment_headers.get("Content-Type") or "").lower()
    if segment_type.startswith("image/") or looks_like_png(segment_bytes):
        log("hls media probe rejected image segment url={} type={}".format(segment_url, segment_type))
        return False
    if "html" in segment_type or looks_like_html(segment_bytes):
        log("hls media probe rejected html segment url={} type={}".format(segment_url, segment_type))
        return False
    return True


def probe_hls_child_stream(child_url, headers):
    try:
        child_text, child_headers = request_text(child_url, headers=headers, return_headers=True)
    except Exception as exc:
        log("hls child stream probe failed url={} cause={}".format(child_url, exc))
        return False

    child_type = (child_headers.get("Content-Type") or "").lower()
    if "mpegurl" not in child_type and "m3u" not in child_type and not child_text.lstrip().startswith("#EXTM3U"):
        return False
    if "#EXT-X-PLAYLIST-TYPE:VOD" not in child_text or "#EXT-X-ENDLIST" not in child_text:
        return False

    map_match = re.search(r'^#EXT-X-MAP:URI="([^"]+)"', child_text, re.MULTILINE)
    if map_match:
        map_url = urllib.parse.urljoin(child_url, map_match.group(1))
        try:
            map_bytes, map_headers = request(map_url, headers=dict(headers, Range="bytes=0-31"), return_headers=True)
        except Exception as exc:
            log("hls map probe failed url={} cause={}".format(map_url, exc))
            return False
        map_type = (map_headers.get("Content-Type") or "").lower()
        if map_type.startswith("image/") or "html" in map_type or looks_like_png(map_bytes) or looks_like_html(map_bytes):
            return False

    media_uris = extract_hls_uris(child_text)
    if len(media_uris) < 2:
        return False

    for media_ref in media_uris[:2]:
        media_url = urllib.parse.urljoin(child_url, media_ref)
        try:
            media_bytes, media_headers = request(media_url, headers=dict(headers, Range="bytes=0-31"), return_headers=True)
        except Exception as exc:
            log("hls media uri probe failed url={} cause={}".format(media_url, exc))
            return False
        media_type = (media_headers.get("Content-Type") or "").lower()
        if media_type.startswith("image/") or "html" in media_type or looks_like_png(media_bytes) or looks_like_html(media_bytes):
            return False

    return True


def resolve_best_hls_url(url, headers, prefer_vod_child=False):
    manifest_text, response_headers = request_text(url, headers=headers, return_headers=True)
    content_type = (response_headers.get("Content-Type") or "").lower()
    if "mpegurl" not in content_type and "m3u" not in content_type and not manifest_text.lstrip().startswith("#EXTM3U"):
        raise ValueError("A HLS manifest nem olvashato")

    variants = parse_hls_variants(manifest_text)
    uris = [variant["uri"] for variant in variants] or extract_hls_uris(manifest_text)
    if not uris:
        return url

    if not prefer_vod_child:
        return url

    max_height = get_max_stream_height()
    if variants and max_height:
        sorted_variants = sorted(variants, key=lambda item: item.get("height") or 0, reverse=True)
        capped_variants = [variant for variant in sorted_variants if (variant.get("height") or 0) <= max_height]
        ordered_variants = capped_variants or sorted(variants, key=lambda item: item.get("height") or 0)
        uris = [variant["uri"] for variant in ordered_variants]

    best_child_url = ""
    for child_ref in uris:
        child_url = urllib.parse.urljoin(url, child_ref)
        if not probe_hls_child_stream(child_url, headers):
            continue

        try:
            child_text = request_text(child_url, headers=headers)
        except Exception:
            continue
        if "#EXT-X-PLAYLIST-TYPE:VOD" in child_text and "#EXT-X-ENDLIST" in child_text:
            return child_url
        if not best_child_url:
            best_child_url = child_url

    return best_child_url or url


def resolve_with_resolveurl(embed_url):
    supported = has_resolver_support(embed_url)
    if not supported:
        raise SourceResolutionError(
            "unsupported_host",
            "A forrás host nem támogatott: {}".format(get_url_host(embed_url) or embed_url),
            embed_url,
        )

    try:
        import resolveurl

        resolved = resolveurl.resolve(embed_url)
    except Exception as exc:
        raise SourceResolutionError(
            "resolver_failed",
            "A forrás feloldása nem sikerült: {}".format(get_url_host(embed_url) or embed_url),
            embed_url,
            cause=exc,
        )

    if not resolved:
        raise SourceResolutionError(
            "resolver_failed",
            "A forrás feloldása nem sikerült: {}".format(get_url_host(embed_url) or embed_url),
            embed_url,
        )

    return ensure_media_result(resolved, input_url=embed_url)


def load_saved_searches():
    ensure_profile_dir()
    if not os.path.exists(SEARCHES_FILE):
        return []
    with open(SEARCHES_FILE, "r", encoding="utf-8") as handle:
        try:
            data = json.load(handle)
        except ValueError:
            return []
    return [item for item in data if isinstance(item, str) and item.strip()]


def save_saved_searches(searches):
    ensure_profile_dir()
    with open(SEARCHES_FILE, "w", encoding="utf-8") as handle:
        json.dump(searches, handle, ensure_ascii=False, indent=2)


def remember_search(query):
    searches = load_saved_searches()
    filtered = [item for item in searches if item.lower() != query.lower()]
    filtered.insert(0, query)
    save_saved_searches(filtered[:50])


def delete_saved_search(query):
    searches = load_saved_searches()
    searches = [item for item in searches if item.lower() != query.lower()]
    save_saved_searches(searches)


def load_update_state():
    ensure_profile_dir()
    if not os.path.exists(UPDATE_STATE_FILE):
        return {}
    with open(UPDATE_STATE_FILE, "r", encoding="utf-8") as handle:
        try:
            data = json.load(handle)
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}


def save_update_state(state):
    ensure_profile_dir()
    with open(UPDATE_STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)


def parse_version_tuple(version):
    try:
        return tuple(int(part) for part in (version or "").split("."))
    except ValueError:
        return (0,)


def fetch_repo_plugin_version():
    try:
        addons_xml = request_text(REPO_ADDONS_URL, headers={"Referer": REPO_ADDONS_URL})
        root = ET.fromstring(addons_xml)
    except Exception as exc:
        log("repo version fetch failed: {}".format(exc))
        return ""

    for addon in root.findall("addon"):
        if addon.attrib.get("id") == ADDON.getAddonInfo("id"):
            return addon.attrib.get("version", "")
    return ""


def maybe_refresh_repository(force=False):
    state = load_update_state()
    now = int(time.time())
    last_refresh = int(state.get("last_repo_refresh", 0) or 0)
    installed_version = ADDON.getAddonInfo("version")
    remote_version = fetch_repo_plugin_version()

    if remote_version and parse_version_tuple(remote_version) > parse_version_tuple(installed_version):
        if state.get("last_notified_version") != remote_version:
            xbmcgui.Dialog().notification(
                "Rombi TV",
                "Frissites elerheto: {}".format(remote_version),
                xbmcgui.NOTIFICATION_INFO,
                5000,
            )
            state["last_notified_version"] = remote_version
            save_update_state(state)
        force = True

    if not force and (now - last_refresh) < REPO_REFRESH_INTERVAL_SECONDS:
        return

    log("triggering Kodi repository refresh")
    xbmc.executebuiltin("UpdateAddonRepos")
    xbmc.executebuiltin("UpdateLocalAddons")
    state["last_repo_refresh"] = now
    save_update_state(state)


def add_directory_item(label, target_query, is_folder=True, art=None, info=None, context=None):
    item = xbmcgui.ListItem(label=label)
    if art:
        item.setArt(art)
    if info:
        item.setInfo("video", info)
    if context:
        item.addContextMenuItems(context)
    xbmcplugin.addDirectoryItem(
        handle=ADDON_HANDLE,
        url=build_url(**target_query),
        listitem=item,
        isFolder=is_folder,
    )


def add_playable_item(label, target_query, art=None, info=None):
    item = xbmcgui.ListItem(label=label)
    item.setProperty("IsPlayable", "true")
    if art:
        item.setArt(art)
    if info:
        item.setInfo("video", info)
    xbmcplugin.addDirectoryItem(
        handle=ADDON_HANDLE,
        url=build_url(**target_query),
        listitem=item,
        isFolder=False,
    )


def parse_page_count(page_html):
    match = re.search(r"Page\s+\d+\s+of\s+(\d+)", page_html, re.IGNORECASE)
    return int(match.group(1)) if match else 1


def fetch_all_pages(first_url, next_url_builder):
    progress = xbmcgui.DialogProgress()
    progress.create("HDMozi", "Találatok betöltése...")
    pages = []
    try:
        first_page = request_text(first_url)
        pages.append(first_page)
        total_pages = parse_page_count(first_page)

        for page_index in range(2, total_pages + 1):
            percent = int(((page_index - 1) / float(max(total_pages, 1))) * 100)
            progress.update(percent, "Oldal {} / {}".format(page_index - 1, total_pages))
            if progress.iscanceled():
                break
            pages.append(request_text(next_url_builder(page_index)))
    finally:
        progress.close()
    return pages


def parse_search_results(page_html):
    results = []
    pattern = re.compile(
        r"<div class=\"result-item\">\s*<article>(.*?)</article>\s*</div>",
        re.DOTALL,
    )
    for block in pattern.findall(page_html):
        link_match = re.search(r'<a href="([^"]+)"', block)
        title_match = re.search(r'<div class="title">\s*<a [^>]+>(.*?)</a>', block, re.DOTALL)
        image_match = re.search(r'<img src="([^"]+)"', block)
        plot_match = re.search(r'<div class="contenido">.*?<p>(.*?)</p>', block, re.DOTALL)
        if not link_match or not title_match:
            continue
        url = link_match.group(1)
        results.append({
            "label": strip_tags(title_match.group(1)),
            "url": url,
            "thumb": normalize_url(image_match.group(1)) if image_match else "",
            "plot": strip_tags(plot_match.group(1)) if plot_match else "",
            "kind": "tvshow" if "/tvshows/" in url else "movie",
        })
    return results


def parse_category_results(page_html):
    results = []
    pattern = re.compile(
        r'<article id="post-(\d+)" class="item ([^"]+)">(.*?)</article>',
        re.DOTALL,
    )
    for _, item_class, block in pattern.findall(page_html):
        link_match = re.search(r'<a href="([^"]+)"', block)
        title_match = re.search(r'<img [^>]*alt="([^"]+)"', block)
        image_match = re.search(r'<img src="([^"]+)"', block)
        if not link_match or not title_match:
            continue
        results.append({
            "label": html.unescape(title_match.group(1)).strip(),
            "url": link_match.group(1),
            "thumb": normalize_url(image_match.group(1)) if image_match else "",
            "kind": "tvshow" if "tvshows" in item_class else "movie",
        })
    return results


def parse_categories(page_html):
    categories = {}
    for href, name in re.findall(r'href="(https://hdmozi\.hu/genre/[^\"]+/)"[^>]*>([^<]+)</a>', page_html):
        slug = href.rstrip("/").split("/")[-1]
        clean_name = html.unescape(name).strip()
        if not clean_name:
            continue
        categories[slug] = {"name": clean_name, "url": href}
    return sorted(categories.values(), key=lambda item: item["name"].lower())


def parse_movie_sources(page_html):
    sources = []
    pattern = re.compile(
        r"<li id='player-option-([^']+)' class='dooplay_player_option' data-type='([^']+)' data-post='(\d+)' data-nume='([^']+)'>(.*?)</li>",
        re.DOTALL,
    )
    for _, source_type, post_id, nume, block in pattern.findall(page_html):
        if nume == "trailer":
            continue
        title_match = re.search(r"<span class='title'>(.*?)</span>", block, re.DOTALL)
        title = strip_tags(title_match.group(1)) if title_match else "Lejátszás"
        sources.append({
            "label": title,
            "source_type": source_type,
            "post_id": post_id,
            "nume": nume,
        })
    return sources


def parse_show_episodes(page_html):
    episodes = []
    episode_pattern = re.compile(
        r"<li class='mark-\d+'.*?<div class='numerando'>(.*?)</div>.*?<div class='episodiotitle'><a href='([^']+)'>(.*?)</a>.*?</li>",
        re.DOTALL,
    )
    for numbering, url, title in episode_pattern.findall(page_html):
        episodes.append({
            "label": "{} - {}".format(strip_tags(numbering), strip_tags(title)),
            "url": url,
        })
    return episodes


def parse_detail_metadata(page_html):
    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", page_html, re.DOTALL)
    plot_match = re.search(r'<div itemprop="description" class="wp-content">\s*<p>(.*?)</p>', page_html, re.DOTALL)
    poster_match = re.search(r'<div class="poster">\s*<img [^>]*src="([^"]+)"', page_html, re.DOTALL)
    return {
        "title": strip_tags(title_match.group(1)) if title_match else "",
        "plot": strip_tags(plot_match.group(1)) if plot_match else "",
        "poster": normalize_url(poster_match.group(1)) if poster_match else "",
    }


def prompt_search():
    keyboard = xbmc.Keyboard("", "Keresés")
    keyboard.doModal()
    if not keyboard.isConfirmed():
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
        return
    query = keyboard.getText().strip()
    if not query:
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
        return
    remember_search(query)
    xbmc.executebuiltin("Container.Update({})".format(build_url(action="search_results", query=query)))
    xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)


def list_root():
    add_directory_item("hdmozi.hu", {"action": "hd_root"})
    add_directory_item("Sorozat.cc", {"action": "sc_root"})
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_home():
    add_directory_item("Keresés", {"action": "prompt_search"}, is_folder=False)
    add_directory_item("Mentett keresések", {"action": "saved_searches"})
    add_directory_item("Kategóriák", {"action": "categories"})
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_saved_searches():
    searches = load_saved_searches()
    for query in searches:
        add_directory_item(
            query,
            {"action": "search_results", "query": query},
            context=[(
                "Törlés a mentett keresésekből",
                "RunPlugin({})".format(build_url(action="delete_saved_search", query=query)),
            )],
        )
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def run_search_results(query):
    pages = fetch_all_pages(
        "{}/?s={}".format(SITE_URL, urllib.parse.quote_plus(query)),
        lambda page: "{}/page/{}/?s={}".format(SITE_URL, page, urllib.parse.quote_plus(query)),
    )
    for result in [item for page in pages for item in parse_search_results(page)]:
        add_result_item(result)
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_categories():
    categories = parse_categories(request_text(SITE_URL + "/?s=halo"))
    for category in categories:
        add_directory_item(category["name"], {
            "action": "category_results",
            "name": category["name"],
            "url": category["url"],
        })
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def run_category_results(name, category_url):
    base = category_url.rstrip("/")
    pages = fetch_all_pages(base + "/", lambda page: "{}/page/{}/".format(base, page))
    for result in [item for page in pages for item in parse_category_results(page)]:
        add_result_item(result)
    xbmcplugin.setPluginCategory(ADDON_HANDLE, name)
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def add_result_item(result):
    info = {"title": result["label"], "plot": result.get("plot", "")}
    art = {"thumb": result.get("thumb", ""), "poster": result.get("thumb", "")}
    if result["kind"] == "tvshow":
        add_directory_item(result["label"], {"action": "show_detail", "url": result["url"]}, art=art, info=info)
    else:
        add_directory_item(result["label"], {"action": "movie_detail", "url": result["url"]}, art=art, info=info)


def list_movie_detail(url):
    page_html = request_text(url)
    meta = parse_detail_metadata(page_html)
    art = {"thumb": meta["poster"], "poster": meta["poster"]}
    info = {"title": meta["title"], "plot": meta["plot"]}
    for source in parse_movie_sources(page_html):
        add_playable_item(source["label"], {
            "action": "play_source",
            "post_id": source["post_id"],
            "source_type": source["source_type"],
            "nume": source["nume"],
            "page_url": url,
        }, art=art, info=info)
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_show_detail(url):
    page_html = request_text(url)
    meta = parse_detail_metadata(page_html)
    art = {"thumb": meta["poster"], "poster": meta["poster"]}
    info = {"tvshowtitle": meta["title"], "plot": meta["plot"]}
    for episode in parse_show_episodes(page_html):
        add_directory_item(episode["label"], {
            "action": "episode_detail",
            "url": episode["url"],
        }, art=art, info=info)
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_episode_detail(url):
    page_html = request_text(url)
    meta = parse_detail_metadata(page_html)
    art = {"thumb": meta["poster"], "poster": meta["poster"]}
    info = {"title": meta["title"], "plot": meta["plot"]}
    for source in parse_movie_sources(page_html):
        add_playable_item(source["label"], {
            "action": "play_source",
            "post_id": source["post_id"],
            "source_type": source["source_type"],
            "nume": source["nume"],
            "page_url": url,
        }, art=art, info=info)
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def resolve_admin_ajax_source(post_id, source_type, nume):
    response = request_json(
        SITE_URL + "/wp-admin/admin-ajax.php",
        data={
            "action": "doo_player_ajax",
            "post": post_id,
            "nume": nume,
            "type": source_type,
        },
    )
    return normalize_url(response.get("embed_url"))


def rc4_decrypt(cipher_text, key):
    s = list(range(256))
    j = 0
    out = []
    key_length = len(key)

    for i in range(256):
        j = (j + s[i] + ord(key[i % key_length])) % 256
        s[i], s[j] = s[j], s[i]

    i = 0
    j = 0
    for byte in cipher_text:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out.append(byte ^ s[(s[i] + s[j]) % 256])

    return bytes(out).decode("utf-8", "replace")


def resolve_videa(player_url):
    player_html = request_text(player_url)
    nonce_match = re.search(r'_xt\s*=\s*"([^"]+)"', player_html)
    if not nonce_match:
        raise ValueError("A Videa nonce nem található")

    nonce = nonce_match.group(1)
    left = nonce[:32]
    right = nonce[32:]
    result = ""
    for index in range(32):
        result += right[index - (VIDEA_STATIC_SECRET.index(left[index]) - 31)]

    parsed = urllib.parse.urlparse(player_url)
    query = dict(urllib.parse.parse_qsl(parsed.query))
    if "f" not in query and "v" not in query:
        raise ValueError("A Videa azonosító nem található")
    random_seed = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(8))
    query["_s"] = random_seed
    query["_t"] = result[:16]
    query["platform"] = "desktop"

    xml_text, headers = request_text(
        "https://videa.hu/player/xml?{}".format(urllib.parse.urlencode(query)),
        headers={"Referer": player_url},
        return_headers=True,
    )

    if xml_text.startswith("<?xml"):
        xml_payload = xml_text
    else:
        rc4_key = result[16:] + random_seed + headers.get("x-videa-xs", "")
        xml_payload = rc4_decrypt(base64.b64decode(xml_text), rc4_key)

    error_url_match = re.search(r"<error[^>]*>(https://videa\.hu/[^<]+)</error>", xml_payload)
    if error_url_match:
        error_page_url = normalize_url(error_url_match.group(1))
        error_page_html = request_text(error_page_url, headers={"Referer": player_url})
        iframe_match = re.search(r'id="videa_player_iframe"[^>]+src="([^"]+)"', error_page_html)
        if iframe_match:
            iframe_url = urllib.parse.urljoin(error_page_url, html.unescape(iframe_match.group(1)))
            if iframe_url != player_url:
                return resolve_videa(iframe_url)

    formats = []
    for name, expires, source_url in re.findall(
        r'video_source\s*name="([^"]+)"[^>]*exp="([^"]+)"[^>]*>([^<]+)',
        xml_payload,
    ):
        hash_match = re.search(r"<hash_value_{}>([^<]+)<".format(re.escape(name)), xml_payload)
        hash_value = hash_match.group(1) if hash_match else None
        if hash_value and expires:
            separator = "&" if "?" in source_url else "?"
            source_url = "{}{}md5={}&expires={}".format(source_url, separator, hash_value, expires)
        height_match = re.search(
            r'video_source\s*name="{}"[^>]*height="(\d+)"'.format(re.escape(name)),
            xml_payload,
        )
        formats.append({
            "url": normalize_url(source_url).replace("&amp;", "&"),
            "height": int(height_match.group(1)) if height_match else 0,
        })

    if not formats:
        root = ET.fromstring(xml_payload)
        video = root.find("./video")
        if video is None:
            raise ValueError("A Videa videóinformáció nem található")

        sources = root.find("./video_sources")
        hashes = root.find("./hash_values")
        for source in sources.findall("./video_source") if sources is not None else []:
            source_url = source.text or ""
            name = source.get("name")
            expires = source.get("exp")
            hash_value = hashes.findtext("hash_value_{}".format(name)) if hashes is not None else None
            if hash_value and expires:
                separator = "&" if "?" in source_url else "?"
                source_url = "{}{}md5={}&expires={}".format(source_url, separator, hash_value, expires)
            formats.append({
                "url": normalize_url(source_url).replace("&amp;", "&"),
                "height": int(source.get("height") or 0),
            })

    if not formats:
        raise ValueError("A Videa források nem találhatók")

    best = select_best_format(formats)
    return {"url": best["url"]}


def rpm_char_codes(*codes):
    return "".join(chr(int(code)) for code in codes)


def rpm_key(protocol):
    return b"kiemtienmua911ca"


def rpm_iv(protocol, fragment):
    return b"1234567890oiuytr"


def rpm_decrypt(hex_text, protocol, fragment):
    encrypted = binascii.unhexlify(hex_text.strip())
    decrypter = Decrypter(AESModeOfOperationCBC(rpm_key(protocol), rpm_iv(protocol, fragment)))
    text = (decrypter.feed(encrypted) + decrypter.feed()).decode("utf-8", "replace").strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]
    try:
        return json.loads(text)
    except ValueError:
        log("rpm_decrypt raw head: {}".format(text[:400].encode("unicode_escape").decode("ascii")))
        normalized = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', text)
        normalized = normalized.replace("'", '"')
        normalized = re.sub(r",\s*}", "}", normalized)
        normalized = re.sub(r",\s*]", "]", normalized)
        log("rpm_decrypt normalized head: {}".format(normalized[:400].encode("unicode_escape").decode("ascii")))
        return json.loads(normalized)


def fetch_rpm_payload(api_url, embed_url, origin, protocol, fragment):
    hex_text = request_text(
        api_url,
        headers={
            "Referer": embed_url,
            "Origin": origin,
        },
    )
    return rpm_decrypt(hex_text, protocol, fragment)


def resolve_rpmshare(embed_url):
    parsed = urllib.parse.urlparse(embed_url)
    origin = "{}://{}".format(parsed.scheme, parsed.netloc)
    api_url = "{}/api/v1/video?id={}&w=1920&h=1080&r=hdmozi.hu".format(
        origin,
        parsed.fragment,
    )
    protocol = parsed.scheme + ":"
    fragment = parsed.fragment and ("#" + parsed.fragment) or parsed.fragment

    for attempt in range(1, RPM_MAX_ATTEMPTS + 1):
        payload = fetch_rpm_payload(api_url, embed_url, origin, protocol, fragment)
        order = payload.get("streamingConfig")
        raw_order = payload.get("streamingConfigRaw")

        config = {"order": ["Cloudflare", "Tiktok"], "adjust": {}}
        if raw_order and isinstance(raw_order, str) and "::" in raw_order:
            token, expiry = raw_order.split("::", 1)
            config["adjust"]["Cloudflare"] = {"params": {"t": token, "e": expiry}}
        if order:
            try:
                parsed_config = json.loads(order)
                if isinstance(parsed_config, dict) and isinstance(parsed_config.get("order"), list):
                    config = parsed_config
                    config.setdefault("adjust", {})
            except ValueError:
                pass

        source_map = {
            "In-House": payload.get("source"),
            "Google": payload.get("hlsVideoGoogle"),
            "Tiktok": payload.get("hlsVideoTiktok") or payload.get("tt"),
            "Cloudflare": payload.get("cf"),
        }

        configured_order = config.get("order", [])
        preferred_order = ["Cloudflare", "In-House", "Google", "Tiktok"] + configured_order
        merged_order = []
        for source_name in preferred_order:
            if source_name not in merged_order:
                merged_order.append(source_name)

        for source_name in merged_order:
            source_url = source_map.get(source_name)
            if not source_url:
                continue
            adjust = config.get("adjust", {}).get(source_name, {})
            if adjust.get("disabled"):
                continue
            normalized_source = urllib.parse.urljoin(origin + "/", normalize_url(source_url))
            parsed_source = urllib.parse.urlparse(normalized_source)
            source_query = dict(urllib.parse.parse_qsl(parsed_source.query))
            source_query.update(adjust.get("params", {}))
            final_url = urllib.parse.urlunparse((
                parsed_source.scheme,
                parsed_source.netloc,
                parsed_source.path,
                parsed_source.params,
                urllib.parse.urlencode(source_query),
                parsed_source.fragment,
            ))
            stream_headers = {
                "Referer": embed_url,
                "Origin": origin,
                "User-Agent": USER_AGENT,
            }
            manifest_type = None
            if is_hls_like_url(final_url):
                try:
                    final_url = resolve_best_hls_url(
                        final_url,
                        stream_headers,
                        prefer_vod_child=(source_name in ("Cloudflare", "Tiktok")),
                    )
                except Exception as exc:
                    log("hls selection failed url={} cause={}".format(final_url, exc))
                    continue
                if not probe_hls_manifest(final_url, stream_headers):
                    continue
                manifest_type = "hls"
            return {
                "url": final_url,
                "headers": stream_headers,
                "manifest_type": manifest_type,
            }

        if attempt < RPM_MAX_ATTEMPTS:
            log("rpm retry {}/{} fragment={}".format(attempt, RPM_MAX_ATTEMPTS, parsed.fragment))
            time.sleep(RPM_RETRY_DELAY_SECONDS)

    raise ValueError("Az RPMStream forrás nem található")


def resolve_okru(embed_url):
    match = re.search(r"/(?:videoembed|video|live)/(\d+)", embed_url)
    if not match:
        raise ValueError("Az OK.ru azonosító nem található")

    media_id = match.group(1)
    metadata = request_json(
        "http://www.ok.ru/dk?cmd=videoPlayerMetadata",
        data={"mid": media_id},
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64; Trident/7.0; rv:11.0) like Gecko",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": embed_url,
        },
    )

    hls_url = metadata.get("hlsManifestUrl")
    stream_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64; Trident/7.0; rv:11.0) like Gecko",
        "Referer": embed_url,
    }
    if hls_url:
        return {"url": normalize_url(hls_url), "headers": stream_headers}

    quality_map = {"ultra": 2160, "quad": 1440, "full": 1080, "hd": 720, "sd": 480, "low": 360, "lowest": 240, "mobile": 144}
    formats = []
    for entry in metadata.get("videos", []):
        source_url = entry.get("url")
        if not source_url:
            continue
        formats.append({
            "url": normalize_url(source_url),
            "height": quality_map.get((entry.get("name") or "").lower(), 0),
        })

    if not formats:
        raise ValueError("Az OK.ru forrás nem található")

    best = select_best_format(formats)
    return {"url": best["url"], "headers": stream_headers}


def resolve_vk(embed_url):
    parsed = urllib.parse.urlparse(embed_url)
    host = parsed.netloc
    ref = "https://{}/".format(host)
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": ref,
        "Origin": ref[:-1],
    }

    media_id = parsed.query if parsed.path.endswith("video_ext.php") else parsed.path.strip("/")
    query = urllib.parse.parse_qs(media_id)
    oid = ""
    video_id = ""
    video_list = ""
    if "oid" in query and "id" in query:
        oid = query["oid"][0]
        video_id = query["id"][0]
        video_list = query.get("list", [""])[0]
    elif "_" in media_id:
        oid, video_id = media_id.split("_", 1)
        if "list=" in media_id:
            video_id, video_list = video_id.split("list=", 1)
            video_id = video_id.rstrip("?&")
    else:
        raise ValueError("A VK azonosító nem található")

    oid = oid.replace("video", "")
    ajax_headers = dict(headers)
    ajax_headers["X-Requested-With"] = "XMLHttpRequest"
    payload_text = request_text(
        "https://{}/al_video.php?act=show".format(host),
        data={
            "act": "show",
            "al": 1,
            "video": "{}_{}".format(oid, video_id),
            "list": video_list,
            "load_playlist": 1 if video_list else "",
            "module": "direct" if video_list else "",
            "show_next": 1 if video_list else "",
            "playlist_id": "{}_-2".format(oid) if video_list else "",
        },
        headers=ajax_headers,
    )

    if payload_text.startswith("<!--"):
        payload_text = payload_text[4:]
    payload_json = json.loads(payload_text)
    player_data = {}
    for item in payload_json.get("payload", []):
        if isinstance(item, list):
            for nested in item:
                if isinstance(nested, dict) and nested.get("player"):
                    player_data = nested.get("player", {}).get("params", [{}])[0]

    if not player_data:
        page_html = request_text(embed_url, headers=headers)
        match = re.search(r"var\s*playerParams\s*=\s*(.+?});", page_html)
        if match:
            fallback_data = json.loads(match.group(1))
            player_data = fallback_data.get("params", [{}])[0]

    formats = []
    for key, value in player_data.items():
        if key.startswith("url") and value:
            try:
                height = int(key[3:])
            except ValueError:
                height = 0
            formats.append({"url": normalize_url(value), "height": height})

    hls_url = player_data.get("hls") or player_data.get("hls_live") or player_data.get("hls_ondemand")
    if hls_url:
        return {"url": normalize_url(hls_url), "headers": headers}

    if not formats:
        raise ValueError("A VK forrás nem található")

    best = select_best_format(formats)
    return {"url": best["url"], "headers": headers}


def resolve_sbot(embed_url):
    parsed = urllib.parse.urlparse(embed_url)
    token = dict(urllib.parse.parse_qsl(parsed.query)).get("k")
    if not token:
        raise ValueError("Az sbot.cf token nem található")

    redirect_url = "https://sorozatok.net/api/core.php?a=redirect&id={}".format(token)
    req = urllib.request.Request(
        redirect_url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": embed_url,
            "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        final_url = response.geturl()

    final_url = normalize_url(final_url)
    if final_url == redirect_url or final_url == embed_url:
        raise SourceResolutionError("resolver_failed", "Az sbot.cf nem adott vissza lejátszható linket", embed_url)
    if "sorozatok.net/watch.php" in final_url:
        watch_html = request_text(final_url, headers={"Referer": embed_url})
        iframe_match = re.search(r'<iframe[^>]+src="([^"]*embed\.php[^\"]+)"', watch_html)
        if iframe_match:
            final_url = normalize_url(urllib.parse.urljoin(final_url, iframe_match.group(1)))
    if is_special_embed_page(final_url):
        raise SourceResolutionError(
            "embed_page",
            "A sorozatok.net csak embed oldalt adott vissza, nem közvetlen videó linket",
            final_url,
        )
    return resolve_embed_url(final_url)


def resolve_filemoon(embed_url):
    return resolve_with_resolveurl(embed_url)


def resolve_youtube(embed_url):
    match = re.search(r"/(?:embed/|watch\?v=)([A-Za-z0-9_-]{11})", embed_url)
    if not match:
        raise ValueError("A YouTube azonosító nem található")
    return {"url": "plugin://plugin.video.youtube/play/?video_id={}".format(match.group(1))}


def build_header_string(headers):
    return "&".join(
        "{}={}".format(urllib.parse.quote_plus(key), urllib.parse.quote_plus(value))
        for key, value in headers.items()
    )


def resolve_embed_url(embed_url):
    if not embed_url:
        raise SourceResolutionError("resolver_failed", "Üres embed URL", embed_url)
    embed_url = normalize_url(embed_url)

    if "youtube.com/" in embed_url or "youtu.be/" in embed_url:
        return resolve_youtube(embed_url)
    if is_media_url(embed_url):
        return ensure_media_result(embed_url)
    if "videa.hu/player" in embed_url:
        return resolve_videa(embed_url)
    if "rpmshare.rpmstream.live" in embed_url:
        return resolve_rpmshare(embed_url)
    if "ok.ru/" in embed_url or "odnoklassniki.ru/" in embed_url:
        return resolve_okru(embed_url)
    if "vk.com/" in embed_url or "vkvideo.ru/" in embed_url:
        return resolve_vk(embed_url)
    if "sbot.cf/" in embed_url:
        return resolve_sbot(embed_url)
    if "filemoon.sx/" in embed_url:
        return resolve_filemoon(embed_url)

    if is_special_embed_page(embed_url):
        raise SourceResolutionError(
            "embed_page",
            "Az embed HTML oldal nem adható át lejátszási URL-ként: {}".format(embed_url),
            embed_url,
        )

    return resolve_with_resolveurl(embed_url)


def play_source(post_id, source_type, nume):
    try:
        embed_url = resolve_admin_ajax_source(post_id, source_type, nume)
        resolved = resolve_embed_url(embed_url)
        stream_url = resolved["url"]
        headers = resolved.get("headers") or {}
        item_path = stream_url
        if headers:
            header_string = build_header_string(headers)
            item_path = stream_url + "|" + header_string
        item = xbmcgui.ListItem(path=item_path)
        manifest_type = resolved.get("manifest_type")
        if manifest_type == "hls" or ".m3u8" in strip_url_headers(resolved["url"]):
            header_string = build_header_string(headers)
            item.setMimeType("application/vnd.apple.mpegurl")
            item.setContentLookup(False)
            item.setProperty("inputstream", "inputstream.adaptive")
            item.setProperty("inputstream.adaptive.manifest_type", "hls")
            max_bandwidth = get_max_stream_bandwidth()
            if max_bandwidth:
                item.setProperty("inputstream.adaptive.max_bandwidth", str(max_bandwidth))
            if header_string:
                item.setProperty("inputstream.adaptive.manifest_headers", header_string)
                item.setProperty("inputstream.adaptive.stream_headers", header_string)
        xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, item)
    except SourceResolutionError as exc:
        log("source resolve failed [{}] host={} url={} cause={}".format(exc.kind, exc.host, exc.url, exc.cause or ""))
        xbmcgui.Dialog().notification("HDMozi", str(exc), xbmcgui.NOTIFICATION_ERROR, 5000)
        xbmcplugin.setResolvedUrl(ADDON_HANDLE, False, xbmcgui.ListItem())
    except Exception as exc:
        log("play_source failed: {}\n{}".format(exc, traceback.format_exc()))
        xbmcgui.Dialog().notification("HDMozi", str(exc), xbmcgui.NOTIFICATION_ERROR, 5000)
        xbmcplugin.setResolvedUrl(ADDON_HANDLE, False, xbmcgui.ListItem())


def router(params):
    action = params.get("action")

    if action in (None, "home", "hd_root"):
        maybe_refresh_repository()

    if action and action.startswith("sc_"):
        sorozatcc.router(params)
        return

    if not action:
        list_root()
        return
    if action == "prompt_search":
        prompt_search()
        return
    if action == "hd_root" or action == "home":
        list_home()
        return
    if action == "saved_searches":
        list_saved_searches()
        return
    if action == "search_results":
        run_search_results(params.get("query", ""))
        return
    if action == "delete_saved_search":
        delete_saved_search(params.get("query", ""))
        xbmc.executebuiltin("Container.Refresh")
        return
    if action == "categories":
        list_categories()
        return
    if action == "category_results":
        run_category_results(params.get("name", "Kategória"), params.get("url", SITE_URL))
        return
    if action == "movie_detail":
        list_movie_detail(params["url"])
        return
    if action == "show_detail":
        list_show_detail(params["url"])
        return
    if action == "episode_detail":
        list_episode_detail(params["url"])
        return
    if action == "play_source":
        play_source(params["post_id"], params["source_type"], params["nume"])
        return

    xbmcgui.Dialog().notification("HDMozi", "Ismeretlen művelet", xbmcgui.NOTIFICATION_ERROR, 3000)


if __name__ == "__main__":
    router(dict(urllib.parse.parse_qsl(sys.argv[2][1:])))
