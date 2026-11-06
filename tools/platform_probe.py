"""Phase 0 platform probe: directory fsync after os.replace, os.replace on an open
file, and a hard kill. Usage: python3 tools/platform_probe.py [workdir]
Default workdir is .torture-work/platform-probe (real disk, inside the repo)."""
import os, sys, subprocess, platform, time

work = sys.argv[1] if len(sys.argv) > 1 else os.path.join(".torture-work", "platform-probe")
os.makedirs(work, exist_ok=True)
print("python:", sys.executable, platform.python_version())
print("platform:", platform.platform())
print("work dir:", os.path.abspath(work))

# 1. Directory fsync after a file swap
a = os.path.join(work, "probe.data"); b = os.path.join(work, "probe.data.compact.tmp")
with open(a, "wb") as f: f.write(b"old"); f.flush(); os.fsync(f.fileno())
with open(b, "wb") as f: f.write(b"new"); f.flush(); os.fsync(f.fileno())
os.replace(b, a)
try:
    fd = os.open(work, os.O_RDONLY)
    try:
        os.fsync(fd)
        print("dir fsync after os.replace: OK")
    finally:
        os.close(fd)
except OSError as e:
    print("dir fsync after os.replace: FAILED", repr(e))
with open(a, "rb") as f: print("content after swap:", f.read())

# 2. Replacing a file that is still open (two handles open, like the engine)
with open(a, "wb") as f: f.write(b"old"); f.flush(); os.fsync(f.fileno())
wh = open(a, "r+b", buffering=0); rh = open(a, "rb", buffering=0)
with open(b, "wb") as f: f.write(b"new"); f.flush(); os.fsync(f.fileno())
try:
    os.replace(b, a)
    print("os.replace while 2 handles open: OK")
    rh.seek(0); print("  old read handle still sees:", rh.read())
    with open(a, "rb") as f: print("  fresh handle sees:", f.read())
except OSError as e:
    print("os.replace while 2 handles open: FAILED", repr(e))
wh.close(); rh.close()

# 3. Hard kill with Popen.kill()
p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
time.sleep(0.2); p.kill(); rc = p.wait()
print("Popen.kill() -> returncode", rc, "(-9 means SIGKILL)")
for n in (a,):
    os.remove(n)
