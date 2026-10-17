"""Fixed Python-only replay launcher, invoked inside the networkless sandbox."""

import ctypes
import errno
import json
import platform
import resource
import runpy
import sys


def main():
    limits = json.loads(sys.argv[1])
    try:
        resource.setrlimit(resource.RLIMIT_AS, (limits['memory_bytes'],) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (limits['cpu_seconds'],) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
        # Linux x86-64 only. Reject alternate syscall ABIs before comparing
        # syscall numbers; x32 calls must not bypass the denied native numbers.
        if sys.platform != 'linux' or platform.machine() != 'x86_64':
            raise OSError('only Linux x86-64 replay is supported')

        class Filter(ctypes.Structure):
            _fields_ = [('code', ctypes.c_ushort), ('jt', ctypes.c_ubyte),
                        ('jf', ctypes.c_ubyte), ('k', ctypes.c_uint32)]

        class Program(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ushort), ('filter', ctypes.POINTER(Filter))]

        kill, allow, denied = 0x80000000, 0x7fff0000, 0x50000 | errno.EPERM
        instructions = [(0x20, 0, 0, 4), (0x15, 1, 0, 0xc000003e), (0x06, 0, 0, kill),
                        (0x20, 0, 0, 0), (0x35, 0, 1, 0x40000000), (0x06, 0, 0, kill)]
        # socket, socketpair, clone, fork, vfork, execve, ptrace, mount,
        # unshare, setns, process_vm_writev, memfd_create, bpf, execveat,
        # io_uring_setup and clone3. No replacement runtime or descendant can
        # escape the single-process address-space and CPU limits.
        # Also deny changing resource limits, System V IPC allocation and host
        # keyring access, which have no role in read-only replay.
        for number in (29, 41, 53, 56, 57, 58, 59, 64, 68, 69, 101, 160, 165,
                       248, 249, 250, 272, 302, 308, 311, 319, 321, 322, 425, 435):
            instructions.extend(((0x15, 0, 1, number), (0x06, 0, 0, denied)))
        instructions.append((0x06, 0, 0, allow))
        filters = (Filter * len(instructions))(*(Filter(*i) for i in instructions))
        program = Program(len(instructions), filters)
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            raise OSError(ctypes.get_errno(), 'PR_SET_NO_NEW_PRIVS failed')
        if libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0:  # PR_SET_SECCOMP, FILTER
            raise OSError(ctypes.get_errno(), 'PR_SET_SECCOMP failed')
    except (OSError, ValueError) as exc:
        print(f'replay isolation setup failed: {exc}', file=sys.stderr)
        return 125
    print('agent-ee replay policy ready', file=sys.stderr, flush=True)
    sys.argv = ['/bundle/script.py']
    runpy.run_path('/bundle/script.py', run_name='__main__')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
