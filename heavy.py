#!/usr/bin/env python3
"""heavy — a memory-aware queue for heavy jobs on macOS.

  heavy <cmd...>     run cmd when it's its turn AND memory is healthy (one heavy job at a time, machine-wide)
  heavy status       memory, running / paused / waiting jobs, token usage
  heavy tokens       recount today's Claude token usage (needs ccusage)

Environment:
  HEAVY_MIN_FREE      start only when free memory >= this % (default 30)
  HEAVY_MAX_GB        hard memory cap for this job via `taskpolicy -m` — the kernel KILLS it above this, use only for disposable jobs
  HEAVY_CLAUDE_SLOTS  max concurrent `claude -p` calls when the shim is installed (default 3)
  HEAVY_DIR           state folder (default ~/.local/state/heavy)
"""
import fcntl, json, os, re, shutil, signal, subprocess, sys, time
from pathlib import Path

D = Path(os.environ.get("HEAVY_DIR") or Path.home() / ".local/state/heavy")
Q = D / "queue"; Q.mkdir(parents=True, exist_ok=True)
LOCK = D / "lock"
MIN_FREE = int(os.environ.get("HEAVY_MIN_FREE", "30"))
MAX_LVL = int(os.environ.get("HEAVY_MAX_LEVEL", "1"))  # allowed pressure level: 1 = normal only. Tests use 4.
LEVEL = {1: "normal", 2: "warn", 4: "critical"}


def mem():
    """(pressure level 1/2/4, free %, swap used GB) — straight from the kernel."""
    lvl = int(subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], capture_output=True, text=True).stdout or 1)
    out = subprocess.run(["memory_pressure", "-Q"], capture_output=True, text=True).stdout
    free = int(re.search(r"free percentage: (\d+)", out).group(1))
    sw = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True).stdout
    used = float(re.search(r"used = ([\d.]+)M", sw).group(1)) / 1024
    return lvl, free, used


def status():
    lvl, free, used = mem()
    print(f"memory: {free}% free · pressure {LEVEL.get(lvl, lvl)} · swap {used:.1f}GB (starts at {MIN_FREE}%+ free, pressure normal)")
    for f in sorted(Q.glob("*.json"), key=lambda p: p.stat().st_mtime):
        j = json.loads(f.read_text())
        try: os.kill(j["pid"], 0)
        except OSError: f.unlink(missing_ok=True); continue
        tag = {"run": "▶ running", "paused": "⏸ paused (low memory)"}.get(j["state"], "⏳ waiting")
        print(f"  {tag}  since {time.strftime('%H:%M', time.localtime(j['t']))}  {j['cmd'][:90]}")


# ── Token control 1: queue `claude -p` calls (scripts that call Claude) to SLOTS at a time, machine-wide ──
CQ = D / "claude-slots"; CQ.mkdir(exist_ok=True)
SLOTS = int(os.environ.get("HEAVY_CLAUDE_SLOTS", "3"))


def real_claude():
    if os.environ.get("HEAVY_REAL_CLAUDE"): return os.environ["HEAVY_REAL_CLAUDE"]
    for d in os.environ.get("PATH", "").split(":"):   # first `claude` on PATH that isn't our shim
        p = Path(d) / "claude"
        try:
            if p.is_file() and b"heavy-shim" not in p.read_bytes()[:400]: return str(p)
        except OSError: pass
    sys.exit("heavy: real `claude` not found on PATH (set HEAVY_REAL_CLAUDE)")


def claude_busy():
    n = 0
    for i in range(SLOTS):
        with open(CQ / f"slot{i}", "a") as f:
            try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); fcntl.flock(f, fcntl.LOCK_UN)
            except OSError: n += 1
    return n


def claude_gate(args):
    """Grab a free slot, then exec the real claude while still holding the lock. The lock frees itself when claude exits."""
    real, waited = real_claude(), 0
    while True:
        for i in range(SLOTS):
            fd = os.open(CQ / f"slot{i}", os.O_RDWR | os.O_CREAT)
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError: os.close(fd); continue
            os.set_inheritable(fd, True)
            os.execv(real, [real] + args)
        if waited == 0: print(f"[heavy] {SLOTS} claude calls already running, waiting for a slot", file=sys.stderr, flush=True)
        waited += 1; time.sleep(2)


# ── Token control 2: usage today vs. your 7-day average, via ccusage (optional) ──
TOK = D / "tokens.json"


def tokens_refresh():
    cc = shutil.which("ccusage")
    if not cc: raise RuntimeError("ccusage not installed (npm i -g ccusage)")
    since = time.strftime("%Y%m%d", time.localtime(time.time() - 8 * 86400))
    days = json.loads(subprocess.run([cc, "daily", "--since", since, "--json"], capture_output=True, text=True, timeout=120,
                                   env=dict(os.environ, PATH=f"{Path(cc).parent}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")).stdout)["daily"]
    today = time.strftime("%Y-%m-%d")
    t = next((d for d in days if d["period"] == today), {"totalTokens": 0, "totalCost": 0})
    past = [d for d in days if d["period"] != today][-7:]
    avg = sum(d["totalTokens"] for d in past) / max(1, len(past))
    j = {"day": today, "tokens": t["totalTokens"], "cost": round(t["totalCost"]), "avg7": avg, "t": time.time()}
    TOK.write_text(json.dumps(j)); return j


def tokens_line():
    try: j = json.loads(TOK.read_text())
    except Exception: return "tokens: not counted yet (heavy tokens)"
    r = j["tokens"] / j["avg7"] * 100 if j["avg7"] else 0
    return f"tokens today {j['tokens'] / 1e6:.0f}M ({r:.0f}% of a usual day) · API-equivalent ${j['cost']} · claude calls {claude_busy()}/{SLOTS}"


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"): print(__doc__); return 0
    if a == ["status"]: status(); print(tokens_line()); return 0
    if a == ["tokens"]:
        try: tokens_refresh()
        except Exception as e: print(f"heavy: {e}"); return 1
        print(tokens_line()); return 0
    if a[0] == "--claude": return claude_gate(a[1:])
    me = Q / f"{os.getpid()}.json"
    info = {"pid": os.getpid(), "cmd": " ".join(a), "cwd": os.getcwd(), "t": time.time(), "state": "wait"}
    me.write_text(json.dumps(info))
    try:
        with open(LOCK, "w") as lk:
            # flock doesn't guarantee FIFO wake order: with 2+ waiters, order may differ from arrival.
            fcntl.flock(lk, fcntl.LOCK_EX)
            said = 0
            while True:
                lvl, free, used = mem()
                if lvl <= MAX_LVL and free >= MIN_FREE: break
                if time.time() - said > 300:
                    print(f"[heavy] waiting for memory: {free}% free (need {MIN_FREE}%), pressure {LEVEL.get(lvl, lvl)}", file=sys.stderr, flush=True); said = time.time()
                time.sleep(15)
            info.update(state="run", t=time.time()); me.write_text(json.dumps(info))
            print(f"[heavy] start: {info['cmd'][:80]} ({free}% free)", file=sys.stderr, flush=True)
            # Own process group, so memwatch can pause/resume the whole job tree at once.
            cap = os.environ.get("HEAVY_MAX_GB")
            p = subprocess.Popen((["taskpolicy", "-m", str(int(float(cap) * 1024))] if cap else []) + a, start_new_session=True)
            info["child"] = p.pid; me.write_text(json.dumps(info))

            def stop(*_):
                try: os.killpg(p.pid, signal.SIGCONT); os.killpg(p.pid, signal.SIGTERM)
                except OSError: pass
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
            rc = p.wait()
            if rc == -9 and cap: print(f"[heavy] killed by the kernel: went over the {cap}GB cap", file=sys.stderr)
            return 128 + -rc if rc < 0 else rc
    except KeyboardInterrupt:
        return 130
    finally:
        me.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
