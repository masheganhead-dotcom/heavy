# heavy

**Stop your Mac from freezing when you run too many AI jobs at once.**

You start a training run, a local model, an ffmpeg render and three Claude agents. Swap climbs to 50 GB, the Mac stalls, and you lose an hour before you notice. `heavy` is a tiny, dependency-free (stdlib Python) guard for macOS:

- **`heavy <cmd>`**: a machine-wide queue. One heavy job at a time, and it starts only when memory is actually free.
- **`memwatch`**: an always-on watcher. When memory stays dangerous it **pauses** (SIGSTOP) the most recently started big job and **resumes** it (SIGCONT) when memory recovers. Nothing is killed and nothing is lost: the job continues from where it stopped. Covers jobs started *without* `heavy` too (python / node / ffmpeg / bun / java using 4 GB+). GUI apps are never touched.
- **Token control for Claude Code** (optional): queue scripted `claude -p` calls (3 at a time) and get a notification when today's usage runs far above your usual day.

```
$ heavy status
memory: 35% free · pressure normal · swap 41.3GB (starts at 30%+ free, pressure normal)
  ▶ running  since 04:43  python train.py
  ⏳ waiting  since 04:51  ffmpeg -i take3.mov ...
tokens today 521M (70% of a usual day) · API-equivalent $270 · claude calls 1/3
```

## Install

```sh
git clone https://github.com/masheganhead-dotcom/heavy && cd heavy
./install.sh                  # heavy + memwatch
./install.sh --claude-shim    # optional: also queue `claude -p` calls
```

Requires macOS (Apple Silicon or Intel) and Python 3.9+. Optional: [`ccusage`](https://github.com/ryoppippi/ccusage) for token counts.

## Use

```sh
heavy python train.py              # waits its turn and for free memory, then runs
heavy ffmpeg -i in.mov out.mp4
HEAVY_MAX_GB=12 heavy python x.py  # hard cap: the kernel kills it above 12 GB (disposable jobs only)
heavy status
heavy tokens
```

| Variable | Default | Meaning |
|---|---|---|
| `HEAVY_MIN_FREE` | 30 | start only at this % of free memory or more |
| `HEAVY_MAX_GB` | none | hard memory cap via `taskpolicy -m` (**kills** the job when exceeded) |
| `HEAVY_CLAUDE_SLOTS` | 3 | concurrent `claude -p` calls |
| `HEAVY_BIG_GB` | 4 | memwatch treats non-heavy jobs this big as "heavy" |
| `HEAVY_SWAP_RATE` | 2000 | swapouts/sec (16 KB pages) that count as thrashing |
| `HEAVY_PROTECT` | — | comma-separated command fragments memwatch must never pause (e.g. your always-on servers) |

## How it decides

macOS exposes a real pressure signal (`kern.memorystatus_vm_pressure_level`: normal / warn / critical). memwatch acts only when pressure is up **and** (free memory < 20% **or** swapping out fast right now), and only if that lasts two checks (~10 s). It checks every 20 s, and every 5 s once pressure rises.

Lessons baked in:
- **Swap size lies on macOS.** The OS grows and shrinks total swap, so `used/total` reads ~90%, and 55 GB of old idle swap is harmless. heavy watches the swap-out *rate* (`vm_stat`), not the size.
- **RSS lies too.** It excludes swapped-out memory, so the 30 GB culprit looks small. heavy uses `top`'s footprint.
- **Pausing never frees memory instantly.** It stops growth; held pages drift to swap. If you need memory back *now*, kill the job yourself.
- **Safety net.** A job paused 3 h+ resumes unless pressure is critical, and you get a reminder every 30 min while anything is paused.

## Claude Code pane (optional)

`claude-mod/` is a Claude Code mod that keeps a side pane and a status line (`🧠 35% · normal · ▶1 ⏸0 ⏳1 · 🪙 521M (70%)`) updated every 10 s. Load it with `claude --plugin-dir claude-mod`.

## Prior art

Linux has [earlyoom](https://github.com/rfjakob/earlyoom), [oomd](https://github.com/facebookincubator/oomd) and [nohang](https://github.com/hakavlad/nohang). The AND-condition and "must persist" rules come from them. Job queues like [pueue](https://github.com/Nukesor/pueue) and [nq](https://github.com/leahneukirchen/nq) don't look at memory. We couldn't find a pressure-aware queue for macOS, so this is one.

## Test

```sh
python3 tests/test_heavy.py   # uses a temp folder, never touches your real queue
```

MIT licensed.
