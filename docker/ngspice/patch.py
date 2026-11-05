#!/usr/bin/env python3
"""Apply the inverse-pswitch resistance floor correction to ngspice 47."""
import hashlib
import sys
from pathlib import Path

path = Path(sys.argv[1]) / 'src/xspice/icm/xtradev/pswitch/cfunc.mod'
text = path.read_text()
old = 'if(r<r_on) r=r_on;/* minimum resistance limiter */'
new = 'if (r < fmin(r_on, r_off)) r = fmin(r_on, r_off); /* minimum resistance limiter */'
if text.count(old) != 2:
    raise SystemExit('ngspice pswitch correction requires both threshold branches')
path.write_text(text.replace(old, new))
