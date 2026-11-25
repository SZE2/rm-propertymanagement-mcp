"""Serve this server's guidance as Agent Skills over MCP (extension io.modelcontextprotocol/skills, SEP-2640).

Read-only, standard library only. A skill is a folder holding SKILL.md (flat YAML frontmatter with name and
description) plus optional text files. Public, generic skills live in skills/. Skills that belong to one
installation live in local/skills/, which is never published. Clients that know the extension call
skills/list and skills/get; every client can read the files with resources/read (skill://<name>/<file>).
The skill URIs are named in the server instructions. resources/list does not enumerate them: the extension
lets a server serve skills without listing them, and older clients keep the empty list they had before.
Set RM_MCP_SKILLS=0 to turn the whole feature off.
"""
import hashlib
import os
import re
import sys

EXTENSION = "io.modelcontextprotocol/skills"
ENABLED = os.environ.get("RM_MCP_SKILLS", "1") != "0"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOTS = (os.path.join(HERE, "skills"), os.path.join(HERE, "local", "skills"))
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TEXT_TYPES = {".md": "text/markdown", ".txt": "text/plain", ".json": "application/json", ".csv": "text/csv"}
MAX_FILES, MAX_BYTES = 512, 16 * 1024 * 1024
TTL_MS = 60000   # cache hint for skills/list and skills/get; "private" because local/skills may be installation-specific


class SkillError(Exception):
    """Unknown or invalid skill or resource. The server answers JSON-RPC -32602 with this message."""


def parse_frontmatter(text):
    """Flat 'key: value' frontmatter between '---' lines. Returns a dict of strings or raises SkillError."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise SkillError("SKILL.md must start with a '---' line")
    fm = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1] in (" ", "\t") or ":" not in line:
            raise SkillError("only flat 'key: value' frontmatter is supported: %r" % line[:60])
        key, value = line.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fm[key.strip()] = value
    else:
        raise SkillError("frontmatter is not closed with '---'")
    name = fm.get("name", "")
    if not NAME_RE.match(name) or len(name) > 64:
        raise SkillError("name must be 1-64 lowercase letters, digits and single hyphens")
    if not 1 <= len(fm.get("description", "")) <= 1024:
        raise SkillError("description must be 1-1024 characters")
    return fm


def _skill_files(folder):
    """Relative paths of the servable text files in a skill folder (no hidden files, no symlinks)."""
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__"
                         and not os.path.islink(os.path.join(root, d)))
        for f in sorted(files):
            path = os.path.join(root, f)
            if f.startswith(".") or os.path.islink(path) or os.path.splitext(f)[1].lower() not in TEXT_TYPES:
                continue
            out.append(os.path.relpath(path, folder).replace(os.sep, "/"))
    return out


def _load(folder, name):
    with open(os.path.join(folder, "SKILL.md"), "rb") as f:
        fm = parse_frontmatter(f.read().decode("utf-8"))
    if fm["name"] != name:
        raise SkillError("folder name must equal frontmatter name %r" % fm["name"])
    rels = _skill_files(folder)
    if len(rels) > MAX_FILES:
        raise SkillError("more than %d files" % MAX_FILES)
    files, resources, total = {}, [], 0
    for rel in rels:
        path = os.path.join(folder, rel)
        with open(path, "rb") as f:
            data = f.read()
        data.decode("utf-8")  # text files only
        total += len(data)
        uri = "skill://%s/%s" % (name, rel)
        files[uri] = path
        resources.append({"uri": uri, "digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data)})
    if total > MAX_BYTES:
        raise SkillError("larger than 16 MiB")
    return {"uri": "skill://%s/SKILL.md" % name, "frontmatter": fm, "resources": resources}, files


def catalog():
    """{SKILL.md uri: (entry, {file uri: path})}. Rebuilt on every call so edits show at once."""
    found, names = {}, set()
    if not ENABLED:
        return found
    for base in ROOTS:
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            folder = os.path.join(base, name)
            if os.path.islink(folder) or not os.path.isfile(os.path.join(folder, "SKILL.md")):
                continue
            if name in names:
                sys.stderr.write("[rm-mcp] skipped skill %s: name already served\n" % name)
                continue
            try:
                entry, files = _load(folder, name)
            except (SkillError, OSError, UnicodeDecodeError) as e:
                sys.stderr.write("[rm-mcp] skipped skill %s: %s\n" % (name, e))
                continue
            names.add(name)
            found[entry["uri"]] = (entry, files)
    return found


def capabilities():
    return {"resources": {"listChanged": False}, "extensions": {EXTENSION: {}}} if ENABLED else {}


def skills_list():
    return {"resultType": "complete", "skills": [entry for entry, _ in catalog().values()],
            "ttlMs": TTL_MS, "cacheScope": "private"}


def skills_get(uri):
    item = catalog().get(uri or "")
    if not item:
        raise SkillError("No skill is served at %s" % uri)
    return {"resultType": "complete", "skill": item[0], "ttlMs": TTL_MS, "cacheScope": "private"}


def read_resource(uri):
    for _, files in catalog().values():
        if uri in files:
            with open(files[uri], "rb") as f:
                text = f.read().decode("utf-8")
            mime = TEXT_TYPES[os.path.splitext(files[uri])[1].lower()]
            return {"contents": [{"uri": uri, "mimeType": mime, "text": text}]}
    raise SkillError("No resource is served at %s" % uri)


def instructions_hint(limit=25):
    uris = sorted(catalog())
    if not uris:
        return ""
    return ("\nGUIDES: before a multi-step task, read the matching guide with read_resource(uri), then follow it: "
            + "; ".join(uris[:limit]))
