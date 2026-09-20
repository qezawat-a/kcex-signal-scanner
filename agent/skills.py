"""Markdown skill loader (CRAG skills.js port). A skill is a .md file with
front-matter (name/description) or a folder with SKILL.md. README.md is ignored."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = BASE_DIR / "skills"


def _parse(raw):
    if not raw.startswith("---"):
        return {}, raw.strip()
    end = raw.find("\n---", 3)
    if end < 0:
        return {}, raw.strip()
    meta = {}
    for line in raw[3:end].strip().split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, raw[end + 4:].strip()


def list_skills(skill_dir=None, exclude=()):
    base = Path(skill_dir) if skill_dir else SKILLS_DIR
    out = []
    try:
        entries = list(base.iterdir())
    except Exception:
        return []
    for e in entries:
        fid = e.name[:-3] if e.name.lower().endswith(".md") else e.name
        if e.is_dir():
            f = e / "SKILL.md"
            if not f.exists():
                continue
        elif e.is_file() and e.suffix.lower() == ".md":
            if e.name.lower() == "readme.md":
                continue
            f = e
        else:
            continue
        try:
            raw = f.read_text(encoding="utf-8")
        except Exception:
            continue
        meta, body = _parse(raw)
        name = meta.get("name", fid)
        if name in exclude or fid in exclude:
            continue
        out.append({"id": fid, "name": name, "description": meta.get("description", ""),
                    "body": body, "file": str(f)})
    return out
