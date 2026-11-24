"""Run: python3 tests/test_heavy.py — touches no real jobs or real queue (HEAVY_DIR = temp folder)."""
import os, subprocess, sys, tempfile, time
from pathlib import Path

os.environ["HEAVY_DIR"] = tempfile.mkdtemp()
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import memwatch as m

# 1. danger judgment: (pressure level, free %, swapouts/sec) -> 0 ok / 1 warn / 2 danger
#    (2, 40, 0) is a real case: 55GB of idle swap, nothing growing -> must NOT pause
for args, want in [((2, 40, 0), 0), ((2, 27, 5000), 2), ((2, 25, 0), 1), ((4, 50, 0), 2), ((1, 60, 9000), 0), ((2, 15, 0), 2)]:
    assert m.judge(*args) == want, (args, m.judge(*args))

# 2. queue: two heavy jobs never overlap
run_env = dict(os.environ, HEAVY_MAX_LEVEL="4", HEAVY_MIN_FREE="0")
log = Path(os.environ["HEAVY_DIR"]) / "order"
def job(n, s):
    return subprocess.Popen([sys.executable, str(ROOT / "heavy.py"), "sh", "-c", f"echo {n}start >> {log}; sleep {s}; echo {n}end >> {log}"], env=run_env)
a = job("A", 2); time.sleep(0.5); b = job("B", 1); a.wait(); b.wait()
lines = log.read_text().split()
assert lines[0][0] == lines[1][0] and lines[1].endswith("end"), lines

# 3. a forged queue ticket is ignored (no pausing random processes)
sleeper = subprocess.Popen(["sleep", "30"])
(m.Q / "1.json").write_text(f'{{"pid": {os.getpid()}, "child": {sleeper.pid}, "cmd": "x", "state": "run", "t": 0}}')
m.pause_resume(2, 4, 5)
stat = subprocess.run(["ps", "-o", "stat=", "-p", str(sleeper.pid)], capture_output=True, text=True).stdout
assert "T" not in stat, stat
sleeper.kill(); (m.Q / "1.json").unlink()

# 4. a real heavy job pauses and resumes where it stopped
cnt = Path(os.environ["HEAVY_DIR"]) / "cnt"
p = subprocess.Popen([sys.executable, str(ROOT / "heavy.py"), "sh", "-c", f"i=0; while [ $i -lt 8 ]; do i=$((i+1)); echo $i > {cnt}; sleep 1; done"], env=run_env)
time.sleep(2.5); m.pause_resume(2, 4, 5); x = cnt.read_text(); time.sleep(2)
assert cnt.read_text() == x, "kept running while paused"
m.pause_resume(0, 1, 99); p.wait()
assert cnt.read_text().strip() == "8"
print("ok: judge, queue, forged ticket, pause/resume")
