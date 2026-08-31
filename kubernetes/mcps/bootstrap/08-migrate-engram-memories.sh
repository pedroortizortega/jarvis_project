#!/usr/bin/env bash
# Two independent operations for getting Engram memories visible through
# memory-router:
#
#   retag   Re-tags EXISTING local observations with the `ns:<namespace>`
#           topic_key prefix that memory-router's EngramBackend requires
#           (see hermes-native/memory-router/src/memory_router/backends/
#           engram.py: store() sets topic_key=f"ns:{namespace}", search()
#           filters by topic_key_prefix=f"ns:{namespace}" -- anything
#           without that exact prefix is invisible to memory_search).
#           Untagged observations are NOT migrated to a different store;
#           it's the SAME engram.db memory-router already reads from
#           (confirmed: EngramBackend spawns the same `engram mcp
#           --tools=agent`) -- this only makes them match the filter.
#
#   merge   Pulls an Engram export from another machine (e.g. a work
#           laptop) into this one's engram.db, via engram's own
#           export/import (NOT raw SQL against two independently-evolved
#           schemas -- too easy to violate a FTS trigger or dedupe
#           invariant neither of us has fully audited).
#
# Both operations write to a live, real database. Both take a backup of
# ~/.engram/engram.db (or $ENGRAM_DATA_DIR/engram.db) before doing anything
# destructive, and print exactly what they're about to do before doing it
# unless --yes is passed.
#
# KNOWN ENGRAM QUIRKS (found the hard way, 2026-08-31):
#   - `engram export`/`import` do not accept --help -- passing it is
#     treated as the <file> argument and actually runs the command.
#   - `engram import` does NOT dedupe the `user_prompts` table
#     (sessions/observations do, via normalized_hash) -- re-importing the
#     same export twice will double your prompts. This script always runs
#     the prompts dedupe pass after merge as a safety net.
#   - engram.db runs in WAL mode. A plain `cp` of just the .db file can
#     silently grab a stale, pre-checkpoint snapshot (missing recent
#     commits sitting in the -wal file) -- confirmed live: a `cp` right
#     after a dedupe still showed the pre-dedupe row count. Every copy
#     this script makes (local backup, or pulling a source db to merge
#     from) goes through `sqlite3 ".backup"`, SQLite's own online-backup
#     API, which is WAL-aware and always consistent. Never `cp` a live
#     engram.db directly.
set -euo pipefail

: "${ENGRAM_DATA_DIR:=$HOME/.engram}"
DB="$ENGRAM_DATA_DIR/engram.db"

log() { printf '[migrate-engram] %s\n' "$*" >&2; }
die() { echo "[migrate-engram] $*" >&2; exit 1; }

command -v engram >/dev/null 2>&1 || die "engram CLI not found on PATH"
command -v sqlite3 >/dev/null 2>&1 || die "sqlite3 not found on PATH"
[ -f "$DB" ] || die "No engram.db at $DB (set ENGRAM_DATA_DIR to override)"

# WAL-safe snapshot of a (possibly live) sqlite db -- never a raw `cp`.
sqlite_backup() {
  local src="$1" dest="$2"
  sqlite3 "$src" ".backup '$dest'"
}

backup_db() {
  local stamp tag="$1"
  stamp=$(date +%Y%m%dT%H%M%S)
  local backup="$ENGRAM_DATA_DIR/engram.db.bak-$tag-$stamp"
  sqlite_backup "$DB" "$backup"
  log "Backup: $backup"
}

usage() {
  cat <<EOF
Usage:
  $0 retag --namespace </projects/name|/agents/name|/global|/user/master> [--project NAME] [--yes]
  $0 merge --source-db <path> [--yes]
  $0 merge --source-host <ssh-target> [--source-path <remote-engram.db>] [--yes]

retag:
  Tags existing local observations so memory-router's memory_search can
  find them. Prepends "ns:<namespace>:" to each observation's topic_key
  (or sets it to "ns:<namespace>" if it had none) -- never overwrites the
  original topic segment, only prefixes it.
  --project   Which Engram project to retag (default: jarvis_project)
  --yes       Skip the confirmation prompt

merge:
  Imports another machine's Engram export into this engram.db, via
  engram's own export/import (never raw SQL across two DBs).
  --source-db     A path to the other machine's engram.db file (safe even
                  if it's a live, currently-running instance -- snapshotted
                  via sqlite3 .backup, WAL-aware, before touching it)
  --source-host   Fetch it from user@host instead (default remote path:
                  ~/.engram/engram.db, override with --source-path).
                  Snapshots it remotely via sqlite3 .backup first (needs
                  sqlite3 on the remote host too), then scp's only that
                  consistent snapshot down -- never the raw db file.
  --yes           Skip the confirmation prompt
EOF
}

[ $# -ge 1 ] || { usage; exit 1; }
MODE="$1"; shift

case "$MODE" in
  retag)
    NAMESPACE=""
    PROJECT="jarvis_project"
    YES=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --namespace) NAMESPACE="$2"; shift 2 ;;
        --project) PROJECT="$2"; shift 2 ;;
        --yes) YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown arg: $1" ;;
      esac
    done
    [ -n "$NAMESPACE" ] || die "--namespace is required"
    # Mirror memory_router/namespaces.py's fixed roots -- fail closed on
    # anything that wouldn't validate on the memory-router side either.
    case "$NAMESPACE" in
      /global|/user/master) ;;
      /projects/*|/agents/*)
        NAME_PART="${NAMESPACE#*/*/}"
        [[ -n "$NAME_PART" && "$NAME_PART" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] \
          || die "Invalid name in '$NAMESPACE' -- expected /projects/<name> or /agents/<name>"
        ;;
      *) die "Invalid namespace '$NAMESPACE' -- must be /global, /user/master, /projects/<name>, or /agents/<name>" ;;
    esac
    TOPIC_PREFIX="ns:$NAMESPACE"

    COUNT=$(sqlite3 "$DB" "SELECT count(*) FROM observations WHERE project='$PROJECT' AND deleted_at IS NULL AND (topic_key IS NULL OR topic_key NOT LIKE 'ns:%');")
    [ "$COUNT" -gt 0 ] || { log "Nothing to retag -- 0 untagged observations in project '$PROJECT'"; exit 0; }
    log "$COUNT observation(s) in project '$PROJECT' will get topic_key prefixed with '$TOPIC_PREFIX:'"
    if [ -z "$YES" ]; then
      read -r -p "Proceed? [y/N] " reply
      [[ "$reply" =~ ^[Yy]$ ]] || { log "Aborted"; exit 1; }
    fi
    backup_db "retag"

    # Reads (sqlite3, read-only) drive the candidate list; writes go
    # through engram's own mem_update MCP tool (same JSON-RPC-over-stdio
    # protocol memory-router's EngramBackend itself uses) so hashing/FTS
    # stay consistent with however engram's own code maintains them --
    # never written directly with UPDATE.
    sqlite3 -json "$DB" "SELECT id, topic_key FROM observations WHERE project='$PROJECT' AND deleted_at IS NULL AND (topic_key IS NULL OR topic_key NOT LIKE 'ns:%');" \
      | ENGRAM_TOPIC_PREFIX="$TOPIC_PREFIX" python3 -c '
import json, os, subprocess, sys

rows = json.load(sys.stdin)
prefix = os.environ["ENGRAM_TOPIC_PREFIX"]

proc = subprocess.Popen(
    ["engram", "mcp", "--tools=agent"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
)

def call(method, params=None, req_id=None):
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if req_id is not None:
        msg["id"] = req_id
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    if req_id is not None:
        line = proc.stdout.readline()
        return json.loads(line)

call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "migrate-engram", "version": "1"}}, req_id=1)
call("notifications/initialized")

ok = 0
for row in rows:
    old = row.get("topic_key")
    new_topic = f"{prefix}:{old}" if old else prefix
    obs_id = row["id"]
    resp = call("tools/call", {"name": "mem_update", "arguments": {"id": obs_id, "topic_key": new_topic}}, req_id=obs_id)
    if "error" in resp:
        print(f"  id={obs_id} FAILED: {resp['error']}", file=sys.stderr)
    else:
        ok += 1

proc.stdin.close()
proc.wait(timeout=10)
print(f"Retagged {ok}/{len(rows)} observations", file=sys.stderr)
'
    log "Done. Verify: engram search --project $PROJECT ... or hermes mcp test memory-router"
    ;;

  merge)
    SOURCE_DB=""
    SOURCE_HOST=""
    SOURCE_PATH='$HOME/.engram/engram.db'
    YES=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --source-db) SOURCE_DB="$2"; shift 2 ;;
        --source-host) SOURCE_HOST="$2"; shift 2 ;;
        --source-path) SOURCE_PATH="$2"; shift 2 ;;
        --yes) YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown arg: $1" ;;
      esac
    done
    [ -n "$SOURCE_DB" ] || [ -n "$SOURCE_HOST" ] || die "--source-db or --source-host is required"

    WORKDIR=$(mktemp -d)
    trap 'rm -rf "$WORKDIR"' EXIT

    if [ -n "$SOURCE_HOST" ]; then
      # Take the consistent snapshot ON the remote side (it may have its
      # own live WAL) and fetch only that single, already-safe file --
      # never scp the raw engram.db (see WAL note in the file header).
      log "Snapshotting $SOURCE_HOST:$SOURCE_PATH remotely, then fetching it"
      REMOTE_TMP="/tmp/engram-merge-$$-$(date +%s).db"
      # shellcheck disable=SC2029  # remote-side expansion of $SOURCE_PATH/$REMOTE_TMP intentional
      ssh "$SOURCE_HOST" "sqlite3 '$SOURCE_PATH' \".backup '$REMOTE_TMP'\""
      scp -q "$SOURCE_HOST:$REMOTE_TMP" "$WORKDIR/engram.db"
      ssh "$SOURCE_HOST" "rm -f '$REMOTE_TMP'"
    else
      [ -f "$SOURCE_DB" ] || die "Source db not found: $SOURCE_DB"
      # Same WAL-safety concern applies to a local --source-db (it could
      # be another live engram instance on this same machine).
      sqlite_backup "$SOURCE_DB" "$WORKDIR/engram.db"
    fi

    log "Exporting from source snapshot"
    ENGRAM_DATA_DIR="$WORKDIR" engram export "$WORKDIR/export.json" 2>&1 | sed 's/^/  /' >&2

    log "Preview of what will be imported into $DB:"
    python3 -c "
import json
d = json.load(open('$WORKDIR/export.json'))
for k in ('sessions', 'observations', 'prompts'):
    v = d.get(k, [])
    print(f'  {k}: {len(v)}')
" >&2

    if [ -z "$YES" ]; then
      read -r -p "Import into $DB? [y/N] " reply
      [[ "$reply" =~ ^[Yy]$ ]] || { log "Aborted (export kept at $WORKDIR/export.json -- copy it out before it's cleaned up)"; trap - EXIT; exit 1; }
    fi

    backup_db "merge"
    log "Before:"; engram stats
    engram import "$WORKDIR/export.json"
    log "After:"; engram stats

    # Safety net for the known user_prompts non-dedupe bug (see header) --
    # a no-op if the import didn't create any duplicates.
    DUPES=$(sqlite3 "$DB" "SELECT count(*) FROM (SELECT content, session_id, created_at FROM user_prompts GROUP BY content, session_id, created_at HAVING count(*) > 1);")
    if [ "$DUPES" -gt 0 ]; then
      log "Deduping $DUPES duplicate prompt group(s) introduced by import..."
      sqlite3 "$DB" "DELETE FROM user_prompts WHERE id IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY content, session_id, created_at ORDER BY id) rn FROM user_prompts) WHERE rn > 1);"
      log "Prompts after dedupe: $(sqlite3 "$DB" 'SELECT count(*) FROM user_prompts;')"
    fi
    log "Done. Merged projects may need 'engram projects consolidate --dry-run' if names differ across machines."
    ;;

  -h|--help) usage; exit 0 ;;
  *) usage; exit 1 ;;
esac
