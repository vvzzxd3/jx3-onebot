"""Read-only acceptance check for the 2026-10-19 migration snapshot.

Run from the repository root with Python 3 (standard library only).
Not a permanent CI contract: subsequent approved edits/archival change this snapshot.
Reads documentation only; never opens runtime config, keys, databases or Git index.
"""
from pathlib import Path
import hashlib
import json
import re
import sys
from urllib.parse import unquote

ROOT = Path.cwd().resolve()
HERE = Path(__file__).resolve().parent
assert HERE == ROOT / '.agents/notes/active/REQ-20261019-notes-lifecycle'
NOTES = ROOT / '.agents/notes'
errors = []

def check(condition, message):
    if not condition:
        errors.append(message)

def digest(data):
    return hashlib.sha256(data).hexdigest()

angle_link = re.compile(r'\]\(<([^>]+)>\)')
inline_link = re.compile(r'\]\((?:<([^>]+)>|([^\s)]+)(?:\s+"[^"\n]*")?)\)')
manifest = json.loads((HERE / 'migration-map.json').read_text())
inventory = {x['path']: x['sha256'] for x in json.loads((HERE / 'source-inventory.json').read_text())}
check(len(manifest) == 33, 'migration count differs from 33')
check(len({x['source'] for x in manifest}) == 33, 'duplicate source')
check(len({x['target'] for x in manifest}) == 33, 'duplicate target')
for row in manifest:
    source, target = ROOT / row['source'], ROOT / row['target']
    check(not source.exists(), 'old process path remains: ' + row['source'])
    check(row['source_sha256'] == inventory[row['source']], 'source hash mismatch: ' + row['source'])
    if not target.is_file():
        errors.append('missing migrated file: ' + row['target'])
        continue
    data = target.read_bytes()
    check(digest(data) == row['target_sha256'], 'destination hash mismatch: ' + row['target'])
    text = data.decode('utf-8')
    banner = row['banner']
    check(text.startswith(banner), 'missing historical banner: ' + row['target'])
    body = text[len(banner):]
    normalized = angle_link.sub('](<LINK>)', body)
    check(digest(normalized.encode()) == row['body_without_link_destinations_sha256'], 'body integrity mismatch: ' + row['target'])

retained = set(inventory) - {row['source'] for row in manifest}
check(len(retained) == 7, 'formal/reference inventory differs from 7')
for path in retained:
    check((ROOT / path).is_file(), 'missing retained file: ' + path)
for path in ('docs/qq-integration/api-call-guide.html', 'docs/weather/reference/heweather-city-api.md', 'docs/weather/reference/heweather-daily-api.md'):
    check(digest((ROOT / path).read_bytes()) == inventory[path], 'reference bytes changed: ' + path)

files = [ROOT / 'README.md', ROOT / 'AGENTS.md'] + sorted((ROOT / 'docs').rglob('*.md')) + sorted(NOTES.rglob('*.md'))
local_links = 0
for file in files:
    text = file.read_text()
    for match in inline_link.finditer(text):
        value = match[1] or match[2]
        if re.match(r'[A-Za-z][\w+.-]*:', value) or value.startswith('//'):
            continue
        local_links += 1
        path, _, anchor = value.partition('#')
        target = (file.parent / unquote(path)).resolve() if path else file
        check(target.exists(), f'broken link: {file.relative_to(ROOT)} -> {value}')
        # Check heading anchors only in Markdown. Historical source-code line numbers
        # are preserved provenance, not claims about the current code location.
        if anchor and target.suffix == '.md' and target.is_file() and not re.fullmatch(r'L\d+(?:-L?\d+)?', anchor):
            headings = re.findall(r'^#{1,6}\s+(.+?)\s*#*$', target.read_text(), re.M)
            slugs = {re.sub(r'[^\w\- ]', '', h.lower()).replace(' ', '-') for h in headings}
            check(unquote(anchor) in slugs, f'unknown heading: {file.relative_to(ROOT)} -> {value}')

indexes = sorted((NOTES / 'active').glob('*/index.md'))
check(len(indexes) == 17, 'requirement package count differs from 17')
ids = {p.parent.name for p in indexes}
index_text = (NOTES / 'INDEX.md').read_text()
statuses = {}
for file in indexes:
    text = file.read_text()
    front = text.split('---', 2)[1]
    fields = dict(re.findall(r'^(\w+):\s*(.*?)$', front, re.M))
    for field in ('id', 'title', 'status', 'approval', 'outcome', 'modules', 'tags', 'created', 'updated', 'archived', 'relations'):
        check(field in fields, f'missing {field}: {file.parent.name}')
    ident = fields['id'].strip('"')
    status = fields['status'].strip('"')
    approval = fields['approval'].strip('"')
    outcome = fields['outcome'].strip('"')
    statuses[status] = statuses.get(status, 0) + 1
    check(ident == file.parent.name, 'id/path mismatch: ' + ident)
    check(status in {'proposed', 'in-progress', 'blocked', 'needs-review', 'ready-to-archive'}, 'invalid active status: ' + ident)
    check(approval in {'pending', 'approved', 'rejected', 'unknown'}, 'invalid approval: ' + ident)
    check(outcome in {'null', 'completed', 'rejected', 'cancelled', 'superseded'}, 'invalid outcome: ' + ident)
    check(fields['archived'] == 'null', 'uncommitted package marked archived: ' + ident)
    check(f'active/{ident}/index.md' in index_text, 'missing from active index: ' + ident)
    if status == 'ready-to-archive':
        check(outcome != 'null', 'missing closure outcome: ' + ident)
    for relation in json.loads(fields['relations']):
        check(relation['type'] in {'depends-on', 'follows-up', 'supersedes', 'related-to', 'historical-context'}, 'invalid relation type: ' + ident)
        check(relation['target'] in ids, 'unknown relation target: ' + ident)
        check(f'../{relation["target"]}/index.md' in text, 'missing navigable relation: ' + ident)

formal = [ROOT / 'README.md', ROOT / 'AGENTS.md'] + sorted((ROOT / 'docs').rglob('*.md'))
for file in formal:
    for m in inline_link.finditer(file.read_text()):
        value = m[1] or m[2]
        check(not re.search(r'notes/(?:active|archive/\d{4})/', value), 'formal document depends on process package: ' + str(file.relative_to(ROOT)))

print(json.dumps(dict(migrated_files=len(manifest), retained_files=len(retained), requirement_packages=len(indexes), statuses=statuses, markdown_files=len(files), local_links=local_links, errors=errors), ensure_ascii=False, indent=2))
sys.exit(1 if errors else 0)
