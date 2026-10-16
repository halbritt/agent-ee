* agent-ee generated ngspice control script
.control
set num_threads=1
set noaskquit
source /work/deck.cir
run
write /work/result.raw
quit
.endc
.end
