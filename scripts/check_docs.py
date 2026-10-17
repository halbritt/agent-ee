#!/usr/bin/env python3
"""Check local Markdown links/anchors and example syntax without running examples.

This checks README, docs, references, and the operator skill. It does not fetch
external URLs, execute model/EDA commands, render Mermaid or prove prose claims.
"""
import ast
import json
import re
import subprocess
import textwrap
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
FENCES = re.compile(r"^```([^\n]*)\n(.*?)^```[ \t]*$", re.M | re.S)
LINKS = re.compile(r"\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+\"[^\"]*\")?\)")


def anchors(text):
    found, counts = set(), {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)(?:\s+#+)?$", FENCES.sub("", text), re.M):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        found.add(slug if count == 0 else f"{slug}-{count}")
    found.update(re.findall(r'(?:id|name)=[\"\']([^\"\']+)[\"\']', text))
    return found


def main():
    documents = sorted({ROOT / "README.md", ROOT / "skills/agent-ee/SKILL.md",
                        *(ROOT / "docs").rglob("*.md"), *(ROOT / "references").rglob("*.md")})
    errors, counts = [], {"documents": len(documents), "local_links": 0,
                          "json_examples": 0, "python_examples": 0,
                          "shell_examples": 0, "mermaid_blocks": 0}
    for document in documents:
        text = document.read_text()
        label = document.relative_to(ROOT).as_posix()
        for block in FENCES.finditer(text):
            language, body = block.group(1).strip(), block.group(2)
            line = text[:block.start()].count("\n") + 1
            try:
                if language == "json":
                    json.loads(body)
                    counts["json_examples"] += 1
                elif language == "python":
                    ast.parse(textwrap.dedent(body))
                    counts["python_examples"] += 1
                elif language in ("sh", "bash"):
                    result = subprocess.run(["bash", "-n"], input=body, text=True,
                                            capture_output=True, check=False)
                    if result.returncode:
                        raise ValueError(result.stderr.strip())
                    counts["shell_examples"] += 1
                elif language == "mermaid":
                    counts["mermaid_blocks"] += 1
            except (ValueError, SyntaxError) as exc:
                errors.append(f"{label}:{line}: {language} example: {exc}")
        prose = FENCES.sub("", text)
        for match in LINKS.finditer(prose):
            link = match.group(1).strip("<>")
            parts = urlsplit(link)
            if parts.scheme or parts.netloc:
                continue
            counts["local_links"] += 1
            target = (document.parent / unquote(parts.path)).resolve() if parts.path else document
            if not target.exists():
                errors.append(f"{label}: missing local target {link}")
            elif parts.fragment and target.suffix == ".md" and unquote(parts.fragment) not in anchors(target.read_text()):
                errors.append(f"{label}: missing heading anchor {link}")
    print(json.dumps({**counts, "errors": errors}, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
