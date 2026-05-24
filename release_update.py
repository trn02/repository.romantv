import argparse
import hashlib
import re
import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parent
REPO = ROOT / "repo"
PLUGIN_ID = "plugin.video.hdmozi"
PLUGIN_SRC = ROOT / PLUGIN_ID
PLUGIN_REPO_DIR = REPO / PLUGIN_ID
SOURCE_ADDON_XML = PLUGIN_SRC / "addon.xml"
REPO_ADDON_XML = PLUGIN_REPO_DIR / "addon.xml"
REPO_ADDONS_XML = REPO / "addons.xml"
REPO_ADDONS_MD5 = REPO / "addons.xml.md5"
REPO_INDEX = REPO / "index.html"
REPO_PLUGIN_INDEX = PLUGIN_REPO_DIR / "index.html"
SITE_INDEX = ROOT / "index.html"
REPOSITORY_ID = "repository.romantv"
REPOSITORY_ADDON_XML = REPO / REPOSITORY_ID / "addon.xml"
PLUGIN_ASSETS = ("logo.png",)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str):
    path.write_text(content, encoding="utf-8", newline="\n")


def parse_version(text: str) -> str:
    match = re.search(r'<addon\b[^>]*\bversion="(\d+)\.(\d+)\.(\d+)"', text, re.DOTALL)
    if not match:
        raise SystemExit("Nem talalhato verzio az addon.xml-ben.")
    return ".".join(match.groups())


def replace_addon_attr(text: str, attr: str, value: str) -> str:
    pattern = rf'(<addon\b[^>]*\b{attr}=")[^"]+(")'
    if not re.search(pattern, text, re.DOTALL):
        raise SystemExit(f"Nem frissitheto a(z) {attr} attributum az addon.xml-ben.")
    return re.sub(
        pattern,
        lambda match: f'{match.group(1)}{value}{match.group(2)}',
        text,
        count=1,
        flags=re.DOTALL,
    )


def bump_patch(version: str) -> str:
    major, minor, patch = [int(part) for part in version.split(".")]
    return f"{major}.{minor}.{patch + 1}"


def indent_xml(element: ET.Element):
    ET.indent(element, space="    ")


def sync_plugin_metadata():
    shutil.copy2(SOURCE_ADDON_XML, REPO_ADDON_XML)
    for asset_name in PLUGIN_ASSETS:
        source_asset = PLUGIN_SRC / asset_name
        if source_asset.exists():
            shutil.copy2(source_asset, PLUGIN_REPO_DIR / asset_name)


def build_plugin_zip(version: str):
    zip_path = PLUGIN_REPO_DIR / f"{PLUGIN_ID}-{version}.zip"
    for existing in PLUGIN_REPO_DIR.glob(f"{PLUGIN_ID}-*.zip"):
        existing.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PLUGIN_SRC.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            zf.write(path, path.relative_to(PLUGIN_SRC.parent).as_posix())


def regenerate_addons_xml():
    addons_root = ET.Element("addons")
    addons_root.append(ET.parse(REPOSITORY_ADDON_XML).getroot())
    addons_root.append(ET.parse(SOURCE_ADDON_XML).getroot())
    indent_xml(addons_root)
    xml_content = ET.tostring(addons_root, encoding="unicode")
    write_text(REPO_ADDONS_XML, '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + xml_content + "\n")


def refresh_md5():
    digest = hashlib.md5(read_text(REPO_ADDONS_XML).encode("utf-8")).hexdigest()
    write_text(REPO_ADDONS_MD5, digest + "\n")


def read_repository_details():
    repo_root = ET.parse(REPOSITORY_ADDON_XML).getroot()
    repo_version = repo_root.attrib["version"]
    repo_name = repo_root.attrib["name"]
    datadir = repo_root.find("./extension/dir/datadir")
    repo_url = datadir.text if datadir is not None else ""
    return repo_name, repo_version, repo_url


def write_repo_indexes(plugin_version: str):
    repo_name, repo_version, repo_url = read_repository_details()
    direct_repo_zip = f"{repo_url}{REPOSITORY_ID}/{REPOSITORY_ID}-{repo_version}.zip"

    write_text(
        REPO_INDEX,
        "\n".join(
            [
                "<!doctype html>",
                "<html>",
                f"<head><meta charset=\"utf-8\"><title>{repo_name}</title></head>",
                "<body>",
                f"<h1>{repo_name}</h1>",
                "<ul>",
                f"  <li><a href=\"{REPOSITORY_ID}-{repo_version}.zip\">{REPOSITORY_ID}-{repo_version}.zip</a></li>",
                f"  <li><a href=\"{direct_repo_zip}\">{REPOSITORY_ID}-{repo_version}.zip (direct)</a></li>",
                f"  <li><a href=\"{REPOSITORY_ID}/\">{REPOSITORY_ID}/</a></li>",
                f"  <li><a href=\"{PLUGIN_ID}/\">{PLUGIN_ID}/</a></li>",
                f"  <li><a href=\"{PLUGIN_ID}/{PLUGIN_ID}-{plugin_version}.zip\">{PLUGIN_ID}-{plugin_version}.zip</a></li>",
                "  <li><a href=\"addons.xml\">addons.xml</a></li>",
                "  <li><a href=\"addons.xml.md5\">addons.xml.md5</a></li>",
                "</ul>",
                f"<p>Kodi source URL: <code>{repo_url}</code></p>",
                "</body>",
                "</html>",
                "",
            ]
        ),
    )

    write_text(
        REPO_PLUGIN_INDEX,
        "\n".join(
            [
                "<!doctype html>",
                "<html>",
                f"<head><meta charset=\"utf-8\"><title>{PLUGIN_ID}</title></head>",
                "<body>",
                f"<h1>{PLUGIN_ID}</h1>",
                "<ul>",
                f"  <li><a href=\"{PLUGIN_ID}-{plugin_version}.zip\">{PLUGIN_ID}-{plugin_version}.zip</a></li>",
                "  <li><a href=\"addon.xml\">addon.xml</a></li>",
                "  <li><a href=\"logo.png\">logo.png</a></li>",
                "  <li><a href=\"../\">../</a></li>",
                "</ul>",
                "</body>",
                "</html>",
                "",
            ]
        ),
    )

    write_text(
        SITE_INDEX,
        "\n".join(
            [
                "<!doctype html>",
                "<html>",
                f"<head><meta charset=\"utf-8\"><title>{repo_name}</title></head>",
                "<body>",
                f"<h1>{repo_name}</h1>",
                "<p>Open the repo folder: <a href=\"repo/\">repo/</a></p>",
                f"<p>Kodi source URL: <code>{repo_url}</code></p>",
                "</body>",
                "</html>",
                "",
            ]
        ),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", help="Explicit target version, e.g. 1.1.2")
    args = parser.parse_args()

    source_xml = read_text(SOURCE_ADDON_XML)
    current_version = parse_version(source_xml)
    next_version = args.version or bump_patch(current_version)

    updated_source_xml = replace_addon_attr(source_xml, "version", next_version)
    write_text(SOURCE_ADDON_XML, updated_source_xml)

    sync_plugin_metadata()
    build_plugin_zip(next_version)
    regenerate_addons_xml()
    refresh_md5()
    write_repo_indexes(next_version)

    print(f"Plugin version updated: {current_version} -> {next_version}")
    print("Next steps: ellenorizd a valtozasokat, majd commitolj es pusholj.")


if __name__ == "__main__":
    main()
