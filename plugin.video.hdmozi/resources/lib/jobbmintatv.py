from __future__ import unicode_literals

import html
import json
import os
import re
import sys
import traceback
import urllib.parse
import urllib.request

import xbmc
import xbmcgui
import xbmcplugin
import xbmcvfs


ADDON_HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]
PROFILE_DIR = ""
SEARCHES_FILE = ""
ACTION_PREFIX = "jmtv_"
EMBED_RESOLVER = None
SITE_URL = "https://jobbmintatv.pro"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def configure(base_url=None, addon_handle=None, profile_dir=None, action_prefix=None, embed_resolver=None):
    global BASE_URL, ADDON_HANDLE, PROFILE_DIR, SEARCHES_FILE, ACTION_PREFIX, EMBED_RESOLVER
    if base_url is not None:
        BASE_URL = base_url
    if addon_handle is not None:
        ADDON_HANDLE = addon_handle
    if profile_dir is not None:
        PROFILE_DIR = profile_dir
        SEARCHES_FILE = os.path.join(PROFILE_DIR, "saved_searches_jobbmintatv.json")
    if action_prefix is not None:
        ACTION_PREFIX = action_prefix
    if embed_resolver is not None:
        EMBED_RESOLVER = embed_resolver


def log(message):
    xbmc.log("[plugin.video.hdmozi][jobbmintatv] {}".format(message), xbmc.LOGINFO)


def build_url(**query):
    action = query.get("action")
    if action and not action.startswith(ACTION_PREFIX):
        query["action"] = ACTION_PREFIX + action
    return "{}?{}".format(BASE_URL, urllib.parse.urlencode(query))


def request_text(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": SITE_URL + "/",
            "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        body = response.read()
        content_type = response.headers.get("Content-Type") or ""
    encoding = "windows-1250" if "windows-1250" in content_type.lower() else "utf-8"
    return body.decode(encoding, "replace")


def normalize_url(url):
    url = html.unescape((url or "").strip())
    if url.startswith("//"):
        return "https:" + url
    return urllib.parse.urljoin(SITE_URL + "/", url)


def strip_tags(value):
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def add_directory_item(label, target_query, is_folder=True, art=None, info=None, context=None):
    item = xbmcgui.ListItem(label=label)
    if art:
        item.setArt(art)
    if info:
        item.setInfo("video", info)
    if context:
        item.addContextMenuItems(context)
    xbmcplugin.addDirectoryItem(ADDON_HANDLE, build_url(**target_query), item, is_folder)


def add_playable_item(label, embed_url, art=None, info=None):
    item = xbmcgui.ListItem(label=label)
    item.setProperty("IsPlayable", "true")
    if art:
        item.setArt(art)
    if info:
        item.setInfo("video", info)
    xbmcplugin.addDirectoryItem(
        ADDON_HANDLE,
        build_url(action="play", embed_url=embed_url),
        item,
        False,
    )


def load_saved_searches():
    if not SEARCHES_FILE or not os.path.exists(SEARCHES_FILE):
        return []
    try:
        with open(SEARCHES_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return [value for value in data if isinstance(value, str) and value.strip()]
    except (OSError, ValueError):
        return []


def save_saved_searches(searches):
    if PROFILE_DIR and not xbmcvfs.exists(PROFILE_DIR):
        xbmcvfs.mkdirs(PROFILE_DIR)
    with open(SEARCHES_FILE, "w", encoding="utf-8") as handle:
        json.dump(searches[:50], handle, ensure_ascii=False, indent=2)


def remember_search(query):
    searches = [value for value in load_saved_searches() if value.lower() != query.lower()]
    save_saved_searches([query] + searches)


def delete_saved_search(query):
    save_saved_searches([value for value in load_saved_searches() if value.lower() != query.lower()])


def parse_cards(page_html):
    cards = []
    pattern = re.compile(
        r"<div class='eltesz_lista_bor'>\s*<a class='kocka' href='([^']+)'>"
        r".*?<img src='([^']+)'>\s*<span class='cimk'>(.*?)</span>\s*</a>"
        r"\s*<span class='egyebekk'>(.*?)</span>",
        re.DOTALL,
    )
    for url, image, title, extra in pattern.findall(page_html):
        year_match = re.search(r"\b(19|20)\d{2}\b", strip_tags(extra))
        cards.append({
            "url": normalize_url(url),
            "thumb": normalize_url(image),
            "title": strip_tags(title),
            "year": year_match.group(0) if year_match else "",
            "plot": strip_tags(extra),
        })
    return cards


def parse_search_results(page_html):
    results = []
    pattern = re.compile(
        r'<a href="([^"]+)">\s*<div[^>]*>\s*'
        r'(?:<img[^>]+src="([^"]+)"[^>]*>)?\s*'
        r'<b[^>]*>(.*?)</b>(.*?)</div>\s*</a>',
        re.DOTALL,
    )
    for url, image, title, extra in pattern.findall(page_html):
        results.append({
            "url": normalize_url(url),
            "thumb": normalize_url(image) if image else "",
            "title": strip_tags(title),
            "plot": strip_tags(extra),
        })
    return results


def parse_metadata(page_html):
    title_match = re.search(r'<div id="sorozat_adatlap">.*?<h1>(.*?)</h1>', page_html, re.DOTALL)
    if not title_match:
        title_match = re.search(r"<h1[^>]*>(.*?)</h1>", page_html, re.DOTALL)
    plot_match = re.search(r'<div id="sorozat_adatlap">.*?<p>(.*?)</p>', page_html, re.DOTALL)
    poster_match = re.search(r'<img[^>]+src="([^"]+_big\.jpg[^"]*)"', page_html, re.IGNORECASE)
    return {
        "title": strip_tags(title_match.group(1)) if title_match else "",
        "plot": strip_tags(plot_match.group(1)) if plot_match else "",
        "poster": normalize_url(poster_match.group(1)) if poster_match else "",
    }


def parse_iframes(page_html):
    results = []
    seen = set()
    for url in re.findall(r'<iframe[^>]+src=["\']([^"\']+)', page_html, re.IGNORECASE):
        url = normalize_url(url)
        if url and url not in seen:
            seen.add(url)
            results.append(url)
    return results


def parse_seasons(page_html):
    seasons = []
    seen = set()
    pattern = re.compile(r'class="evadoks" href="([^"]+/(\d+)_evad/)"', re.IGNORECASE)
    for url, number in pattern.findall(page_html):
        url = normalize_url(url)
        if url not in seen:
            seen.add(url)
            seasons.append((int(number), url))
    return sorted(seasons)


def parse_episodes(page_html):
    episodes = []
    seen = set()
    pattern = re.compile(r'class="(?:reszeks|reszek)" href="([^"]+/(\d+)_resz/)"', re.IGNORECASE)
    for url, number in pattern.findall(page_html):
        url = normalize_url(url)
        if url not in seen:
            seen.add(url)
            episodes.append((int(number), url))
    return sorted(episodes)


def parse_next_page(page_html):
    match = re.search(r"href='([^']+)'[^>]*>következő oldal</a>", page_html, re.IGNORECASE)
    return normalize_url(match.group(1)) if match else ""


def parse_categories(page_html, kind):
    base = "filmek" if kind == "movies" else "sorozatok"
    results = []
    seen = set()
    pattern = re.compile(
        r'<a class=["\']lka["\']\s+href=["\']([^"\']*/{}/[^"\']*)["\']>(.*?)</a>'.format(base),
        re.IGNORECASE,
    )
    for url, name in pattern.findall(page_html):
        clean_name = strip_tags(name)
        normalized = normalize_url(url)
        if clean_name and normalized not in seen:
            seen.add(normalized)
            results.append((clean_name, normalized))
    return results


def list_root():
    add_directory_item("Keresés", {"action": "prompt_search"}, is_folder=False)
    add_directory_item("Mentett keresések", {"action": "saved_searches"})
    add_directory_item("Online filmek", {"action": "list", "kind": "movies", "url": SITE_URL + "/filmek/1/1"})
    add_directory_item("Film kategóriák", {"action": "categories", "kind": "movies"})
    add_directory_item("Online sorozatok", {"action": "list", "kind": "series", "url": SITE_URL + "/sorozatok/1/1"})
    add_directory_item("Sorozat kategóriák", {"action": "categories", "kind": "series"})
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


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
    xbmc.executebuiltin("Container.Update({})".format(build_url(action="search", query=query)))
    xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)


def list_saved_searches():
    for query in load_saved_searches():
        add_directory_item(
            query,
            {"action": "search", "query": query},
            context=[(
                "Törlés a mentett keresésekből",
                "RunPlugin({})".format(build_url(action="delete_search", query=query)),
            )],
        )
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_search(query):
    remember_search(query)
    url = SITE_URL + "/ajax.php?" + urllib.parse.urlencode({"keres2": query})
    for result in parse_search_results(request_text(url)):
        art = {"thumb": result["thumb"], "poster": result["thumb"]}
        info = {"title": result["title"], "plot": result["plot"]}
        add_directory_item(result["title"], {"action": "detail", "url": result["url"]}, art=art, info=info)
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_catalog(kind, url):
    page_html = request_text(url)
    for card in parse_cards(page_html):
        art = {"thumb": card["thumb"], "poster": card["thumb"]}
        info = {"title": card["title"], "year": card["year"], "plot": card["plot"]}
        add_directory_item(card["title"], {"action": "detail", "url": card["url"]}, art=art, info=info)
    next_url = parse_next_page(page_html)
    if next_url:
        add_directory_item("Következő oldal", {"action": "list", "kind": kind, "url": next_url})
    xbmcplugin.setPluginCategory(ADDON_HANDLE, "Online filmek" if kind == "movies" else "Online sorozatok")
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_categories(kind):
    base_url = SITE_URL + ("/filmek/1/1" if kind == "movies" else "/sorozatok/1/1")
    for name, category_url in parse_categories(request_text(base_url), kind):
        add_directory_item(name, {"action": "list", "kind": kind, "url": category_url})
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_detail(url):
    page_html = request_text(url)
    meta = parse_metadata(page_html)
    art = {"thumb": meta["poster"], "poster": meta["poster"]}
    info = {"title": meta["title"], "plot": meta["plot"]}
    seasons = parse_seasons(page_html)
    if seasons:
        for number, season_url in seasons:
            add_directory_item("{}. évad".format(number), {"action": "season", "url": season_url}, art=art, info=info)
    else:
        for index, embed_url in enumerate(parse_iframes(page_html), 1):
            add_playable_item("Lejátszás {}".format(index), embed_url, art=art, info=info)
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_season(url):
    page_html = request_text(url)
    for number, episode_url in parse_episodes(page_html):
        add_directory_item("{}. rész".format(number), {"action": "episode", "url": episode_url})
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_episode(url):
    page_html = request_text(url)
    meta = parse_metadata(page_html)
    info = {"title": meta["title"], "plot": meta["plot"]}
    art = {"thumb": meta["poster"], "poster": meta["poster"]}
    for index, embed_url in enumerate(parse_iframes(page_html), 1):
        add_playable_item("Lejátszás {}".format(index), embed_url, art=art, info=info)
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def play(embed_url):
    try:
        resolved = EMBED_RESOLVER(embed_url) if EMBED_RESOLVER else {"url": embed_url}
        if not isinstance(resolved, dict):
            resolved = {"url": resolved}
        stream_url = resolved.get("url", "")
        headers = resolved.get("headers") or {}
        item_path = stream_url
        if headers:
            item_path += "|" + "&".join(
                "{}={}".format(urllib.parse.quote_plus(key), urllib.parse.quote_plus(value))
                for key, value in headers.items()
            )
        item = xbmcgui.ListItem(path=item_path)
        if resolved.get("manifest_type") == "hls" or ".m3u8" in stream_url:
            item.setMimeType("application/vnd.apple.mpegurl")
            item.setContentLookup(False)
            if not resolved.get("native_hls"):
                item.setProperty("inputstream", "inputstream.adaptive")
                item.setProperty("inputstream.adaptive.manifest_type", "hls")
        xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, item)
    except Exception as exc:
        log("play failed: {}\n{}".format(exc, traceback.format_exc()))
        xbmcgui.Dialog().notification("JobbMintATV", str(exc), xbmcgui.NOTIFICATION_ERROR, 5000)
        xbmcplugin.setResolvedUrl(ADDON_HANDLE, False, xbmcgui.ListItem())


def router(params):
    action = params.get("action", "")
    if action.startswith(ACTION_PREFIX):
        action = action[len(ACTION_PREFIX):]
    if not action or action == "root":
        list_root()
    elif action == "prompt_search":
        prompt_search()
    elif action == "saved_searches":
        list_saved_searches()
    elif action == "delete_search":
        delete_saved_search(params.get("query", ""))
        xbmc.executebuiltin("Container.Refresh")
    elif action == "search":
        list_search(params.get("query", ""))
    elif action == "list":
        list_catalog(params.get("kind", "movies"), params.get("url", SITE_URL + "/filmek/1/1"))
    elif action == "categories":
        list_categories(params.get("kind", "movies"))
    elif action == "detail":
        list_detail(params["url"])
    elif action == "season":
        list_season(params["url"])
    elif action == "episode":
        list_episode(params["url"])
    elif action == "play":
        play(params.get("embed_url", ""))
    else:
        xbmcgui.Dialog().notification("JobbMintATV", "Ismeretlen művelet", xbmcgui.NOTIFICATION_ERROR, 3000)
