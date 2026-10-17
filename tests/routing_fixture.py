"""Original, generated pad-to-pad native board fixtures (no library assets)."""

def make_board(path, lengths=(10, 10.3), vias=0, unrouted=False, width=0.2, drill=0.3,
               layers=2, outline=(20, 15), open_outline=False, net_names=('D+', 'D-')):
    """outline=None saves no Edge.Cuts; open_outline omits the closing edge."""
    import pcbnew as k
    b = k.BOARD()
    b.SetCopperLayerCount(layers)
    for i, length in enumerate(lengths):
        n = k.NETINFO_ITEM(b, net_names[i]); b.Add(n)
        positions = [(0, 0), (length, 0)]
        points = [k.VECTOR2I(round(x*1e6), (i*3+2)*1000000) for x, y in positions]
        for j, position in enumerate(points):
            fp = k.FOOTPRINT(b); fp.SetReference(f'J{i}{j}'); b.Add(fp)
            pad = k.PAD(fp); pad.SetNumber('1'); pad.SetAttribute(k.PAD_ATTRIB_SMD)
            pad.SetSize(k.VECTOR2I(400000, 400000))
            ls = k.LSET(); ls.AddLayer(k.B_Cu if j and vias % 2 else k.F_Cu)
            pad.SetLayerSet(ls); pad.SetPosition(position); pad.SetNet(n); fp.Add(pad)
        if unrouted and i == 1:
            continue
        starts = [points[0]]
        for j in range(vias):
            position = k.VECTOR2I((j+1)*2000000, points[0].y)
            via = k.PCB_VIA(b); via.SetPosition(position); via.SetWidth(600000)
            via.SetDrill(round(drill*1e6)); via.SetViaType(k.VIATYPE_THROUGH)
            via.SetLayerPair(k.F_Cu, k.B_Cu); via.SetNet(n); b.Add(via)
            starts.append(position)
        starts.append(points[1])
        for j, (start, end) in enumerate(zip(starts, starts[1:])):
            t = k.PCB_TRACK(b); t.SetStart(start); t.SetEnd(end)
            t.SetLayer(k.B_Cu if j % 2 else k.F_Cu); t.SetWidth(round(width*1e6)); t.SetNet(n); b.Add(t)
    w, h = outline or (0, 0)
    corners = [(-2, -2), (w-2, -2), (w-2, h-2), (-2, h-2)]
    edge_pairs = list(zip(corners, corners[1:] + corners[:1])) if outline else []
    for start, end in edge_pairs[:-1] if open_outline else edge_pairs:
        edge = k.PCB_SHAPE(b); edge.SetShape(k.SHAPE_T_SEGMENT); edge.SetLayer(k.Edge_Cuts)
        edge.SetStart(k.VECTOR2I(round(start[0]*1e6), round(start[1]*1e6)))
        edge.SetEnd(k.VECTOR2I(round(end[0]*1e6), round(end[1]*1e6))); edge.SetWidth(50000); b.Add(edge)
    k.SaveBoard(str(path), b)


def make_scope(root, document, *, version=3, **board_options):
    """Owned empty schematic and routing-only obligations; no parity claim."""
    from agent_ee.storage import write_json
    make_board(root / 'test.kicad_pcb', **board_options)
    (root / 'test.kicad_sch').write_text(
        '(kicad_sch (version 20250114) (generator "eeschema") '
        '(uuid "d59616d7-b33b-41fe-bc77-f86de4d10183") (paper "A4") (lib_symbols))')
    (root / 'requirements.txt').write_text('Owned routing acceptance bounds; only declared nets and limits.')
    write_json(root / 'rules.json', document)
    scope = {'schema': f'agent-ee.scope/{version}', 'name': 'Owned routing fixture',
        'scope': 'Explicit routing constraints only', 'schematic': 'test.kicad_sch',
        'board': 'test.kicad_pcb', 'requirements': ['requirements.txt'],
        'inputs': [{'path': p, 'at': p} for p in
                   ('test.kicad_sch', 'test.kicad_pcb', 'rules.json', 'requirements.txt')],
        'checks': [{'id': 'routing', 'kind': 'routing_rules', 'params': {'routing_rules': 'rules.json'}}],
        'baseline': {'requirements': [{'id': rid, 'statement': 'Meet explicit routing constraints.',
            'checks': ['routing'], 'source': {'path': 'requirements.txt', 'locator': rid},
            **({'intent': 'acceptance'} if version == 3 else {})}
            for rid in sorted({c['requirement'] for c in document['constraints']})],
            'targets': ['requirements.txt'], 'reference_architectures': ['requirements.txt']}}
    write_json(root / 'scope.json', scope)
    return scope


def manifest_for_rules(inputs, files, spec, requirements):
    from agent_ee.storage import identity
    requirements = [{**r, 'statement': 'Owned synthetic acceptance requirement.'} for r in requirements]
    scope = {'schema': 'agent-ee.scope/2', 'board': 'test.kicad_pcb', 'checks': [spec],
             'requirements': sorted(p for p in files if p != 'test.kicad_pcb'),
             'baseline': {'requirements': requirements, 'targets': ['requirements.txt'],
                          'reference_architectures': ['requirements.txt']}}
    return {'candidate_id': identity({'scope': scope, 'files': files}), 'scope': scope, 'files': files}


def assert_sealed_native(test, ctx):
    """Exercise production seal/witness logic on actual native output."""
    from agent_ee import baseline, candidate
    from agent_ee.storage import write_json
    write_json(ctx.run_dir / 'candidate.json', ctx.manifest)
    write_json(ctx.run_dir / 'baseline.json', baseline.compile_baseline(
        ctx.scope, ctx.manifest['files'], ctx.inputs))
    write_json(ctx.run_dir / 'checks' / f'{ctx.cid}.json', ctx.result)
    candidate.verify_snapshot(ctx.run_dir)
    baseline.freeze_evidence(ctx.run_dir, [ctx.result])
    baseline.verify_evidence(ctx.run_dir, [ctx.result], required=True)
    if ctx.result['execution'] == 'ok' and ctx.result['outcome'] == 'defect':
        rid = next(c['requirement'] for c in ctx.result['details']['constraints'] if c['status'] == 'violation')
        baseline.validate_witnesses(ctx.run_dir, [{'requirement_id': rid, 'check_id': ctx.cid}],
                                    [{'path': f'checks/{ctx.cid}.json'}])
    evaluation = ctx.work / 'rules-evaluation.json'
    original = evaluation.read_bytes()
    evaluation.write_bytes(original + b'\n')
    with test.assertRaisesRegex(ValueError, 'seal'):
        baseline.verify_evidence(ctx.run_dir, [ctx.result], required=True)
    evaluation.write_bytes(original)
    baseline.verify_evidence(ctx.run_dir, [ctx.result], required=True)

    contract_path = ctx.inputs / ctx.result['details']['source']['path']
    original_contract = contract_path.read_bytes()
    contract_path.write_bytes(original_contract + b'\n')
    with test.assertRaisesRegex(ValueError, 'snapshot'):
        candidate.verify_snapshot(ctx.run_dir)
    contract_path.write_bytes(original_contract)
    candidate.verify_snapshot(ctx.run_dir)
