"""Declared-edge identity for rectangular native boards.

Corner selection and seat semantics adapted after inspection of KiCadRoutingTools
222bb144744c3ddd979674e528bf8647e9ced8d6, placement/floorplan.py.
No mating-face inference is made; the job preserves connector rotation.

Copyright (c) 2026 drandyhaas

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def setbacks(rect, outline):
    return {'west': rect[0] - outline[0], 'north': rect[1] - outline[1],
            'east': outline[2] - rect[2], 'south': outline[3] - rect[3]}


def nearest_edge(rect, outline, margin):
    distances = setbacks(rect, outline)
    reached = [edge for edge, distance in distances.items() if distance <= margin]
    if len(reached) > 1:
        outside = [edge for edge in reached if distances[edge] < -margin]
        if len(outside) == 1:
            return outside[0]
        span = {'west': rect[3] - rect[1], 'east': rect[3] - rect[1],
                'north': rect[2] - rect[0], 'south': rect[2] - rect[0]}
        longest = max(span[edge] for edge in reached)
        return min((edge for edge in reached if span[edge] == longest), key=distances.get)
    return min(distances, key=distances.get)
