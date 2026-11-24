#!/usr/bin/env python3
"""TRAVA tmux (PreToolUse, matcher Bash).

Incidente 24/11/2026: uma limpeza de disco apagou /tmp/tmux-<uid> e o bot
(tmux send-keys nesse socket) ficou ~45 min sem entregar mensagens ao dono.

Bloqueia (exit 2 + motivo em stderr) comando de remocao/movimentacao que atinja
/tmp/tmux-* ou glob amplo na raiz de /tmp (inclui find /tmp sem excluir tmux).
Vale para sessao principal, subagentes e headless (sem variavel de modo).
Uso: python3 tmux_guard.py [MAIA_HOME]   (stdin = JSON do hook)
Log: <MAIA_HOME>/logs/tmux_guard.log (MAIA_HOME = argv[1], senao env MAIA_HOME,
senao /opt/MAIA).
Rollback: remover a entrada deste hook (PreToolUse, matcher "Bash") do settings.json.
"""
import fnmatch
import json
import os
import posixpath
import re
import shlex
import sys
import time

MAIA_HOME = (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else os.environ.get("MAIA_HOME") or "/opt/MAIA")
LOG = os.path.join(MAIA_HOME, "logs", "tmux_guard.log")
MSG = (
    "TRAVA: limpeza atinge /tmp/tmux-* (socket por onde o bot entrega as mensagens do "
    "dono - incidente 24/11/2026). Refaca excluindo: find /tmp ... ! -path '/tmp/tmux-*' ! -type s"
)
_UID = os.getuid() if hasattr(os, "getuid") else 1000
PROTECTED_NAMES = tuple(dict.fromkeys(("tmux-0", "tmux-1000", "tmux-1001", "tmux-1002", "tmux-%d" % _UID))) + ("default",)
PROTECTED_PATHS = ("/tmp/tmux-0", "/tmp/tmux-%d" % _UID, "/tmp/tmux-%d/default" % _UID)
DESTRUCT = {"rm", "rmdir", "unlink", "shred", "mv", "tmpreaper", "tmpwatch"}
FIND_PATTERN_OPTS = {"-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-regex", "-iregex"}
INTERP = re.compile(r"\b(python3?|perl|node|ruby|php)\b")
INTERP_DEL = re.compile(r"rmtree|os\.remove|os\.unlink|os\.rmdir|os\.rename|\.unlink\(|unlinkSync|rmSync|rmdirSync|shutil\.move|\bunlink\b|\brmdir\b")
INTERP_TMP = re.compile(r"tmux|['\"]/tmp/?['\"]|/tmp/\*|/tmp/\.\*")
GLOB = re.compile(r"[*?\[]")


def toks(s):
    try:
        lex = shlex.shlex(s, posix=True, punctuation_chars=";&|<>()")
        lex.whitespace_split = True
        return list(lex)
    except ValueError:
        return s.replace("'", " ").replace('"', " ").split()


def units(cmd):
    # separa por ; && || e quebra de linha; pipe fica junto (find ... | xargs rm)
    return [u for u in re.split(r"\n|;|&&|\|\|", cmd) if u.strip()]


def hits_protected(path, in_tmp):
    p = path.strip()
    if in_tmp and not p.startswith("/") and not p.startswith("~") and not p.startswith("$"):
        p = "/tmp/" + p
    if not p.startswith("/"):
        return False
    if p in ("/", "/tmp", "/tmp/"):
        return True
    p = posixpath.normpath(p)
    if p in ("/", "/tmp"):
        return True
    if "tmux" in p and p.startswith("/tmp/"):
        return True
    if p.startswith("/tmp/") and GLOB.search(p):
        first = p[5:].split("/")[0]
        if first.startswith(".") and GLOB.search(first):
            return True  # /tmp/.* e variantes
        if any(fnmatch.fnmatchcase(n, first) for n in PROTECTED_NAMES[:-1]):
            return True
    return False


def pattern_hits(pat):
    return any(fnmatch.fnmatchcase(n, pat) for n in PROTECTED_NAMES) or any(
        fnmatch.fnmatchcase(p, pat) for p in PROTECTED_PATHS
    ) or "tmux" in pat


def check_find(t, in_tmp):
    i = t.index("find")
    starts = []
    j = i + 1
    while j < len(t) and not t[j].startswith("-") and t[j] not in ("!", "(", "|"):
        starts.append(t[j])
        j += 1
    excluded = False
    positives = []
    k = j
    while k < len(t):
        tok = t[k]
        if tok in FIND_PATTERN_OPTS and k + 1 < len(t):
            pat = t[k + 1]
            neg = k > 0 and t[k - 1] in ("!", "-not")
            prune = k + 2 < len(t) and t[k + 2] == "-prune"
            if (neg or prune) and "tmux" in pat:
                excluded = True
            elif not neg and not prune:
                positives.append(pat)
            k += 2
            continue
        k += 1
    for s in starts:
        sp = posixpath.normpath("/tmp/" + s) if (in_tmp and not s.startswith("/")) else s
        if "tmux" in sp and sp.startswith("/tmp"):
            return True
        if sp in ("/", "/tmp", "/tmp/") or posixpath.normpath(sp) in ("/", "/tmp"):
            if excluded:
                continue
            if positives and not any(pattern_hits(p) for p in positives):
                continue
            return True
    if any(pattern_hits(p) for p in positives) and not excluded:
        return any(s.startswith("/tmp") or s == "/" or (in_tmp and not s.startswith("/")) for s in starts)
    return False


def unit_blocks(u, in_tmp):
    t = toks(u)
    base = [os.path.basename(x) for x in t]
    destr = any(b in DESTRUCT for b in base) or "-delete" in t
    if "systemd-tmpfiles" in base and any(x in ("--clean", "--remove") for x in t):
        return True
    if not destr:
        return False
    if "find" in base:
        t2 = [("find" if os.path.basename(x) == "find" else x) for x in t]
        return check_find(t2, in_tmp)
    targets = [x for x in t if not x.startswith("-") and x not in ("!", "(", ")", "|", "&", "<", ">")]
    return any(hits_protected(x, in_tmp) for x in targets)


def blocks(cmd):
    if not cmd:
        return False
    if INTERP.search(cmd) and INTERP_DEL.search(cmd) and INTERP_TMP.search(cmd):
        return True
    in_tmp = bool(re.search(r"\bcd\s+['\"]?/tmp/?['\"]?\s*($|;|&|\|)", cmd, re.M))
    return any(unit_blocks(u, in_tmp) for u in units(cmd))


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        return 0
    if d.get("tool_name") != "Bash":
        return 0
    cmd = (d.get("tool_input") or {}).get("command", "") or ""
    try:
        bad = blocks(cmd)
    except Exception:
        bad = False
    if not bad:
        return 0
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "dec": "deny",
                                "agent": d.get("agent_type") or "", "cmd": cmd[:300]}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    sys.stderr.write(MSG + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
