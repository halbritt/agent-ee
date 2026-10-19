#!/usr/bin/env python3
"""Scratch-only synthetic benchmark preparation, execution and reporting."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from agent_ee import benchmark_run as benchmark


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    for name in ('fetch','prepare','run','report','closure'):
        command = sub.add_parser(name)
        command.add_argument('manifest',type=Path)
        if name=='fetch':
            command.add_argument('cache',type=Path)
            command.add_argument('--archives',type=Path)
            command.add_argument('--git-source',action='append',default=[],metavar='BOARD=REPOSITORY',
                                 help='read an owner board from a local Git repository (read-only)')
        elif name=='prepare':
            command.add_argument('cache',type=Path)
            command.add_argument('output',type=Path)
        elif name=='run':
            command.add_argument('prepared',type=Path)
            command.add_argument('output',type=Path)
            command.add_argument('--expectation-commit',required=True)
            command.add_argument('--image',required=True)
        elif name=='report':
            command.add_argument('first',type=Path)
            command.add_argument('--second',type=Path)
        else:
            command.add_argument('cache',type=Path)
            command.add_argument('--closure',type=Path,required=True)
            command.add_argument('--run',type=Path,action='append',default=[],
                                 help='retained run whose runtime and native versions must match the closure')
    rerun = sub.add_parser('native-rerun',help='re-execute retained runs from their captured inputs')
    rerun.add_argument('first',type=Path);rerun.add_argument('output',type=Path)
    rerun.add_argument('--repeats',type=int,default=2)
    rerun.add_argument('--image',help='run inside this pinned image from the committed implementation')
    internal = sub.add_parser('_execute',help=argparse.SUPPRESS)
    internal.add_argument('prepared',type=Path);internal.add_argument('output',type=Path)
    args = parser.parse_args()
    if args.command=='fetch':
        sources = dict(item.split('=',1) for item in args.git_source)
        print(benchmark.fetch(args.manifest,args.cache,archives=args.archives,git_sources=sources))
    elif args.command=='prepare':
        print(benchmark.prepare(args.manifest,args.cache,args.output))
    elif args.command=='run':
        print(benchmark.run(args.manifest,args.prepared,args.output,args.expectation_commit,args.image))
    elif args.command=='report':
        print(json.dumps(benchmark.report(args.manifest,args.first,args.second),indent=2,allow_nan=False))
    elif args.command=='closure':
        print(json.dumps(benchmark.verify_closure(args.manifest,args.cache,args.closure,args.run),indent=2,allow_nan=False))
    elif args.command=='native-rerun':
        if args.image:
            summary = benchmark.image_native_rerun(args.first,args.output,args.image,args.repeats)
        else:
            summary = benchmark.native_rerun(args.first,args.output,args.repeats)
        print(json.dumps({k:summary[k] for k in ('exact_cases','semantic_cases','native_exact_cases',
                                                  'native_semantic_cases','all_cases','same_font_substitutions',
                                                  'same_native_runtime')}))
    else:
        benchmark.execute_batch(args.prepared,args.output)


if __name__=='__main__':
    main()
