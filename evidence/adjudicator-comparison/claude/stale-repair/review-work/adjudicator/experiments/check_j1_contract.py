import re, sys
pcb = open(sys.argv[1]).read()
# Find J1 footprint block pads
j1 = pcb[pcb.index('Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical'):]
pads = re.findall(r'\(pad "(\d)" thru_hole.*?\(net "([^"]+)"\)', j1, re.S)
print("J1 pads (board):", pads)
req = {"2": "input", "1": "filtered output", "3": "ground"}
print("Declared contract:", req)
