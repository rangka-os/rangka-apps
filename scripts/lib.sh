# Shared by the scripts in this folder: source it, do not run it.
# Exports ROOT (this repo), RANGKA_OS (rangka-os checkout), RANGKA_BIN (kernel binary) and CONNECTORS.

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

# RANGKA_OS: env, else the first ancestor of this repo that has rangka-os/kernel.
if [ -z "${RANGKA_OS:-}" ]; then
  d=$ROOT
  while [ "$d" != "/" ]; do
    d=$(dirname "$d")
    if [ -d "$d/rangka-os/kernel" ]; then RANGKA_OS=$d/rangka-os; break; fi
  done
fi
if [ -z "${RANGKA_OS:-}" ] || [ ! -d "$RANGKA_OS/kernel" ]; then
  echo "lib.sh: rangka-os not found; set RANGKA_OS to its checkout" >&2
  exit 1
fi

# RANGKA_BIN: env, else the kernel binary, built when missing.
RANGKA_BIN=${RANGKA_BIN:-$RANGKA_OS/kernel/bin/rangka}
if [ ! -x "$RANGKA_BIN" ]; then
  make -C "$RANGKA_OS/kernel" build >&2
fi

# CONNECTORS: env, else rangka-connectors next to rangka-os.
CONNECTORS=${CONNECTORS:-$(dirname "$RANGKA_OS")/rangka-connectors}

export ROOT RANGKA_OS RANGKA_BIN CONNECTORS
