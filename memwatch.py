#!/usr/bin/env python3
"""memwatch — keeps a Mac from freezing under heavy AI workloads.

Runs always (launchd, --loop). Checks every 20s, every 5s under pressure.
When memory stays dangerous for ~10s it SIGSTOPs the most recently started big job
(heavy jobs, plus any python/node/ffmpeg/bun/java process using 4GB+), and SIGCONTs it
when memory recovers — nothing is killed, work resumes where it stopped. Apps are never touched.
"""
import json, os, re, signal, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from heavy import mem, D, Q, MIN_FREE

STATE = D / "memwatch.json"; LOG = D / "memwatch.log"
SWAP_RATE_DANGER = float(os.environ.get("HEAVY_SWAP_RATE", "2000"))  # swapouts/sec (16KB pages) that mean "thrashing right now"
MAX_PAUSE = 3 * 3600   # safety net: a job paused 3h+ resumes unless pressure is critical
BIG_GB = float(os.environ.get("HEAVY_BIG_GB", "4"))
KINDS = re.compile(r"^(python[\d.]*|Python|node|ffmpeg|bun|java)$")   # scripts/converters only; GUI apps are never paused
PROTECT = ("memwatch", "heavy") + tuple(x for x in os.environ.get("HEAVY_PROTECT", "").split(",") if x)  # never pause these


def sh(*a): return subprocess.run(a, capture_output=True, text=True).stdout


def note(title, body):
    subprocess.run(["osascript", "-e", f'display notification {json.dumps(body, ensure_ascii=False)} with title {json.dumps(title, ensure_ascii=False)}'])
    with open(LOG, "a") as f: f.write(f"{time.strftime('%m-%d %H:%M')} {title} {body}\n")


def top_mem(n):
    """[(pid, GB)] by real footprint. ps RSS misses swapped-out memory, top's MEM doesn't."""
    out = sh("top", "-l", "1", "-o", "mem", "-n", str(n), "-stats", "pid,mem")
    return [(int(p), int(v) / {"K": 1048576, "M": 1024, "G": 1}[u]) for p, v, u in re.findall(r"^(\d+)\s+(\d+)([KMG])\+?-?\s*$", out, re.M)]


def name_of(cmd):
    w = cmd.split()
    return next((x.split("/")[-1] for x in w if x.endswith((".py", ".js", ".mjs", ".sh"))), w[0].split("/")[-1] if w else "?")


def top3():
    return [f"{name_of(sh('ps', '-o', 'command=', '-p', str(p)))} {gb:.1f}GB" for p, gb in top_mem(3)]


def real_heavy(j):
    """A queue ticket counts only if its pid is a heavy process and child is its child (blocks forged tickets)."""
    me = sh("ps", "-o", "command=", "-p", str(j.get("pid", 0)))
    par = sh("ps", "-o", "ppid=", "-p", str(j.get("child", 0))).strip()
    return "heavy" in me and par == str(j.get("pid"))


def pause_resume(level, lvl, free):
    n = 0
    for f in Q.glob("*.json"):
        try: j = json.loads(f.read_text()); g = j.get("child")
        except Exception: continue
        if not g or not real_heavy(j): continue
        try:
            if level == 2 and j["state"] == "run":
                os.killpg(g, signal.SIGSTOP); j.update(state="paused", pt=time.time())
                note("⏸ heavy job paused", f"low memory ({free}% free) → {j['cmd'][:60]} · resumes automatically")
            elif j["state"] == "paused" and ((lvl == 1 and free >= MIN_FREE) or (lvl < 4 and time.time() - j.get("pt", 0) > MAX_PAUSE)):
                os.killpg(g, signal.SIGCONT); j["state"] = "run"
                note("▶ heavy job resumed", f"{free}% free → {j['cmd'][:60]}")
            else:
                n += j["state"] == "paused"; continue
            f.write_text(json.dumps(j))
        except ProcessLookupError: pass
    return n


def age(et):  # ps etime [[dd-]hh:]mm:ss → seconds
    d, _, t = et.strip().rpartition("-"); p = [int(x) for x in t.split(":")]
    while len(p) < 3: p.insert(0, 0)
    return int(d or 0) * 86400 + p[0] * 3600 + p[1] * 60 + p[2]


def bigs():
    """Big jobs started WITHOUT heavy: BIG_GB+, script kinds only, minus protected and heavy-managed."""
    heavy_g = {json.loads(f.read_text()).get("child") for f in Q.glob("*.json")}
    res = []
    for pid, gb in top_mem(20):
        if gb < BIG_GB: continue
        row = sh("ps", "-o", "pgid=,etime=,command=", "-p", str(pid)).split(None, 2)
        if len(row) < 3: continue
        g, et, cmd = row
        if not KINDS.match(cmd.split()[0].split("/")[-1]) or any(x in cmd for x in PROTECT) or int(g) in heavy_g: continue
        cwd = next((l[1:] for l in sh("lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn").split("\n") if l.startswith("n")), "")
        res.append({"pid": pid, "gb": round(gb, 1), "age": age(et), "name": name_of(cmd), "where": cwd.replace(str(Path.home()), "~")})
    return res


def sig(pid, s):
    for c in [pid] + [int(x) for x in sh("pgrep", "-P", str(pid)).split()]:
        try: os.kill(c, s)
        except OSError: pass


def foreign(st, level, lvl, free):
    """Machine-wide: pause the youngest big job on danger (or when 2+ run under warn); resume oldest-paused first. One per tick."""
    paused = {int(k): v for k, v in st.get("foreign", {}).items()}
    for p in list(paused):
        try: os.kill(p, 0)
        except OSError: paused.pop(p)
    running = [b for b in bigs() if b["pid"] not in paused]
    if running and (level == 2 or (level == 1 and len(running) >= 2)):
        b = min(running, key=lambda x: x["age"])
        sig(b["pid"], signal.SIGSTOP); paused[b["pid"]] = {**b, "t": time.time()}
        note("⏸ big job paused", f"{b['name']} {b['gb']}GB ({b['where']}) · {free}% free · resumes automatically")
    elif paused and ((lvl == 1 and free >= MIN_FREE) or (lvl < 4 and time.time() - min(v["t"] for v in paused.values()) > MAX_PAUSE)):
        p = min(paused, key=lambda k: paused[k]["t"]); b = paused.pop(p)
        sig(p, signal.SIGCONT)
        note("▶ big job resumed", f"{b['name']} ({b['where']}) · {free}% free")
    st["foreign"] = paused
    return len(paused)


def judge(lvl, free, rate):
    """0 ok · 1 warn · 2 danger. Pressure AND (low free OR swapping out fast right now), like earlyoom's AND rule.
    Swap *size* is useless on macOS: the OS resizes total swap, and 55GB of old idle swap with zero
    new swapouts is harmless. Only the swapout *rate* means thrashing."""
    if lvl >= 4 or (lvl >= 2 and (free < 20 or rate > SWAP_RATE_DANGER)): return 2
    return 1 if lvl >= 2 and free < 30 else 0


def tick(st, streak):
    lvl, free, used = mem()
    so = int(re.search(r"Swapouts:\s+(\d+)", sh("vm_stat")).group(1)); t = time.time()
    rate = (so - st.get("so", so)) / max(1, t - st.get("so_t", t - 1)); st["so"], st["so_t"] = so, t
    level = judge(lvl, free, rate)
    streak = streak + 1 if level else 0
    act = level if streak >= 2 else 0           # must persist 2 checks (~10s) before acting, like oomd
    ok_lvl = 1 if level == 0 else max(lvl, 2)
    n = pause_resume(act, ok_lvl, free) + foreign(st, act, ok_lvl, free)
    now = time.time()
    if level and now - st.get(str(level), 0) > 900:
        note("🔴 Mac memory critical" if level == 2 else "🟡 Mac memory warning", f"{free}% free · swap {used:.0f}GB (+{rate:.0f} pages/s) · biggest: {', '.join(top3())}"); st[str(level)] = now
    if n and now - st.get("remind", 0) > 1800:
        note("⏸ jobs still paused", f"{n} paused · resume when memory frees up (or after 3h) · heavy status"); st["remind"] = now
    if level == 0 and now - st.get("tok", 0) > 600:
        st["tok"] = now
        try:
            from heavy import tokens_refresh
            j = tokens_refresh(); r = j["tokens"] / j["avg7"] * 100 if j["avg7"] else 0
            for cut in (300, 200, 150):
                key = f"tok{cut}-{j['day']}"
                if r >= cut and not st.get(key):
                    note("🪙 heavy token day", f"today {j['tokens'] / 1e6:.0f}M = {r:.0f}% of a usual day · API-equivalent ${j['cost']}"); st[key] = 1
                    break
        except Exception: pass                     # ccusage is optional
    STATE.write_text(json.dumps(st))
    return lvl, streak


def main():
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    if "--loop" not in sys.argv:
        tick(st, 1); return
    streak = 0
    while True:
        try: lvl, streak = tick(st, streak)
        except Exception as e:
            lvl = 1
            with open(LOG, "a") as f: f.write(f"{time.strftime('%m-%d %H:%M')} error {e}\n")
        time.sleep(5 if lvl >= 2 or streak else 20)


if __name__ == "__main__":
    main()
