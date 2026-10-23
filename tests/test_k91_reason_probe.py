"""Round-3 refusal-reason probe, committed as a regression test (AGENTEE-442, third-HOLD root redesign).

Written by the independent Sol reviewer of round 3 (reviews/ee442-native-positive-b-sol-r3.md, H4;
probe round3/refusal-reasons/reason_probe.py, sha256
a78462fea3cf483dcc8383ef930b67e058f4e58f354d0421c15051530db1ba28). It drives the actual production
consumer for the untouched replay, the four intended controls (asserting each reason, the layers
attempted and the origin frame) and three replay plumbing failures. Adapted only as needed to run
as a test; every change is marked ADAPTED: checkout-relative paths, observations written to the
temporary directory, consumer_state given the slot name (revision 4 judges by contract), and the
two assertions that encoded the H4 defect now assert the corrected outcome (INCOMPLETE, and
controls_refused False). The probe body is otherwise the reviewer's.
"""
import copy, json, sys, tempfile, traceback, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))  # ADAPTED: checkout-relative, not cwd-relative
import k91_consumer as k, k91_classify as c
from agent_ee import campaign, routing_adapter, stage_receipts
from tests.kicad_route_fixture import FAKE_PIN, make_candidate, make_checkout
from tests.test_routing_experiment_boundary import CURRENT
observations={}; stacks=[]
original=campaign.run_implementation_steps
def witness(*args,**kwargs):
    try:
        result=original(*args,**kwargs)
    except Exception as exc:
        contexts=[]
        context=exc.__context__
        while context is not None:
            contexts.append({'error':f'{type(context).__name__}: {context}', 'traceback':[f'{Path(f.filename).name}:{f.name}:{f.lineno}' for f in traceback.extract_tb(context.__traceback__)]})
            context=context.__context__
        stacks.append({'accepted':False,'error':f'{type(exc).__name__}: {exc}', 'contexts':contexts,
                       'traceback':[f'{Path(f.filename).name}:{f.name}:{f.lineno}' for f in traceback.extract_tb(exc.__traceback__)]})
        raise
    stacks.append({'accepted':True})
    return result


class ReasonProbeTest(unittest.TestCase):
    def test_reviewer_reason_probe(self):
        global observations, stacks
        with tempfile.TemporaryDirectory(prefix='review442-reasons-') as temp:
            root=Path(temp); checkout,code=make_checkout(root/'checkout',{'name':'complete'})
            mismatch,_=make_checkout(root/'mismatch',{'name':'complete'})
            with (mismatch/'src/kicad_route/__init__.py').open('a') as f:f.write('# different runtime\n')
            with k.pinned(root,FAKE_PIN,code), patch.object(campaign,'run_implementation_steps',witness):
                decl=k.declaration(checkout,project='project',board='rc.kicad_pcb',schematic='rc.kicad_sch',name='rc',budget=5,timeout=30,policy=CURRENT)
                bind=k.binding(decl); work=root/'positive/1'; work.mkdir(parents=True)
                positive=k.consume(work,make_candidate(root/'positive/design'),bind)
                assert positive['consumer']['accepted'] and positive['consumer_verify']['accepted'] and positive['consumer_adopt']['accepted'],positive
                kept=k.tree_sha256(work)
                # The untouched retained receipt must pass the exact same replay plumbing and real verifier/adopter.
                with k.disposable_receipt(work):
                    untouched=k.consume(work,make_candidate(root/'untouched/design'),bind,replay=True,direct_design=make_candidate(root/'untouched/direct'))
                assert untouched['consumer']['accepted'] and untouched['consumer_run_step']['accepted'] and untouched['consumer_verify']['accepted'] and untouched['consumer_adopt']['accepted'],untouched
                print('UNTOUCHED replay: run_step accepted; real verify accepted; real adopt accepted; consumer returned')
                start=len(stacks)
                controls=k.run_controls(root/'controls',work,decl,bind,mismatch,make_candidate,'project/rc.kicad_pcb')
                actual_stacks=stacks[start:]
                assert len(actual_stacks)==4,actual_stacks
                expected={
                    'a_mismatched_runtime':('kicad-route does not qualify: tool file differs from pin', [False,False,False], 'validate_campaign_step'),
                    'b_tampered_candidate':('stage receipt outputs changed',[True,True,False], 'validate_receipt'),
                    'b_tampered_sidecar':('retained routing artifacts differ from the adapter report',[True,True,False], '_verify'),
                    'c_stale_input':('routing receipt chain input differs from preceding adopted output',[True,False,False], 'run_implementation_steps')}
                for (name,rec),stack in zip(controls.items(),actual_stacks):
                    reason,attempted,origin=expected[name]
                    assert reason in rec['consumer']['error'],(name,rec)
                    assert [rec[key]['attempted'] for key in ('consumer_run_step','consumer_verify','consumer_adopt')]==attempted,(name,rec)
                    assert any(':'+origin+':' in frame for frame in stack['traceback']),(name,stack)
                    assert rec['design_before']==rec['design_after']
                    if name.startswith('b_'):
                        assert rec['consumer_run_step']['accepted'] is True
                        assert rec['consumer_verify']['accepted'] is False
                    assert not any('k91_consumer.py:run_step:' in frame for frame in stack['traceback']),stack
                    observations[name]={'record':rec,'exception':stack,'reason_confirmed':True}
                    print('INTENDED',name,':',rec['consumer']['error'],'origin=',stack['traceback'][-1],'unchanged=True')
                assert controls['b_tampered_sidecar']['direct_adopt']['accepted'] is True
                verdict=c.v2_verdict({'controls':controls}); assert verdict['controls_refused']
                # Challenge the source concern with REAL replay failures, never stub verification/adoption.
                attacks={}
                for mode in ('binding-mismatch','invalid-json','missing-receipt'):
                    with k.disposable_receipt(work):
                        path=work/k.STEP_DIR/'receipt.json'
                        if mode=='binding-mismatch':
                            doc=json.loads(path.read_text()); doc['binding']['source_candidate_id']='unrelated replay input'
                            path.write_text(json.dumps(doc))
                        elif mode=='invalid-json':path.write_text('{not JSON')
                        else:path.unlink()
                        rec=k.consume(work,make_candidate(root/mode/'design'),bind,replay=True,direct_design=make_candidate(root/mode/'direct'))
                    stack=stacks[-1]
                    assert rec['consumer_run_step']['attempted'] and rec['consumer_run_step']['accepted'] is False,rec
                    assert rec['consumer_verify']['attempted'] is False and rec['consumer_adopt']['attempted'] is False,rec
                    assert rec['design_before']==rec['design_after']
                    assert any('k91_consumer.py:run_step:' in frame for frame in stack['traceback'] + [f for context in stack.get('contexts',[]) for f in context['traceback']]),stack
                    reported=c.consumer_state(rec,'b_tampered_candidate')  # ADAPTED: revision 4 judges by the slot's contract
                    assert reported==c.INCOMPLETE,(mode,reported)  # ADAPTED: was ==c.REFUSED (the H4 defect); fixed expectation
                    altered=copy.deepcopy(controls); altered['b_tampered_candidate']=rec
                    aggregated=c.v2_verdict({'controls':altered})['controls_refused']
                    assert aggregated is False  # ADAPTED: was `is True` (the H4 defect); fixed expectation
                    attacks[mode]={'record':rec,'exception':stack,'classifier_state':reported,'controls_refused':aggregated,'reviewer_state':'incomplete'}
                    print('PLUMBING',mode,': replay_error=',rec['consumer_run_step']['error'],'final_error=',rec['consumer']['error'],'verify/adopt skipped; classifier=',reported,'controls_refused=',aggregated,'reviewer=incomplete')
                assert k.tree_sha256(work)==kept
                observations.update(untouched=untouched,plumbing_attacks=attacks)
                (root/'observations.json').write_text(json.dumps(observations,indent=2,default=str)+'\n')  # ADAPTED: temp, not build/
        print('Intended controls establish expected reasons; unrelated plumbing failures are INCOMPLETE')  # ADAPTED message


if __name__ == '__main__':
    unittest.main()
