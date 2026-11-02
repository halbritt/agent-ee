"""JSON boundary shape checks, before semantic design validation or I/O."""


def problems(design):
    errors = []

    def expect(value, kind, path):
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            errors.append(f"{path}: supply a {kind.__name__}")
            return False
        return True

    def obj(value, required, optional, path):
        if not expect(value, dict, path):
            return False
        for key in sorted(set(required) - value.keys()):
            errors.append(f"{path}.{key}: supply this required field")
        for key in sorted(value.keys() - set(required) - set(optional)):
            errors.append(
                f"{path}.{key}: unknown field; remove it or use the documented format"
            )
        for key, kind in {**required, **optional}.items():
            if key in value and kind is not None:
                expect(value[key], kind, path + "." + key)
        return not (set(required) - value.keys())

    def rows(value, path):
        if not expect(value, list, path):
            return []
        return list(enumerate(value))

    required = dict(
        schema=str,
        name=str,
        requirement_prefix=str,
        intent=str,
        board=dict,
        parts=dict,
        nets=dict,
        no_connects=list,
        power_flags=list,
        ground=str,
        connectors=list,
        decoupling=list,
        groups=list,
        plane=dict,
        functional=list,
        bench=list,
    )
    if not obj(design, required, dict(placement=dict, mechanics=dict), "design"):
        return errors
    if errors:
        return errors
    obj(
        design["board"],
        dict(
            width_mm=None,
            height_mm=None,
            thickness_mm=None,
            copper_layers=None,
            max_component_height_mm=None,
        ),
        {},
        "board",
    )
    for ref, part in design["parts"].items():
        path = "parts." + ref
        if (
            obj(
                part,
                dict(lcsc=str, value=str, lib_id=str, footprint=str, group=str),
                dict(connector=None),
                path,
            )
            and part.get("connector") is not None
        ):
            obj(part["connector"], dict(rows=int, columns=int), {}, path + ".connector")
    for net, terms in design["nets"].items():
        for i, term in rows(terms, "nets." + net):
            expect(term, str, f"nets.{net}[{i}]")
    for key in ("no_connects", "power_flags", "bench"):
        for i, value in rows(design[key], key):
            expect(value, str, f"{key}[{i}]")
    for i, g in rows(design["groups"], "groups"):
        obj(g, dict(name=str, anchor=str, title=str), {}, f"groups[{i}]")
    obj(design["plane"], dict(net=str, layer=str), {}, "plane")
    for i, c in rows(design["connectors"], "connectors"):
        obj(
            c,
            dict(
                reference=str,
                edge=str,
                position_mm=None,
                inset_mm=None,
                rotation_deg=None,
                insertion=str,
            ),
            dict(local_mating_axis=str),
            f"connectors[{i}]",
        )
    for i, d in rows(design["decoupling"], "decoupling"):
        path = f"decoupling[{i}]"
        if obj(
            d,
            dict(
                capacitor=str,
                device=str,
                supply=list,
                **{"return": list},
                max_distance_mm=None,
            ),
            {},
            path,
        ):
            for key in ("supply", "return"):
                for j, value in rows(d[key], path + "." + key):
                    expect(value, str, f"{path}.{key}[{j}]")
    for i, f in rows(design["functional"], "functional"):
        path = f"functional[{i}]"
        if obj(f, dict(text=str, witnesses=list), {}, path):
            for j, w in rows(f["witnesses"], path + ".witnesses"):
                wp = f"{path}.witnesses[{j}]"
                if not expect(w, dict, wp):
                    continue
                if w.get("type") == "pin_net":
                    obj(w, dict(id=str, type=str, terminal=str, net=str), {}, wp)
                elif w.get("type") == "element_between":
                    if obj(
                        w,
                        dict(id=str, type=str, **{"class": str}, nets=list, value=str),
                        dict(lib_id=str, pins=list),
                        wp,
                    ):
                        for k in ("nets", "pins"):
                            if k in w:
                                for n, v in rows(w[k], wp + "." + k):
                                    expect(v, str, f"{wp}.{k}[{n}]")
                else:
                    errors.append(wp + ".type: use pin_net or element_between")
    if "placement" in design:
        obj(
            design["placement"],
            {},
            dict(budget_s=int, seeds=int, steps_per_seed=int),
            "placement",
        )
    if "mechanics" in design:
        m = design["mechanics"]
        if obj(m, dict(access_owners=list), dict(packages=dict), "mechanics"):
            for i, v in rows(m["access_owners"], "mechanics.access_owners"):
                expect(v, str, f"mechanics.access_owners[{i}]")
            if isinstance(m.get("packages", {}), dict):
                for ref, p in m.get("packages", {}).items():
                    obj(
                        p,
                        dict(size_mm=list, offset_mm=list),
                        {},
                        "mechanics.packages." + ref,
                    )
    return errors
