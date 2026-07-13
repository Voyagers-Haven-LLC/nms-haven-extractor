"""
Helper for UPDATE_HAVEN_EXTRACTOR.bat.

Used so the .bat doesn't have to wrestle with cmd.exe's nightmare of escaping
quotes and parens inside `python -c` and `for /f` blocks. Self-contained,
stdlib only.

Usage:
  python _haven_updater_helper.py version <haven_extractor.py path>
  python _haven_updater_helper.py tag     <release_json path>
  python _haven_updater_helper.py url     <release_json path>

Prints the requested value to stdout. Exits 1 on any error so the .bat can
detect failures via errorlevel.
"""

import json
import re
import sys


def get_version(path: str) -> str:
    text = open(path, encoding='utf-8').read()
    m = re.search(r'__version__\s*=\s*[\'"](\d+\.\d+\.\d+)[\'"]', text)
    return m.group(1) if m else ''


def get_tag(path: str) -> str:
    d = json.load(open(path, encoding='utf-8'))
    return d.get('tag_name', '').lstrip('v')


def get_url(path: str) -> str:
    d = json.load(open(path, encoding='utf-8'))
    for asset in d.get('assets', []):
        if asset.get('name', '').startswith('HavenExtractor-mod-'):
            return asset.get('browser_download_url', '')
    return ''


def main() -> int:
    if len(sys.argv) < 3:
        print('usage: _haven_updater_helper.py <version|tag|url> <path>', file=sys.stderr)
        return 1
    cmd, path = sys.argv[1], sys.argv[2]
    handlers = {'version': get_version, 'tag': get_tag, 'url': get_url}
    if cmd not in handlers:
        print(f'unknown subcommand: {cmd}', file=sys.stderr)
        return 1
    try:
        result = handlers[cmd](path)
    except Exception as e:
        print(f'error: {e}', file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == '__main__':
    sys.exit(main())
