"""Convert native XML net names at the board-writing boundary.

KiCad 10.0.7 XML unescapes literal slashes; PCB/native S-expression names
retain ``{slash}``. Sheet path separators are structural and stay literal.
The XML inventory and schematic label text must remain in their own domains.
"""


def from_xml(root):
    """Return raw XML name -> PCB name, refusing colliding encoded names.

    Declared sheet names include both the leading and trailing separator.
    Local names with additional slashes are ambiguous in XML (a sheet path
    and a literal slash export identically), so refuse them. Global and
    generated names have no leading separator. Braces and spaces are
    already native text, including overbar markup, and must not be escaped.
    """
    paths = {'/'}
    paths.update(s.get('name') for s in root.findall('./design/sheet') if s.get('name'))
    paths.update(s.get('names') for s in root.findall('./components/comp/sheetpath') if s.get('names'))
    if any(not p.startswith('/') or not p.endswith('/') for p in paths):
        raise ValueError('invalid native sheet name path for board net encoding')
    result, owners = {}, {}
    for net in root.findall('./nets/net'):
        name = net.get('name')
        if not name:
            raise ValueError('missing native net name for board net encoding')
        encoded = encode_net(name, paths)
        if encoded in owners and owners[encoded] != name:
            raise ValueError('native net names collide after board encoding: ' + repr(name))
        owners[encoded] = name
        result[name] = encoded
    return result


def encode_net(name, paths=None):
    """Return the PCB name of one raw XML net name: the rule `from_xml` applies.

    `paths` are the declared sheet name paths. A name without a leading
    separator has every slash encoded. A local name keeps its sheet prefix;
    with `paths` an extra slash after the most specific prefix is refused.
    Without `paths` (a consumer holding only one declared XML name) the
    structure is unknown, so a local name is taken verbatim: an unambiguous
    one has no literal slash, and an ambiguous one cannot match the board
    form KiCad writes, so the comparison still fails closed.
    """
    if paths is None:
        return name if name.startswith('/') else name.replace('/', '{slash}')
    prefix = max((p for p in paths if name.startswith(p)), key=len, default='')
    suffix = name[len(prefix):]
    if prefix and '/' in suffix:
        raise ValueError('ambiguous local net slash in XML; escaped native netlist required: ' + repr(name))
    return prefix + suffix.replace('/', '{slash}')
