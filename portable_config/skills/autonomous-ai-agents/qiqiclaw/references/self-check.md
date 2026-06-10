# Manual Deep Self-Check Recipe

Beyond `qiqiclaw doctor`, this is a comprehensive manual diagnostic you can run to produce a full
health report for a QiQi claw instance. Useful for onboarding, debugging, or documentation.

## What to Collect

### 1. System Environment (parallel batch)
```bash
uname -a
cat /etc/os-release | head -5
echo "SHELL=$SHELL USER=$USER PWD=$PWD HOME=$HOME"
ip addr show | grep "inet "
df -h / && free -h | head -2 && nproc
# GPU (if available)
nvidia-smi 2>/dev/null | head -10 || echo "No NVIDIA GPU"
lspci 2>/dev/null | grep -i vga || true
# WSL specific
cat /etc/wsl.conf 2>/dev/null || echo "Not WSL"
cat /proc/version 2>/dev/null | head -1
```

### 2. Runtime Versions (parallel batch)
```bash
python3 --version && pip --version
node --version 2>/dev/null || echo "node: absent"
git --version
curl --version | head -1
wget --version | head -1
```

### 3. QiQi Framework
```bash
which qiqiclaw && qiqiclaw version        # installed binary
pip show qiqiclaw | head -3            # pip package info
cat ~/.qiqiclaw/config.yaml                # full config
cat ~/.qiqiclaw/memory/*.yaml 2>/dev/null  # persistent memory contents (may be SQLite-backed instead)
```

### 4. Skills & Tools Inventory
- `skills_list` tool — count + categories
- Config section: `toolsets` key for enabled sets
- Available tools: the `version` banner lists the count

### 5. Persistent State
- `cronjob(action="list")` — active scheduled jobs
- `session_search` — recent sessions count
- `memory` — check if any entries exist (user + agent memories)

### 6. Network Connectivity
```bash
ping -c 2 -W 1 8.8.8.8          # ICMP baseline
curl -s -o /dev/null -w "%{http_code}" --max-time 3 https://www.google.com
```
Also note whether the model API itself is reachable (implicit from the running session).
- **WSL2 DNS nuance**: `resolvectl query` may work while `nslookup`/`curl` fail — they use different resolution paths. If they disagree, DNS is broken and needs `references/wsl2-troubleshooting.md`.

### 7. Report Structure

Group findings into sections:
1. System Environment (OS, kernel, WSL status, hostname, IP, disk, RAM, CPU)
2. Toolchain & Runtime (Python, Node, Git, curl, wget versions)
3. QiQi Framework (version, config, model, toolsets, tools count)
4. Skills Inventory (count by category)
5. Persistent State (memory, cron, sessions)
6. Key Configuration Summary (important toggles at a glance)
7. Network Connectivity (ICMP + HTTPS)
8. Self-Check Scorecard (letter grades per dimension + overall)
9. Recommendations (actionable next steps)

### Pitfalls

- **Config path varies**: this installation uses `~/.qiqiclaw/` (not `~/.qiqiclaw/`). Always check before running.
- **Desktop path under WSL**: `/mnt/c/Users/<username>/Desktop/`. Discover username with `ls /mnt/c/Users/`.
- **Tirith warning**: if `tirith` binary is missing, the security scanner falls back to pattern matching. The banner prints a warning on startup.
- **Memory may be SQLite**: `~/.qiqiclaw/memory/*.yaml` may be empty if using the default SQLite backend. Check with the `memory` tool instead.
- **WSL2 DNS may be broken**: `ping 8.8.8.8` works but `nslookup`/`curl` fail → see `references/wsl2-troubleshooting.md`.
- **PIP externally-managed**: Ubuntu 24.04 blocks `pip install --user`. Use venv or pipx instead.
- **Sudo may be blocked**: The terminal tool may block sudo even when authorized. Fall back to writing a `.sh` script for manual execution.
- **Parallelize**: terminal calls in categories 1–2 can run in a single parallel batch. Skills/memory/state checks use native tools so batch those too.
