use crate::exact::{Int, Point, Rat, integer, point, point_value};
use anyhow::{Context, Result, bail};
use num_traits::{Signed, ToPrimitive, Zero};
use serde_json::Value;
use std::cmp::Ordering;

pub type Polygon = Vec<Point>;

#[derive(Clone, Debug, Eq, PartialEq, Ord, PartialOrd)]
pub struct Bounds {
    pub x0: Int,
    pub y0: Int,
    pub x1: Int,
    pub y1: Int,
}

impl Bounds {
    pub fn translated(&self, p: &Point) -> Self {
        Self {
            x0: &self.x0 + &p.x,
            y0: &self.y0 + &p.y,
            x1: &self.x1 + &p.x,
            y1: &self.y1 + &p.y,
        }
    }
    pub fn grown(&self, by: &Int) -> Self {
        Self {
            x0: &self.x0 - by,
            y0: &self.y0 - by,
            x1: &self.x1 + by,
            y1: &self.y1 + by,
        }
    }
    pub fn meets(&self, b: &Self) -> bool {
        self.x0 < b.x1 && b.x0 < self.x1 && self.y0 < b.y1 && b.y0 < self.y1
    }
    /// Quarter turns map opposite corners to opposite corners. A prepared
    /// contour's bounds therefore move without revisiting its vertices.
    pub fn at(&self, origin: &Point, position: &Point, turns: i64) -> Self {
        if turns.rem_euclid(4) == 0 {
            return self.translated(&position.minus(origin));
        }
        let a = crate::exact::rotate(
            &Point {
                x: &self.x0 - &origin.x,
                y: &self.y0 - &origin.y,
            },
            turns,
        )
        .plus(position);
        let b = crate::exact::rotate(
            &Point {
                x: &self.x1 - &origin.x,
                y: &self.y1 - &origin.y,
            },
            turns,
        )
        .plus(position);
        Self {
            x0: a.x.clone().min(b.x.clone()),
            y0: a.y.clone().min(b.y.clone()),
            x1: a.x.max(b.x),
            y1: a.y.max(b.y),
        }
    }
}

/// Bounds of an immutable, qualified contour collection. Both levels matter:
/// a distant footprint rejects once, while disjoint contours of nearby rigid
/// blocks reject without scanning their (often curved) native polygons.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct PolygonBounds {
    pub total: Option<Bounds>,
    pub shapes: Vec<Bounds>,
}
impl PolygonBounds {
    pub fn new(polygons: &[Polygon]) -> Result<Self> {
        let shapes = polygons
            .iter()
            .map(|p| bounds(std::slice::from_ref(p)))
            .collect::<Result<Vec<_>>>()?;
        let total = shapes.first().map(|first| {
            let (mut x0, mut y0, mut x1, mut y1) = (&first.x0, &first.y0, &first.x1, &first.y1);
            for b in &shapes[1..] {
                x0 = x0.min(&b.x0);
                y0 = y0.min(&b.y0);
                x1 = x1.max(&b.x1);
                y1 = y1.max(&b.y1);
            }
            Bounds {
                x0: x0.clone(),
                y0: y0.clone(),
                x1: x1.clone(),
                y1: y1.clone(),
            }
        });
        Ok(Self { total, shapes })
    }
    pub fn at(&self, origin: &Point, position: &Point, turns: i64) -> Self {
        let delta = position.minus(origin);
        let moved = |b: &Bounds| {
            if turns.rem_euclid(4) == 0 {
                b.translated(&delta)
            } else {
                b.at(origin, position, turns)
            }
        };
        Self {
            total: self.total.as_ref().map(&moved),
            shapes: self.shapes.iter().map(moved).collect(),
        }
    }
    /// `left` and `right` are the same contours (in the same order) from which
    /// these bounds were prepared. Bounds can only reject, never admit overlap.
    pub fn overlaps(&self, left: &[Polygon], other: &Self, right: &[Polygon]) -> bool {
        assert_eq!(
            self.shapes.len(),
            left.len(),
            "stale prepared polygon bounds"
        );
        assert_eq!(
            other.shapes.len(),
            right.len(),
            "stale prepared polygon bounds"
        );
        let (Some(a), Some(b)) = (&self.total, &other.total) else {
            return false;
        };
        if !a.meets(b) {
            return false;
        }
        left.iter().zip(&self.shapes).any(|(p, pb)| {
            right
                .iter()
                .zip(&other.shapes)
                .any(|(q, qb)| pb.meets(qb) && positive_overlap_narrow(p, q))
        })
    }
}

fn cross_checked(a: &Point, b: &Point, c: &Point) -> Option<i128> {
    if let (Some(ax), Some(ay), Some(bx), Some(by), Some(cx), Some(cy)) = (
        a.x.to_i128(),
        a.y.to_i128(),
        b.x.to_i128(),
        b.y.to_i128(),
        c.x.to_i128(),
        c.y.to_i128(),
    ) {
        if let Some(v) = bx
            .checked_sub(ax)
            .and_then(|x| cy.checked_sub(ay).and_then(|y| x.checked_mul(y)))
            .and_then(|left| {
                by.checked_sub(ay)
                    .and_then(|y| cx.checked_sub(ax).and_then(|x| y.checked_mul(x)))
                    .and_then(|right| left.checked_sub(right))
            })
        {
            return Some(v);
        }
    }
    None
}
pub fn cross(a: &Point, b: &Point, c: &Point) -> Int {
    cross_checked(a, b, c)
        .map(Int::from)
        .unwrap_or_else(|| (&b.x - &a.x) * (&c.y - &a.y) - (&b.y - &a.y) * (&c.x - &a.x))
}
/// Predicate callers need only the determinant sign. Keep representable
/// products on the stack; overflowing arithmetic takes the exact BigInt path.
pub fn cross_sign(a: &Point, b: &Point, c: &Point) -> Ordering {
    cross_checked(a, b, c)
        .map(|v| v.cmp(&0))
        .unwrap_or_else(|| cross(a, b, c).cmp(&Int::zero()))
}
fn in_segment_bounds(a: &Point, b: &Point, p: &Point) -> bool {
    (&a.x).min(&b.x) <= &p.x
        && &p.x <= (&a.x).max(&b.x)
        && (&a.y).min(&b.y) <= &p.y
        && &p.y <= (&a.y).max(&b.y)
}
fn segment_boxes_meet(a: &Point, b: &Point, c: &Point, d: &Point) -> bool {
    (&a.x).min(&b.x) <= (&c.x).max(&d.x)
        && (&c.x).min(&d.x) <= (&a.x).max(&b.x)
        && (&a.y).min(&b.y) <= (&c.y).max(&d.y)
        && (&c.y).min(&d.y) <= (&a.y).max(&b.y)
}

pub fn edges(p: &Polygon) -> impl Iterator<Item = (&Point, &Point)> {
    p.iter().zip(p.iter().cycle().skip(1)).take(p.len())
}
pub fn area2(p: &Polygon) -> Int {
    edges(p).map(|(a, b)| &a.x * &b.y - &b.x * &a.y).sum()
}
pub fn area(p: &Polygon) -> Rat {
    Rat::new(area2(p).abs(), 2.into())
}
pub fn on_segment(a: &Point, b: &Point, p: &Point) -> bool {
    in_segment_bounds(a, b, p) && cross_sign(a, b, p) == Ordering::Equal
}
/// Closed-polygon membership, including the boundary, without a floating ray cast.
pub fn inside(p: &Point, vertices: &Polygon) -> bool {
    let mut result = false;
    for (a, b) in edges(vertices) {
        if on_segment(a, b, p) {
            return true;
        }
        if (a.y > p.y) != (b.y > p.y) {
            if cross_sign(a, b, p) == b.y.cmp(&a.y) {
                result = !result;
            }
        }
    }
    result
}
pub fn intersects(a: &Point, b: &Point, c: &Point, d: &Point) -> bool {
    if !segment_boxes_meet(a, b, c, d) {
        return false;
    }
    let (u, v, w, z) = (
        cross_sign(a, b, c),
        cross_sign(a, b, d),
        cross_sign(c, d, a),
        cross_sign(c, d, b),
    );
    (u != Ordering::Equal
        && v != Ordering::Equal
        && u != v
        && w != Ordering::Equal
        && z != Ordering::Equal
        && w != z)
        || u == Ordering::Equal && in_segment_bounds(a, b, c)
        || v == Ordering::Equal && in_segment_bounds(a, b, d)
        || w == Ordering::Equal && in_segment_bounds(c, d, a)
        || z == Ordering::Equal && in_segment_bounds(c, d, b)
}

pub fn simple_polygon(raw: &Value) -> Result<Polygon> {
    let raw = raw.as_array().context("polygon needs 3..512 vertices")?;
    if !(3..=512).contains(&raw.len()) {
        bail!("polygon needs 3..512 vertices");
    }
    let mut p: Polygon = raw
        .iter()
        .map(point)
        .collect::<Result<_>>()
        .map_err(|_| anyhow::anyhow!("polygon coordinates must be integer nanometres"))?;
    if p.first() == p.last() {
        p.pop();
    }
    let unique: std::collections::BTreeSet<_> = p.iter().collect();
    if unique.len() != p.len() {
        bail!("repeated polygon vertex");
    }
    loop {
        let mut changed = false;
        if p.len() < 3 {
            break;
        }
        for i in 0..p.len() {
            let a = &p[(i + p.len() - 1) % p.len()];
            let b = &p[(i + 1) % p.len()];
            if cross_sign(a, &p[i], b) == Ordering::Equal {
                if !on_segment(a, b, &p[i]) {
                    bail!("polygon edge reverses");
                }
                p.remove(i);
                changed = true;
                break;
            }
        }
        if !changed {
            break;
        }
    }
    if p.len() < 3 || area2(&p).is_zero() {
        bail!("zero polygon area");
    }
    let n = p.len();
    let mut spans = edges(&p)
        .enumerate()
        .map(|(i, (a, b))| {
            (
                (&a.x).min(&b.x),
                (&a.x).max(&b.x),
                (&a.y).min(&b.y),
                (&a.y).max(&b.y),
                i,
            )
        })
        .collect::<Vec<_>>();
    spans.sort();
    for (k, &(_, x1, y0, y1, i)) in spans.iter().enumerate() {
        for &(u0, _, v0, v1, j) in &spans[k + 1..] {
            if u0 > x1 {
                break;
            }
            if i.abs_diff(j) == 1 || i.min(j) == 0 && i.max(j) == n - 1 || v0 > y1 || v1 < y0 {
                continue;
            }
            if intersects(&p[i], &p[(i + 1) % n], &p[j], &p[(j + 1) % n]) {
                bail!("polygon self-intersection");
            }
        }
    }
    if area2(&p).is_negative() {
        p.reverse();
    }
    Ok(p)
}

pub fn convex(p: &Polygon) -> bool {
    p.len() >= 3
        && (0..p.len()).all(|i| {
            cross_sign(
                &p[(i + p.len() - 2) % p.len()],
                &p[(i + p.len() - 1) % p.len()],
                &p[i],
            ) == Ordering::Greater
        })
}
pub fn triangles(p: &Polygon) -> Vec<Polygon> {
    if convex(p) {
        return (1..p.len() - 1)
            .map(|i| vec![p[0].clone(), p[i].clone(), p[i + 1].clone()])
            .collect();
    }
    let mut remaining = p.clone();
    let mut out = Vec::new();
    while remaining.len() > 3 {
        let n = remaining.len();
        let ear = (0..n)
            .find(|&i| {
                let (a, b, c) = (
                    &remaining[(i + n - 1) % n],
                    &remaining[i],
                    &remaining[(i + 1) % n],
                );
                cross_sign(a, b, c) == Ordering::Greater
                    && !remaining.iter().any(|p| {
                        p != a
                            && p != b
                            && p != c
                            && cross_sign(a, b, p) != Ordering::Less
                            && cross_sign(b, c, p) != Ordering::Less
                            && cross_sign(c, a, p) != Ordering::Less
                    })
            })
            .expect("qualified simple polygon has an ear");
        out.push(vec![
            remaining[(ear + n - 1) % n].clone(),
            remaining[ear].clone(),
            remaining[(ear + 1) % n].clone(),
        ]);
        remaining.remove(ear);
    }
    out.push(remaining);
    out
}

type RationalPoint = (Rat, Rat);
fn qcross(a: &Point, b: &Point, p: &RationalPoint) -> Rat {
    Rat::from_integer(&b.x - &a.x) * (&p.1 - Rat::from_integer(a.y.clone()))
        - Rat::from_integer(&b.y - &a.y) * (&p.0 - Rat::from_integer(a.x.clone()))
}
pub fn clipped_area(subject: &Polygon, clip: &Polygon) -> Rat {
    let mut output: Vec<RationalPoint> = subject
        .iter()
        .map(|p| {
            (
                Rat::from_integer(p.x.clone()),
                Rat::from_integer(p.y.clone()),
            )
        })
        .collect();
    for (a, b) in edges(clip) {
        if output.is_empty() {
            return Rat::zero();
        }
        let incoming = output;
        output = Vec::new();
        for i in 0..incoming.len() {
            let p = &incoming[i];
            let q = &incoming[(i + 1) % incoming.len()];
            let cp = qcross(a, b, p);
            let cq = qcross(a, b, q);
            if cp >= Rat::zero() {
                output.push(p.clone());
            }
            if cp.is_negative() != cq.is_negative() {
                let t = &cp / (&cp - &cq);
                output.push((&p.0 + &t * (&q.0 - &p.0), &p.1 + &t * (&q.1 - &p.1)));
            }
        }
    }
    if output.is_empty() {
        return Rat::zero();
    }
    let twice: Rat = (0..output.len())
        .map(|i| {
            let (a, b) = (&output[i], &output[(i + 1) % output.len()]);
            &a.0 * &b.1 - &b.0 * &a.1
        })
        .sum();
    twice.abs() / Rat::from_integer(2.into())
}
pub fn intersection_area(a: &Polygon, b: &Polygon) -> Rat {
    if a.is_empty() || b.is_empty() {
        return Rat::zero();
    }
    if !polygon_boxes_meet(a, b) {
        return Rat::zero();
    }
    let (convex_a, convex_b) = (convex(a), convex(b));
    // For a convex container, exact half-planes prove closed containment
    // without allocating rational clipping vertices.
    if convex_a && convex_contains(a, b) {
        return area(b);
    }
    if convex_b && convex_contains(b, a) {
        return area(a);
    }
    if convex_a && convex_b {
        return clipped_area(a, b);
    }
    let right = triangles(b);
    let mut sum = Rat::zero();
    for x in triangles(a) {
        for y in &right {
            sum += clipped_area(&x, y);
        }
    }
    sum
}
pub fn positive_overlap(a: &Polygon, b: &Polygon) -> bool {
    if !polygon_boxes_meet(a, b) {
        return false;
    }
    positive_overlap_narrow(a, b)
}
fn positive_overlap_narrow(a: &Polygon, b: &Polygon) -> bool {
    if convex(a) && convex(b) {
        return ![(a, b), (b, a)].into_iter().any(|(own, other)| {
            edges(own).any(|(u, v)| {
                other
                    .iter()
                    .all(|p| cross_sign(u, v, p) != Ordering::Greater)
            })
        });
    }
    intersection_area(a, b) > Rat::zero()
}
pub fn contains(outline: &Polygon, subject: &Polygon) -> bool {
    if convex(outline) {
        return convex_contains(outline, subject);
    }
    intersection_area(outline, subject) == area(subject)
}
fn convex_contains(outline: &Polygon, subject: &Polygon) -> bool {
    edges(outline).all(|(a, b)| {
        subject
            .iter()
            .all(|p| cross_sign(a, b, p) != Ordering::Less)
    })
}

pub fn qualified_polygons(record: &Value) -> Result<Vec<Polygon>> {
    if record.get("status").and_then(Value::as_str) != Some("proven") {
        bail!(
            "{}",
            record
                .get("status")
                .and_then(Value::as_str)
                .unwrap_or("missing")
        );
    }
    let rows = record
        .get("polygons")
        .and_then(Value::as_array)
        .context("missing or excessive polygons")?;
    if rows.is_empty() || rows.len() > 64 {
        bail!("missing or excessive polygons");
    }
    let p: Vec<Polygon> = rows.iter().map(simple_polygon).collect::<Result<_>>()?;
    for i in 0..p.len() {
        for j in i + 1..p.len() {
            if positive_overlap(&p[i], &p[j])
                || edges(&p[i]).any(|(a, b)| edges(&p[j]).any(|(c, d)| intersects(a, b, c, d)))
            {
                bail!("nested or touching polygon contours unsupported");
            }
        }
    }
    Ok(p)
}
pub fn courtyard_bounds(record: &Value) -> Result<(Vec<Polygon>, Vec<Polygon>)> {
    let outer = qualified_polygons(record)?;
    let Some(inner) = record.get("inner_polygons") else {
        return Ok((outer.clone(), outer));
    };
    let tolerance = integer(&record["approximation"]["tolerance_nm"])
        .map_err(|_| anyhow::anyhow!("curve bound without a declared approximation tolerance"))?;
    if tolerance <= Int::zero() || tolerance > 50_000.into() {
        bail!("curve bound without a declared approximation tolerance");
    }
    let inner = qualified_polygons(&serde_json::json!({"status":"proven","polygons":inner}))?;
    if inner.len() != outer.len() {
        bail!("inner and outer curve bounds differ in contours");
    }
    Ok((outer, inner))
}
fn point_bounds<'a>(
    mut points: impl Iterator<Item = &'a Point>,
) -> Option<(&'a Int, &'a Int, &'a Int, &'a Int)> {
    let p = points.next()?;
    let (mut x0, mut y0, mut x1, mut y1) = (&p.x, &p.y, &p.x, &p.y);
    for p in points {
        x0 = x0.min(&p.x);
        y0 = y0.min(&p.y);
        x1 = x1.max(&p.x);
        y1 = y1.max(&p.y);
    }
    Some((x0, y0, x1, y1))
}
fn polygon_boxes_meet(a: &Polygon, b: &Polygon) -> bool {
    let (Some((a0, b0, a1, b1)), Some((c0, d0, c1, d1))) =
        (point_bounds(a.iter()), point_bounds(b.iter()))
    else {
        return false;
    };
    a0 < c1 && c0 < a1 && b0 < d1 && d0 < b1
}
pub fn bounds(polygons: &[Polygon]) -> Result<Bounds> {
    let (x0, y0, x1, y1) =
        point_bounds(polygons.iter().flatten()).context("empty polygon bounds")?;
    Ok(Bounds {
        x0: x0.clone(),
        y0: y0.clone(),
        x1: x1.clone(),
        y1: y1.clone(),
    })
}
pub fn rectangle(b: &Bounds) -> Polygon {
    vec![
        Point {
            x: b.x0.clone(),
            y: b.y0.clone(),
        },
        Point {
            x: b.x1.clone(),
            y: b.y0.clone(),
        },
        Point {
            x: b.x1.clone(),
            y: b.y1.clone(),
        },
        Point {
            x: b.x0.clone(),
            y: b.y1.clone(),
        },
    ]
}
pub fn polygon_value(p: &Polygon) -> Value {
    Value::Array(p.iter().map(point_value).collect())
}
pub fn polygons_value(p: &[Polygon]) -> Value {
    Value::Array(p.iter().map(polygon_value).collect())
}
pub fn point_segment2(p: &Point, a: &Point, b: &Point) -> Rat {
    let dx = &b.x - &a.x;
    let dy = &b.y - &a.y;
    let len = &dx * &dx + &dy * &dy;
    if len.is_zero() {
        return Rat::from_integer(crate::exact::squared_distance(p, a));
    }
    let px = &p.x - &a.x;
    let py = &p.y - &a.y;
    let projection = &px * &dx + &py * &dy;
    if projection <= Int::zero() {
        return Rat::from_integer(&px * &px + &py * &py);
    }
    if projection >= len {
        return Rat::from_integer(crate::exact::squared_distance(p, b));
    }
    // The interior distance is the squared parallelogram height. Keeping
    // projection and determinant integral avoids normalizing intermediate
    // rational coordinates; only the reported distance needs a fraction.
    let determinant = &dx * &py - &dy * &px;
    Rat::new(&determinant * &determinant, len)
}
pub fn segment_distance2(a: &Point, b: &Point, c: &Point, d: &Point) -> Rat {
    if intersects(a, b, c, d) {
        Rat::zero()
    } else {
        [
            point_segment2(a, c, d),
            point_segment2(b, c, d),
            point_segment2(c, a, b),
            point_segment2(d, a, b),
        ]
        .into_iter()
        .min()
        .unwrap()
    }
}
pub fn separation2(a: &Polygon, b: &Polygon) -> Rat {
    if positive_overlap(a, b) {
        return Rat::zero();
    }
    edges(a)
        .flat_map(|(u, v)| edges(b).map(move |(w, z)| segment_distance2(u, v, w, z)))
        .min()
        .unwrap_or_else(Rat::zero)
}
pub fn slabs(p: &Polygon) -> Option<Vec<Bounds>> {
    if edges(p).any(|(a, b)| a.x != b.x && a.y != b.y) {
        return None;
    }
    let xs: Vec<Int> = p
        .iter()
        .map(|p| p.x.clone())
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect();
    let mut out = Vec::new();
    for span in xs.windows(2) {
        let mut ys: Vec<Int> = edges(p)
            .filter(|(a, b)| {
                a.y == b.y
                    && a.x.clone().min(b.x.clone()) <= span[0]
                    && span[1] <= a.x.clone().max(b.x.clone())
            })
            .map(|(a, _)| a.y.clone())
            .collect();
        ys.sort();
        for pair in ys.chunks_exact(2) {
            out.push(Bounds {
                x0: span[0].clone(),
                y0: pair[0].clone(),
                x1: span[1].clone(),
                y1: pair[1].clone(),
            });
        }
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn segment_distance_matches_rational_projection_at_endpoints_and_large_coordinates() {
        fn projected(p: &Point, a: &Point, b: &Point) -> Rat {
            let dx = &b.x - &a.x;
            let dy = &b.y - &a.y;
            let len = &dx * &dx + &dy * &dy;
            if len.is_zero() {
                return Rat::from_integer(crate::exact::squared_distance(p, a));
            }
            let t = Rat::new((&p.x - &a.x) * &dx + (&p.y - &a.y) * &dy, len)
                .max(Rat::zero())
                .min(Rat::from_integer(1.into()));
            let x = Rat::from_integer(&a.x - &p.x) + &t * Rat::from_integer(dx);
            let y = Rat::from_integer(&a.y - &p.y) + t * Rat::from_integer(dy);
            &x * &x + &y * &y
        }
        let segments = [
            (0, 0, 0, 0),
            (0, 0, 4, 0),
            (0, 0, 0, 4),
            (0, 0, 4, 3),
            (4, 3, -2, -5),
            (-3, 2, 2, -3),
        ];
        let points = [(-5, -2), (0, 0), (1, 2), (2, 0), (4, 0), (4, 3), (7, 8)];
        for (scale, shift) in [
            (Int::from(1), Int::zero()),
            (Int::from(1), Int::from(1) << 200_usize),
            (Int::from(1) << 140_usize, -(Int::from(1) << 210_usize)),
        ] {
            let at = |x: i64, y: i64| Point {
                x: Int::from(x) * &scale + &shift,
                y: Int::from(y) * &scale - &shift,
            };
            for (ax, ay, bx, by) in segments {
                let (a, b) = (at(ax, ay), at(bx, by));
                for (x, y) in points {
                    let p = at(x, y);
                    assert_eq!(point_segment2(&p, &a, &b), projected(&p, &a, &b));
                    assert_eq!(point_segment2(&p, &b, &a), projected(&p, &a, &b));
                }
            }
        }
        assert_eq!(
            point_segment2(&Point::new(1, 0), &Point::zero(), &Point::new(2, 2)),
            Rat::new(1.into(), 2.into())
        );
        assert_eq!(
            point_segment2(&Point::new(-3, 4), &Point::zero(), &Point::new(5, 0)),
            Rat::from_integer(25.into())
        );
    }
    #[test]
    fn prepared_bounds_match_exact_contour_pairs_including_contact_and_concavity() {
        let left = vec![
            simple_polygon(&json!([[0, 0], [2, 0], [2, 2], [0, 2]])).unwrap(),
            simple_polygon(&json!([[10, 0], [12, 0], [12, 2], [10, 2]])).unwrap(),
        ];
        let bounds = PolygonBounds::new(&left).unwrap();
        for (raw, expected) in [
            (json!([[5, 0], [7, 0], [7, 2], [5, 2]]), false),
            (json!([[2, 0], [4, 0], [4, 2], [2, 2]]), false),
            (json!([[1, 0], [3, 0], [3, 2], [1, 2]]), true),
            (
                json!([[100, 100], [102, 100], [102, 102], [100, 102]]),
                false,
            ),
        ] {
            let right = vec![simple_polygon(&raw).unwrap()];
            let cached = bounds.overlaps(&left, &PolygonBounds::new(&right).unwrap(), &right);
            assert_eq!(cached, expected);
            assert_eq!(
                cached,
                left.iter()
                    .any(|p| right.iter().any(|q| positive_overlap(p, q)))
            );
        }
        let notch = vec![
            simple_polygon(&json!([[0, 0], [10, 0], [10, 2], [2, 2], [2, 10], [0, 10]])).unwrap(),
        ];
        let hole = vec![simple_polygon(&json!([[4, 4], [6, 4], [6, 6], [4, 6]])).unwrap()];
        assert!(!PolygonBounds::new(&notch).unwrap().overlaps(
            &notch,
            &PolygonBounds::new(&hole).unwrap(),
            &hole
        ));
        assert!(!PolygonBounds::default().overlaps(&[], &bounds, &left));
    }
    #[test]
    fn contacts_are_not_positive_overlap_and_clipping_is_exact() {
        let a = simple_polygon(&json!([[0, 0], [2, 0], [0, 2]])).unwrap();
        let b = simple_polygon(&json!([[1, 0], [3, 0], [1, 2]])).unwrap();
        assert_eq!(intersection_area(&a, &b), Rat::new(1.into(), 2.into()));
        let touching = simple_polygon(&json!([[2, 0], [4, 0], [4, 2], [2, 2]])).unwrap();
        assert!(!positive_overlap(&a, &touching));
        assert_eq!(separation2(&a, &touching), Rat::zero());
    }
    #[test]
    fn qualification_normalizes_and_rejects_reversal() {
        let a = simple_polygon(&json!([[0, 0], [0, 2], [2, 2], [2, 0], [0, 0]])).unwrap();
        assert!(area2(&a) > Int::zero());
        assert!(simple_polygon(&json!([[0, 0], [2, 0], [1, 0], [2, 2], [0, 2]])).is_err());
    }
    #[test]
    fn sign_predicates_preserve_arbitrary_precision_and_closed_boundaries() {
        let wide = Int::from(1) << 150_usize;
        let a = Point {
            x: wide.clone(),
            y: wide.clone(),
        };
        let b = Point {
            x: &wide + 2,
            y: wide.clone(),
        };
        let c = Point {
            x: wide.clone(),
            y: &wide + 2,
        };
        assert_eq!(cross_sign(&a, &b, &c), Ordering::Greater);
        assert_eq!(cross(&a, &b, &c), 4.into());
        let axis = Int::from(1) << 100_usize;
        assert_eq!(
            cross_sign(
                &Point::zero(),
                &Point {
                    x: axis.clone(),
                    y: 0.into()
                },
                &Point {
                    x: 0.into(),
                    y: axis.clone()
                }
            ),
            Ordering::Greater
        );
        assert_eq!(
            cross(
                &Point::zero(),
                &Point {
                    x: axis.clone(),
                    y: 0.into()
                },
                &Point {
                    x: 0.into(),
                    y: axis.clone()
                }
            ),
            &axis * &axis
        );
        let outer = rectangle(&Bounds {
            x0: wide.clone(),
            y0: wide.clone(),
            x1: &wide + 10,
            y1: &wide + 10,
        });
        let inner = vec![a, b, c];
        assert!(contains(&outer, &inner));
        assert_eq!(
            intersection_area(&outer, &inner),
            Rat::from_integer(2.into())
        );
        assert!(inside(
            &Point {
                x: wide.clone(),
                y: &wide + 5
            },
            &outer
        ));
        assert!(!inside(
            &Point {
                x: &wide - 1,
                y: &wide + 5
            },
            &outer
        ));
    }
    #[test]
    fn bounding_sweep_preserves_crossing_and_concave_notches() {
        assert!(simple_polygon(&json!([[0, 0], [4, 0], [1, 4], [1, -1], [4, 4], [0, 4]])).is_err());
        let notch =
            simple_polygon(&json!([[0, 0], [8, 0], [8, 2], [2, 2], [2, 8], [0, 8]])).unwrap();
        let hole = simple_polygon(&json!([[3, 3], [6, 3], [6, 6], [3, 6]])).unwrap();
        assert!(!positive_overlap(&notch, &hole));
        assert_eq!(intersection_area(&notch, &hole), Rat::zero());
        assert!(!contains(&notch, &hole));
    }
}
