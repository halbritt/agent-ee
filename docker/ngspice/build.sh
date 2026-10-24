#!/bin/sh
# Build the qualified simulator; no project or component-specific model edits.
set -eu
pin=a80f6e3e95d51534905b1f23410a951802666656
root=/build/ngspice
scripts=/usr/local/share/agent-ee/ngspice
export SOURCE_DATE_EPOCH=1786454110

git clone --depth 1 --branch ngspice-47 https://git.code.sf.net/p/ngspice/ngspice "$root"
test "$(git -C "$root" rev-parse HEAD)" = "$pin"
python3 "$scripts/patch.py" "$root"
cd "$root"
./autogen.sh
./configure --prefix=/usr --without-x --with-readline=no --enable-xspice --disable-debug
make -j4
make DESTDIR=/out install
# Keep the upstream redistribution notices beside the installed simulator.
mkdir -p /out/usr/share/doc/ngspice /out/usr/share/agent-ee
cp COPYING /out/usr/share/doc/ngspice/COPYING
python3 - "$pin" "$scripts" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
root = Path('/out')
files = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in sorted(root.rglob('*')) if p.is_file()}
record = {'schema': 'agent-ee.ngspice-install/1', 'version': '47',
          'upstream': 'https://git.code.sf.net/p/ngspice/ngspice',
          'commit': sys.argv[1], 'correction': 'inverse-pswitch-resistance-floor/1',
          'patch_sha256': hashlib.sha256((Path(sys.argv[2]) / 'patch.py').read_bytes()).hexdigest(),
          'files': files}
(root / 'usr/share/agent-ee/ngspice-install.json').write_text(json.dumps(record, indent=2) + '\n')
PY
