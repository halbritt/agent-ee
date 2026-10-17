"""Reproduce resistor-branch arithmetic from supplied fresh native connectivity."""
import json
n=json.load(open('/evidence/work/netlist/connectivity.json'))
nets=n['nets']; pin_nets={p:k for k,ps in nets.items() for p in ps}
branches=[]
for ref,c in n['components'].items():
 if ref.startswith('R') and {pin_nets.get(ref+'.1'),pin_nets.get(ref+'.2')}=={'/CAN2+','/CAN2-'}:
  branches.append((ref,c['value'],c['dnp']))
print('Direct branches (reference, ohms, DNP):',branches)
print('Populated conductance, S:',sum(0 if dnp else 1/float(v) for _,v,dnp in branches))
print('Both 120-ohm branches hypothetically fitted, ohms:',1/(1/120+1/120))
print('LS9/10 high-impedance driver, no receiver input current: V =',5*1000/(1000+1000),'Rth =',1/(1/1000+1/1000))
