"""Check README local links, image files and Mermaid source synchronization."""

import json
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote


def main():
    root = Path(__file__).resolve().parent.parent
    text = (root / "README.md").read_text(encoding="utf-8")
    errors = []
    links = re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text)
    anchors = {
        re.sub(r"[^\w\-\u4e00-\u9fff ]", "", heading.lower()).replace(" ", "-")
        for heading in re.findall(r"^#{1,6} (.+)$", text, re.M)
    }
    local_count = 0
    for link in links:
        if re.match(r"[a-z]+://", link):
            continue
        target, _, anchor = unquote(link).partition("#")
        if target:
            local_count += 1
            if not (root / target).exists():
                errors.append(f"Missing link target: {target}")
        elif anchor and anchor not in anchors:
            errors.append(f"Missing anchor: {anchor}")
    diagrams = re.findall(r"<!-- diagram: ([^ ]+) -->\s*```mermaid\n(.*?)```", text, re.S)
    for source, body in diagrams:
        path = root / source
        if not path.exists() or path.read_text(encoding="utf-8").strip() != body.strip():
            errors.append(f"Diagram source differs from README: {source}")
    if len(diagrams) != len(re.findall(r"^```mermaid", text, re.M)):
        errors.append("Every Mermaid block must declare its source")
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    image_sizes = {}
    for image in images:
        path = root / image
        if not path.exists():
            continue
        if path.suffix == ".png":
            data = path.read_bytes()
            if data[:8] != b"\x89PNG\r\n\x1a\n":
                errors.append(f"Invalid PNG: {image}")
            else:
                image_sizes[image] = list(struct.unpack(">II", data[16:24]))
        elif path.suffix == ".svg":
            ET.parse(path)
    result = {
        "lines": len(text.splitlines()),
        "sections": len(re.findall(r"^## ", text, re.M)),
        "images": len(images), "mermaid_diagrams": len(diagrams),
        "local_links_checked": local_count, "image_sizes": image_sizes,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
