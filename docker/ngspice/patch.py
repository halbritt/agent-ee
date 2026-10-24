#!/usr/bin/env python3
"""Apply the inverse-pswitch resistance floor correction to ngspice 47."""
import hashlib
import sys
from pathlib import Path

path = Path(sys.argv[1]) / 'src/xspice/icm/xtradev/pswitch/cfunc.mod'
expected = '83c2f7c80125348f59b8bd37c51561bb37fcd6713630c466e47fc95196077d08'
if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
    raise SystemExit('ngspice pswitch source differs from the reviewed revision')
text = path.read_text()
old = 'if(r<r_on) r=r_on;/* minimum resistance limiter */'
new = 'if (r < fmin(r_on, r_off)) r = fmin(r_on, r_off); /* minimum resistance limiter */'
if text.count(old) != 2:
    raise SystemExit('ngspice pswitch correction requires both threshold branches')
path.write_text(text.replace(old, new))
