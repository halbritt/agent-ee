# Independent copper-path verification using raw coordinates from the saved board.
# Pad copper modeled as rectangles/circles; tracks as segments with width.
# Goal: prove fitted R1 (dnp=false) bridges /IN (J1.1) to /OUT (J1.2) electrically.
import math

def seg_touches_rect(p, rc, rs):
    # point p on/inside axis-aligned rect centered rc size rs (ignores roundrect corners; endpoints here hit pad centers so fine)
    return abs(p[0]-rc[0]) <= rs[0]/2 + 1e-9 and abs(p[1]-rc[1]) <= rs[1]/2 + 1e-9

pads = {
 'J1.1': ((100,100),(1.7,1.7),'/IN'),
 'J1.2': ((100,102.54),(1.7,1.7),'/OUT'),
 'J1.3': ((100,105.08),(1.7,1.7),'GND'),
 'R1.1': ((104.0875,100),(1.025,1.4),'/IN'),
 'R1.2': ((105.9125,100),(1.025,1.4),'/OUT'),
 'C1.1': ((109.05,100),(1.0,1.45),'/OUT'),
 'C1.2': ((110.95,100),(1.0,1.45),'GND'),
}
tracks = [  # (start, end, net)
 ((100,100),(104.0875,100),'/IN'),
 ((105.9125,100),(109.05,100),'/OUT'),
 ((100,102.54),(107.5,102.54),'/OUT'),
 ((107.5,102.54),(107.5,100),'/OUT'),
 ((110.95,100),(113,100),'GND'),
]
# /IN: track1 endpoints vs J1.1 and R1.1
t = tracks[0]
print('IN  track start in J1.1 pad:', seg_touches_rect(t[0], *pads['J1.1'][:2]))
print('IN  track end   in R1.1 pad:', seg_touches_rect(t[1], *pads['R1.1'][:2]))
# /OUT: track2 endpoints vs R1.2 and C1.1
t = tracks[1]
print('OUT track start in R1.2 pad:', seg_touches_rect(t[0], *pads['R1.2'][:2]))
print('OUT track end   in C1.1 pad:', seg_touches_rect(t[1], *pads['C1.1'][:2]))
# J1.2 branch: track3 start in J1.2; track4 end (107.5,100) lies ON track2 (y=100, 105.9125<=107.5<=109.05)
t3, t4 = tracks[2], tracks[3]
print('OUT track3 start in J1.2 pad:', seg_touches_rect(t3[0], *pads['J1.2'][:2]))
print('OUT track3 end == track4 start:', t3[1]==t4[0])
x,y = t4[1]
on_t2 = (abs(y-100)<=0.25/2) and (105.9125-0.125 <= x <= 109.05+0.125)
print('OUT track4 end overlaps track2 copper (T-junction):', on_t2)
# GND F.Cu: track5 start in C1.2, end at via (113,100)
t5 = tracks[4]
print('GND track5 start in C1.2 pad:', seg_touches_rect(t5[0], *pads['C1.2'][:2]))
print('GND track5 end at via (113,100):', t5[1]==(113,100))
# Fitted resistive element: R1 dnp=false, value 10k, pads on /IN and /OUT
print('R1 fitted 10k bridges nets:', pads['R1.1'][2], '->', pads['R1.2'][2])
