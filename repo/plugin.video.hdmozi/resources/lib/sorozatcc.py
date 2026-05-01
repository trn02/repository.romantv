from __future__ import unicode_literals

import html
import json
import os
import re
import sys
import traceback
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs


ADDON = xbmcaddon.Addon()
ADDON_HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]
PROFILE_DIR = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
SEARCHES_FILE = os.path.join(PROFILE_DIR, "saved_searches_sorozatcc.json")
SITE_URL = "https://sorozat.cc"
ACTION_PREFIX = "sc_"
EMBED_RESOLVER = None
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
SORT_OPTIONS = {
    "latest_upload": {"label": "Legfrissebb feltöltés", "method": "newestUpload", "params": {"orderBy": "created_at", "sortBy": "DESC"}},
    "latest_title": {"label": "Legújabb film/sorozat", "method": "related", "params": {}},
    "most_viewed": {"label": "Legnézettebb", "method": "viewership", "params": {}},
    "new_links": {"label": "Legújabb link", "method": "newLinks", "params": {"orderBy": "created_at", "sortBy": "DESC", "orderByLinks": "latest"}},
    "new_playback": {"label": "Új online lejátszás", "method": "newOnlinePlayback", "params": {"orderBy": "created_at", "sortBy": "DESC", "orderByLinks": "play"}},
    "az": {"label": "A - Z", "method": "orderByNameAsc", "params": {"orderBy": "title", "sortBy": "ASC"}},
}


def ensure_profile_dir():
    if not xbmcvfs.exists(PROFILE_DIR):
        xbmcvfs.mkdirs(PROFILE_DIR)


def build_url(**query):
    if query.get("action") and not query["action"].startswith(ACTION_PREFIX):
        query["action"] = ACTION_PREFIX + query["action"]
    return "{}?{}".format(BASE_URL, urllib.parse.urlencode(query))


def configure(base_url=None, addon_handle=None, profile_dir=None, action_prefix=None, embed_resolver=None):
    global BASE_URL, ADDON_HANDLE, PROFILE_DIR, SEARCHES_FILE, ACTION_PREFIX, EMBED_RESOLVER
    if base_url is not None:
        BASE_URL = base_url
    if addon_handle is not None:
        ADDON_HANDLE = addon_handle
    if profile_dir is not None:
        PROFILE_DIR = profile_dir
        SEARCHES_FILE = os.path.join(PROFILE_DIR, "saved_searches_sorozatcc.json")
    if action_prefix is not None:
        ACTION_PREFIX = action_prefix
    if embed_resolver is not None:
        EMBED_RESOLVER = embed_resolver


def log(message):
    xbmc.log("[plugin.video.sorozatcc] {}".format(message), xbmc.LOGINFO)


def new_opener():
    jar = CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar


def request(url, opener=None, data=None, headers=None, return_headers=False):
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
    active_opener = opener or urllib.request.build_opener()
    with active_opener.open(req, timeout=20) as response:
        body = response.read()
        if return_headers:
            return body, response.headers
        return body


def request_text(url, opener=None, data=None, headers=None, encoding="utf-8", return_headers=False):
    response = request(url, opener=opener, data=data, headers=headers, return_headers=return_headers)
    if return_headers:
        body, response_headers = response
        return body.decode(encoding, "replace"), response_headers
    return response.decode(encoding, "replace")


def strip_tags(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def normalize_url(url):
    if not url:
        return url
    url = html.unescape(url).strip().strip('"\'')
    if url.startswith("//"):
        return "https:" + url
    return url


def slugify(text):
    text = strip_tags(text).lower()
    replacements = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ö": "o", "ő": "o",
        "ú": "u", "ü": "u", "ű": "u", "ß": "ss",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "kategoria"


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
    save_saved_searches([item for item in searches if item.lower() != query.lower()])


def add_directory_item(label, target_query, is_folder=True, art=None, info=None, context=None):
    item = xbmcgui.ListItem(label=label)
    if art:
        item.setArt(art)
    if info:
        item.setInfo("video", info)
    if context:
        item.addContextMenuItems(context)
    xbmcplugin.addDirectoryItem(ADDON_HANDLE, build_url(**target_query), item, is_folder)


def add_playable_item(label, target_query, art=None, info=None):
    item = xbmcgui.ListItem(label=label)
    item.setProperty("IsPlayable", "true")
    if art:
        item.setArt(art)
    if info:
        item.setInfo("video", info)
    xbmcplugin.addDirectoryItem(ADDON_HANDLE, build_url(**target_query), item, False)


def parse_cards(page_html, kind):
    items = []
    pattern = re.compile(r'<a href="(https://sorozat\.cc/(?:film|sorozat)/[^"]+)">\s*<div class="card .*?">(.*?)</div>\s*</div>\s*</a>', re.DOTALL)
    for url, block in pattern.findall(page_html):
        title_match = re.search(r'<h5 class="font-size-14 mt-1 data-title">(.*?)</h5>', block, re.DOTALL)
        img_match = re.search(r'src="([^"]+)"\s*class="img-fluid rounded"', block)
        year_match = re.search(r'badge bg-secondary">([^<]+)<', block)
        rate_match = re.search(r'badge bg-success">([^<]+)<', block)
        if not title_match:
            continue
        items.append({
            "label": strip_tags(title_match.group(1)),
            "url": url,
            "thumb": normalize_url(img_match.group(1)) if img_match else "",
            "year": strip_tags(year_match.group(1)) if year_match else "",
            "rating": strip_tags(rate_match.group(1)) if rate_match else "",
            "kind": kind,
        })
    return items


def parse_categories(page_html, base_path):
    results = []
    seen = set()
    pattern = re.compile(r'<input[^>]+wire:model\.live="selectedCategorys"[^>]+value="(\d+)"[^>]*>\s*<label[^>]*>\s*([^<(]+)', re.DOTALL)
    for category_id, name in pattern.findall(page_html):
        if category_id in seen:
            continue
        seen.add(category_id)
        clean_name = strip_tags(name)
        url = "{}/{}/kategoria/{}/{}".format(SITE_URL, base_path, category_id, slugify(clean_name))
        results.append({
            "id": category_id,
            "slug": slugify(clean_name),
            "url": url,
            "name": clean_name,
        })
    return results


def parse_seasons(page_html):
    seasons = []
    pattern = re.compile(r'<a href="(https://sorozat\.cc/sorozat/[^"]+/evad/(\d+))">.*?<h5[^>]*>(.*?)</h5>.*?(\d+) epizód', re.DOTALL)
    for url, season_no, title, episode_count in pattern.findall(page_html):
        seasons.append({
            "url": url,
            "season": season_no,
            "label": strip_tags(title),
            "episodes": episode_count,
        })
    return seasons


def parse_episode_rows(page_html):
    episodes = []
    row_pattern = re.compile(r'<tr>.*?<td class="align-middle">(\d+\. epizód)</td>.*?<td class="align-middle">(\d+\. rész)</td>.*?wire:click="getLinks\((\d+),\s*(\d+),\s*(\d+)\)"', re.DOTALL)
    for ep_label, part_label, idx, episode_id, series_id in row_pattern.findall(page_html):
        episodes.append({
            "label": "{} - {}".format(ep_label, part_label),
            "episode_index": idx,
            "episode_id": episode_id,
            "series_id": series_id,
        })
    return episodes


def parse_movie_detail(page_html, page_url):
    title_match = re.search(r'<h5[^>]*>(.*?)</h5>\s*<h5[^>]*>(.*?)\((\d{4})\)</h5>', page_html, re.DOTALL)
    plot_match = re.search(r'<div class="col-lg-8">.*?<p[^>]*>(.*?)</p>', page_html, re.DOTALL)
    poster_match = re.search(r'<img src="([^"]+)" alt="[^"]+ film online"', page_html)
    iframe_match = re.search(r'<iframe id="myVideo" src="([^"]+)"', page_html)
    button_pattern = re.compile(r'wire:click="selectEmbed\((\d+)\)"[^>]*>\s*([^<\n]+)')
    hosts = []
    for embed_id, host_name in button_pattern.findall(page_html):
        hosts.append({"embed_id": embed_id, "host": strip_tags(host_name)})
    return {
        "title": strip_tags(title_match.group(2)) if title_match else "",
        "plot": strip_tags(plot_match.group(1)) if plot_match else "",
        "poster": normalize_url(poster_match.group(1)) if poster_match else "",
        "default_embed": normalize_url(iframe_match.group(1)) if iframe_match else "",
        "hosts": hosts,
        "page_url": page_url,
    }


def extract_livewire_component(page_html, component_name):
    pattern = re.compile(r'wire:snapshot="([^"]+)"[^>]+wire:id="([^"]+)"')
    for snapshot, wire_id in pattern.findall(page_html):
        decoded = html.unescape(snapshot)
        if '"name":"{}"'.format(component_name) in decoded:
            return decoded, wire_id
    return None, None


def extract_csrf(page_html):
    match = re.search(r'data-csrf="([^"]+)"', page_html)
    return match.group(1) if match else ""


def livewire_call(page_url, component_name, method, params):
    opener, _ = new_opener()
    page_html = request_text(page_url, opener=opener)
    csrf = extract_csrf(page_html)
    snapshot, _ = extract_livewire_component(page_html, component_name)
    if not csrf or not snapshot:
        raise ValueError("A dinamikus linkadatok nem találhatók")
    body = json.dumps({
        "_token": csrf,
        "components": [{
            "snapshot": snapshot,
            "updates": {},
            "calls": [{"path": "", "method": method, "params": params}],
        }],
    }).encode("utf-8")
    req = urllib.request.Request(
        SITE_URL + "/livewire/update",
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": page_url,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-CSRF-TOKEN": csrf,
            "X-Requested-With": "XMLHttpRequest",
            "X-Livewire": "true",
        },
    )
    with opener.open(req, timeout=20) as response:
        return response.read().decode("utf-8", "replace")


def parse_livewire_response(response_text):
    payload = json.loads(response_text)
    component = payload.get("components", [{}])[0]
    snapshot = component.get("snapshot", "{}")
    effects = component.get("effects", {})
    snapshot_data = json.loads(snapshot) if snapshot else {}
    return snapshot_data, effects


def extract_first_iframe(html_text):
    match = re.search(r'<iframe[^>]+src="([^"]+)"', html_text)
    return normalize_url(match.group(1)) if match else ""


def resolve_embed_url(embed_url):
    embed_url = normalize_url(embed_url)
    if not embed_url:
        raise ValueError("Ures embed URL")
    return embed_url


def prompt_search(search_type):
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
    xbmc.executebuiltin("Container.Update({})".format(build_url(action="search_results", search_type=search_type, query=query)))
    xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)


def list_root():
    add_directory_item("Keresés filmek között", {"action": "prompt_search", "search_type": "movie"}, is_folder=False)
    add_directory_item("Keresés sorozatok között", {"action": "prompt_search", "search_type": "series"}, is_folder=False)
    add_directory_item("Mentett keresések", {"action": "saved_searches"})
    add_directory_item("Online sorozatok", {"action": "series_home"})
    add_directory_item("Online filmek", {"action": "movies_home"})
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_saved_searches():
    for query in load_saved_searches():
        add_directory_item(
            query,
            {"action": "search_pick_type", "query": query},
            context=[("Törlés a mentett keresésekből", "RunPlugin({})".format(build_url(action="delete_saved_search", query=query)))],
        )
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_search_pick_type(query):
    add_directory_item("Keresés filmek között", {"action": "search_results", "search_type": "movie", "query": query})
    add_directory_item("Keresés sorozatok között", {"action": "search_results", "search_type": "series", "query": query})
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_section_home(kind):
    base = "sorozatok" if kind == "series" else "filmek"
    page_html = request_text(SITE_URL + "/" + base)
    label = "Online sorozatok" if kind == "series" else "Online filmek"
    add_directory_item("Kategóriák", {"action": "categories", "kind": kind})
    add_directory_item("Rendezés", {"action": "sorts", "kind": kind})
    run_list_page(page_html, kind, label, base_url=SITE_URL + "/" + base, base_query={})


def list_categories(kind):
    base = "sorozatok" if kind == "series" else "filmek"
    page_html = request_text(SITE_URL + "/" + base)
    category_base = "sorozatok" if kind == "series" else "filmek"
    for category in parse_categories(page_html, category_base):
        add_directory_item(category["name"], {"action": "category", "kind": kind, "url": category["url"], "name": category["name"]})
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_sorts(kind, category_url=None, category_name=None):
    for sort_key, sort_info in SORT_OPTIONS.items():
        add_directory_item(sort_info["label"], {"action": "sorted_list", "kind": kind, "sort": sort_key, "url": category_url or "", "name": category_name or ""})
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def build_page_url(base_url, page, query):
    params = dict(query)
    if page > 1:
        params["oldal"] = page
    if params:
        return base_url + "?" + urllib.parse.urlencode(params)
    return base_url


def run_list_page(page_html, kind, category_label, base_url, base_query):
    items = parse_cards(page_html, kind)
    for item in items:
        info = {"title": item["label"], "year": item["year"], "rating": item["rating"]}
        art = {"thumb": item["thumb"], "poster": item["thumb"]}
        if kind == "movie":
            add_directory_item(item["label"], {"action": "movie_detail", "url": item["url"]}, art=art, info=info)
        else:
            add_directory_item(item["label"], {"action": "series_detail", "url": item["url"]}, art=art, info=info)

    page_match = re.search(r'Oldalak:\s*(\d+)\s*/\s*(\d+)', page_html)
    if page_match:
        current_page = int(page_match.group(1))
        total_pages = int(page_match.group(2))
        if current_page < total_pages:
            add_directory_item("Következő oldal ({}/{})".format(current_page + 1, total_pages), {
                "action": "paged_list",
                "kind": kind,
                "base_url": base_url,
                "query": json.dumps(base_query, ensure_ascii=False),
                "page": current_page + 1,
                "name": category_label,
            })

    xbmcplugin.setPluginCategory(ADDON_HANDLE, category_label)
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_category(kind, category_url, category_name):
    page_html = request_text(category_url)
    add_directory_item("Rendezés", {"action": "sorts", "kind": kind, "url": category_url, "name": category_name})
    run_list_page(page_html, kind, category_name, category_url, {})


def list_sorted(kind, sort_key, category_url, category_name):
    sort_def = SORT_OPTIONS.get(sort_key, SORT_OPTIONS["latest_upload"])
    sort_query = dict(sort_def.get("params", {}))
    base_url = category_url or (SITE_URL + ("/sorozatok" if kind == "series" else "/filmek"))
    label = category_name or sort_def.get("label", "Lista")
    if sort_key in ["latest_upload", "az", "new_links", "new_playback"]:
        page_html = request_text(build_page_url(base_url, 1, sort_query))
        run_list_page(page_html, kind, label, base_url, sort_query)
        return
    component_name = "series.list-series" if kind == "series" else "movies.movies"
    response = livewire_call(base_url, component_name, sort_def["method"], [])
    snapshot_data, effects = parse_livewire_response(response)
    page_html = effects.get("html", "")
    query = {
        "orderBy": snapshot_data.get("data", {}).get("orderBy", ""),
        "sortBy": snapshot_data.get("data", {}).get("orderByType", ""),
        "orderByLinks": snapshot_data.get("data", {}).get("orderByLinks", ""),
    }
    query = {key: value for key, value in query.items() if value}
    run_list_page(page_html, kind, label, base_url, query)


def list_paged(kind, base_url, query_json, page, name):
    query = json.loads(query_json) if query_json else {}
    page_html = request_text(build_page_url(base_url, int(page), query))
    run_list_page(page_html, kind, name, base_url, query)


def run_search_results(search_type, query):
    remember_search(query)
    base_url = SITE_URL + ("/filmek" if search_type == "movie" else "/sorozatok")
    search_query = {"kereses": query}
    page_html = request_text(build_page_url(base_url, 1, search_query))
    run_list_page(page_html, search_type, "Keresés: {}".format(query), base_url, search_query)


def list_movie_detail(url):
    page_html = request_text(url)
    detail = parse_movie_detail(page_html, url)
    art = {"thumb": detail["poster"], "poster": detail["poster"]}
    info = {"title": detail["title"], "plot": detail["plot"]}
    if detail["default_embed"]:
        add_playable_item("Alapértelmezett lejátszás", {"action": "play_embed", "embed_url": detail["default_embed"]}, art=art, info=info)
    for host in detail["hosts"]:
        add_directory_item(host["host"], {"action": "movie_host", "page_url": url, "embed_id": host["embed_id"], "title": detail["title"], "poster": detail["poster"], "plot": detail["plot"]}, art=art, info=info)
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_movie_host(page_url, embed_id, title, poster, plot):
    art = {"thumb": poster, "poster": poster}
    info = {"title": title, "plot": plot}
    try:
        response = livewire_call(page_url, "movies.movie-embed", "selectEmbed", [int(embed_id)])
        _, effects = parse_livewire_response(response)
        embed_url = extract_first_iframe(effects.get("html", ""))
        if embed_url:
            add_playable_item("Lejátszás", {"action": "play_embed", "embed_url": embed_url}, art=art, info=info)
        else:
            xbmcgui.Dialog().notification("Sorozat.cc", "A kiválasztott link nem olvasható ki", xbmcgui.NOTIFICATION_ERROR, 4000)
    except Exception as exc:
        log("movie host failed: {}\n{}".format(exc, traceback.format_exc()))
        xbmcgui.Dialog().notification("Sorozat.cc", str(exc), xbmcgui.NOTIFICATION_ERROR, 4000)
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_series_detail(url):
    page_html = request_text(url)
    title_match = re.search(r'<h5[^>]*>(.*?)</h5>\s*<h5[^>]*>(.*?)\((\d{4})\)</h5>', page_html, re.DOTALL)
    poster_match = re.search(r'<img src="([^"]+)" alt="[^"]+ online"', page_html)
    plot_match = re.search(r'<div class="col-lg-8">.*?<p[^>]*>(.*?)</p>', page_html, re.DOTALL)
    art = {"thumb": normalize_url(poster_match.group(1)) if poster_match else "", "poster": normalize_url(poster_match.group(1)) if poster_match else ""}
    info = {"title": strip_tags(title_match.group(2)) if title_match else "", "plot": strip_tags(plot_match.group(1)) if plot_match else ""}
    for season in parse_seasons(page_html):
        add_directory_item("{} ({} epizód)".format(season["label"], season["episodes"]), {"action": "season_detail", "url": season["url"]}, art=art, info=info)
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_season_detail(url):
    page_html = request_text(url)
    for episode in parse_episode_rows(page_html):
        add_directory_item(episode["label"], {"action": "episode_links", "page_url": url, "episode_index": episode["episode_index"], "episode_id": episode["episode_id"], "series_id": episode["series_id"]})
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_episode_links(page_url, episode_index, episode_id, series_id):
    try:
        response = livewire_call(page_url, "series.episode-links", "getLinks", [int(episode_index), int(episode_id), int(series_id)])
        snapshot_data, effects = parse_livewire_response(response)
        links = re.findall(r'"link":"(https:\\/\\/[^\"]+)","quality":"([^\"]*)","language":"([^\"]*)","storage":"([^\"]*)"', response)
        matches = re.findall(r'<iframe[^>]+src="([^"]+)"', effects.get("html", ""))
        if matches:
            for idx, embed in enumerate(matches, 1):
                add_playable_item("Lejátszás {}".format(idx), {"action": "play_embed", "embed_url": normalize_url(embed)})
        elif links:
            for idx, (embed, quality, language, storage) in enumerate(links, 1):
                label = "{} | {} | {} | {}".format(idx, quality or "?", language or "?", storage or "?")
                add_playable_item(label, {"action": "play_embed", "embed_url": normalize_url(embed.replace('\\/', '/'))})
        else:
            html_blob = effects.get("html", "")
            link_pattern = re.compile(r'(https?://[^\s"\']+)')
            urls = []
            for embed in link_pattern.findall(html_blob):
                if any(host in embed for host in ["videa.hu", "ok.ru", "vk.com", "vkvideo.ru", "drive.google.com", "sendvid.com", "rumble.com", "dailymotion.com", "filemoon", "hqq", "dood", "streamz", "sbot", "sbbrisk"]):
                    urls.append(normalize_url(embed))
            for idx, embed in enumerate(dict.fromkeys(urls), 1):
                add_playable_item("Lejátszás {}".format(idx), {"action": "play_embed", "embed_url": embed})
    except Exception as exc:
        log("episode links failed: {}\n{}".format(exc, traceback.format_exc()))
        xbmcgui.Dialog().notification("Sorozat.cc", str(exc), xbmcgui.NOTIFICATION_ERROR, 4000)
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def play_embed(embed_url):
    try:
        resolved_url = EMBED_RESOLVER(embed_url) if EMBED_RESOLVER else resolve_embed_url(embed_url)
        if isinstance(resolved_url, dict):
            resolved_url = resolved_url.get("url", "")
        item = xbmcgui.ListItem(path=resolved_url)
        xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, item)
    except Exception as exc:
        log("play embed failed: {}\n{}".format(exc, traceback.format_exc()))
        xbmcgui.Dialog().notification("Sorozat.cc", str(exc), xbmcgui.NOTIFICATION_ERROR, 4000)
        xbmcplugin.setResolvedUrl(ADDON_HANDLE, False, xbmcgui.ListItem())


def router(params):
    action = params.get("action", "")
    if action.startswith(ACTION_PREFIX):
        action = action[len(ACTION_PREFIX):]
    if not action:
        list_root()
        return
    if action == "prompt_search":
        prompt_search(params.get("search_type", "movie"))
        return
    if action == "saved_searches":
        list_saved_searches()
        return
    if action == "search_pick_type":
        list_search_pick_type(params.get("query", ""))
        return
    if action == "delete_saved_search":
        delete_saved_search(params.get("query", ""))
        xbmc.executebuiltin("Container.Refresh")
        return
    if action == "search_results":
        run_search_results(params.get("search_type", "movie"), params.get("query", ""))
        return
    if action == "series_home":
        list_section_home("series")
        return
    if action == "movies_home":
        list_section_home("movie")
        return
    if action == "categories":
        list_categories(params.get("kind", "movie"))
        return
    if action == "sorts":
        list_sorts(params.get("kind", "movie"), params.get("url", ""), params.get("name", ""))
        return
    if action == "category":
        list_category(params.get("kind", "movie"), params.get("url", SITE_URL), params.get("name", "Kategória"))
        return
    if action == "sorted_list":
        list_sorted(params.get("kind", "movie"), params.get("sort", "latest_upload"), params.get("url", ""), params.get("name", ""))
        return
    if action == "paged_list":
        list_paged(params.get("kind", "movie"), params.get("base_url", SITE_URL), params.get("query", "{}"), params.get("page", "1"), params.get("name", "Lista"))
        return
    if action == "movie_detail":
        list_movie_detail(params["url"])
        return
    if action == "movie_host":
        list_movie_host(params["page_url"], params["embed_id"], params.get("title", ""), params.get("poster", ""), params.get("plot", ""))
        return
    if action == "series_detail":
        list_series_detail(params["url"])
        return
    if action == "season_detail":
        list_season_detail(params["url"])
        return
    if action == "episode_links":
        list_episode_links(params["page_url"], params["episode_index"], params["episode_id"], params["series_id"])
        return
    if action == "play_embed":
        play_embed(params.get("embed_url", ""))
        return
    xbmcgui.Dialog().notification("Sorozat.cc", "Ismeretlen művelet", xbmcgui.NOTIFICATION_ERROR, 3000)


if __name__ == "__main__":
    router(dict(urllib.parse.parse_qsl(sys.argv[2][1:])))
