import re, json, sys
pcb = open('/candidate/project/rc_filter.kicad_pcb').read()
# Extract J1 footprint block pad nets
j1 = pcb[pcb.index('Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical'):]
pads = re.findall(r'\(pad "(\d)" thru_hole.*?\(net "([^"]+)"\)', j1, re.S)
print("J1 pad nets (saved PCB):", pads)
# Reconstruct schematic net membership from exported netlist evidence
xml = open('/evidence/work/netlist/design.xml').read()
nets = re.findall(r'<net code="\d+" name="([^"]+)"[^>]*>(.*?)</net>', xml, re.S)
for name, body in nets:
    nodes = re.findall(r'ref="(\w+)" pin="(\d)"', body)
    print(name, nodes)
