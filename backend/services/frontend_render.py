"""Render management HTML with content-versioned local assets."""
import hashlib
import re

_PATTERN = re.compile(r'''((?:src|href)=["'])(/static/[^"'?#]+)(?:\?[^"']*)?(["'])''')


def render(path, root):
    root = root.resolve()
    def version(match):
        asset = (root / match[2].removeprefix('/static/')).resolve()
        if not asset.is_relative_to(root) or not asset.is_file():
            return match[0]
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()[:16]
        return match[1] + match[2] + '?v=' + digest + match[3]
    return _PATTERN.sub(version, path.read_text(encoding='utf-8'))
