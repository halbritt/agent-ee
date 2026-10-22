//! Exact prepared Stage-A Manhattan scoring. No placement policy or geometry.
//!
//! The C ABI owns every input after `prepare`; scoring is read-only and allocates
//! nothing. Coordinates are signed i64 nanometres, rotations are degrees. A score
//! outside nonnegative i64 is refused so the Python caller can use its arbitrary
//! precision oracle. A failed batch must be discarded in its entirety.
//!
//! Pointer contracts: all nonempty input/output arrays must be valid, aligned,
//! and cover their documented lengths. Output must not alias input or prepared
//! state. A handle is either null or a live handle returned by `prepare`, freed
//! exactly once after all readers finish. Foreign pointers and concurrent free
//! cannot be validated here. Invalid array contents/counts are refused.

use std::ffi::c_void;
use std::slice;

const MAX: i128 = i64::MAX as i128;

#[derive(Debug)]
struct Rotation {
    // The peer coordinates are translated once: target = peer - pad offset.
    // Each span belongs to one physical pad; repeated pads remain separate.
    starts: Box<[usize]>,
    xs: Box<[i64]>,
    ys: Box<[i64]>,
    wide: Option<Box<[(i128, i128)]>>,
    fast_limit: i64,
}

impl Rotation {
    fn new(offsets: &[i64], starts: &[i64], peers: &[i64], first: usize, end: usize) -> Self {
        let pads = end - first;
        // If both a query and every target lie in [-B, B], a pad distance is
        // <= 4B and their sum <= pads*4B <= i64::MAX. This proves all arithmetic
        // in the fast loop safe without checks on each peer or accumulation.
        let limit = if pads == 0 {
            i64::MAX
        } else {
            (MAX / (4 * pads as i128)) as i64
        };
        let mut translated = Vec::new();
        let mut boundaries = Vec::with_capacity(pads + 1);
        boundaries.push(0);
        let mut fits = true;
        for pad in first..end {
            let ox = offsets[2 * pad] as i128;
            let oy = offsets[2 * pad + 1] as i128;
            for peer in starts[pad] as usize..starts[pad + 1] as usize {
                let x = peers[2 * peer] as i128 - ox;
                let y = peers[2 * peer + 1] as i128 - oy;
                fits &= x.abs() <= limit as i128 && y.abs() <= limit as i128;
                translated.push((x, y));
            }
            boundaries.push(translated.len());
        }
        if fits {
            Self {
                starts: boundaries.into_boxed_slice(),
                xs: translated.iter().map(|p| p.0 as i64).collect(),
                ys: translated.iter().map(|p| p.1 as i64).collect(),
                wide: None,
                fast_limit: limit,
            }
        } else {
            Self {
                starts: boundaries.into_boxed_slice(),
                xs: Box::new([]),
                ys: Box::new([]),
                wide: Some(translated.into_boxed_slice()),
                fast_limit: 0,
            }
        }
    }

    #[inline]
    fn score(&self, x: i64, y: i64) -> i64 {
        if let Some(points) = &self.wide {
            return self.score_wide(x, y, |i| points[i]);
        }
        let b = self.fast_limit;
        if x < -b || x > b || y < -b || y > b {
            return self.score_wide(x, y, |i| (self.xs[i] as i128, self.ys[i] as i128));
        }
        let mut total = 0i64;
        for span in self.starts.windows(2) {
            let xs = &self.xs[span[0]..span[1]];
            let ys = &self.ys[span[0]..span[1]];
            let mut best = i64::MAX;
            for (&a, &b) in xs.iter().zip(ys) {
                best = best.min((x - a).abs() + (y - b).abs());
            }
            total += best;
        }
        total
    }

    #[cold]
    fn score_wide(&self, x: i64, y: i64, point: impl Fn(usize) -> (i128, i128)) -> i64 {
        let mut total = 0i128;
        for span in self.starts.windows(2) {
            let mut best = i128::MAX;
            for i in span[0]..span[1] {
                let (a, b) = point(i);
                best = best.min((x as i128 - a).abs() + (y as i128 - b).abs());
            }
            total += best;
            if total > MAX {
                return -1;
            }
        }
        total as i64
    }
}

#[derive(Debug)]
struct Prepared {
    rotations: [Rotation; 4],
}

impl Prepared {
    fn new(offsets: &[i64], starts: &[i64], peers: &[i64], rotations: &[i64]) -> Option<Self> {
        if offsets.len() % 2 != 0 || peers.len() % 2 != 0 {
            return None;
        }
        let pads = offsets.len() / 2;
        if starts.len() != pads + 1
            || rotations.len() != 5
            || starts[0] != 0
            || rotations[0] != 0
            || *starts.last()? != (peers.len() / 2) as i64
            || rotations[4] != pads as i64
            || starts.windows(2).any(|s| s[0] < 0 || s[0] >= s[1])
            || rotations.windows(2).any(|s| s[0] < 0 || s[0] > s[1])
        {
            return None;
        }
        Some(Self {
            rotations: std::array::from_fn(|r| {
                Rotation::new(
                    offsets,
                    starts,
                    peers,
                    rotations[r] as usize,
                    rotations[r + 1] as usize,
                )
            }),
        })
    }

    #[inline]
    fn score(&self, x: i64, y: i64, rotation: i64) -> i64 {
        match rotation {
            0 => self.rotations[0].score(x, y),
            90 => self.rotations[1].score(x, y),
            180 => self.rotations[2].score(x, y),
            270 => self.rotations[3].score(x, y),
            _ => -1,
        }
    }
}

fn array_len(count: i64, width: usize) -> Option<usize> {
    let len = usize::try_from(count).ok()?.checked_mul(width)?;
    (len <= isize::MAX as usize / std::mem::size_of::<i64>()).then_some(len)
}

unsafe fn input<'a>(pointer: *const i64, len: usize) -> Option<&'a [i64]> {
    if len == 0 {
        Some(&[])
    } else if pointer.is_null() {
        None
    } else {
        Some(slice::from_raw_parts(pointer, len))
    }
}

#[no_mangle]
pub extern "C" fn agent_ee_score_abi_v1() -> u32 {
    1
}

/// Copy and prepare four rotations. Null means invalid counts or contents.
/// `offsets`: 2*pads, `starts`: pads+1, `peers`: 2*peer_count, `rotations`: 5.
/// `rotations` partitions the pad indices; `starts` partitions peer point pairs.
/// Empty rotations are valid, but every included pad needs a nonempty peer span.
/// # Safety
/// The module-level pointer contracts apply to each input array.
#[no_mangle]
pub unsafe extern "C" fn agent_ee_score_prepare_v1(
    offsets: *const i64,
    starts: *const i64,
    pads: i64,
    peers: *const i64,
    peer_count: i64,
    rotations: *const i64,
) -> *mut c_void {
    let prepare = || -> Option<Prepared> {
        let offsets_len = array_len(pads, 2)?;
        let starts_len = array_len(pads.checked_add(1)?, 1)?;
        let peers_len = array_len(peer_count, 2)?;
        Prepared::new(
            input(offsets, offsets_len)?,
            input(starts, starts_len)?,
            input(peers, peers_len)?,
            input(rotations, 5)?,
        )
    };
    match prepare() {
        Some(prepared) => Box::into_raw(Box::new(prepared)).cast(),
        None => std::ptr::null_mut(),
    }
}

/// Return the exact nonnegative score, or -1 for overflow/null/invalid rotation.
/// # Safety
/// `handle` must obey the module-level handle lifetime contract.
#[no_mangle]
pub unsafe extern "C" fn agent_ee_score_one_v1(
    handle: *const c_void,
    x: i64,
    y: i64,
    rotation: i64,
) -> i64 {
    match handle.cast::<Prepared>().as_ref() {
        Some(prepared) => prepared.score(x, y, rotation),
        None => -1,
    }
}

/// Score `count` triples (x, y, rotation) into `count` output values.
/// Status: 0 success, 1 score overflow, 2 invalid count/pointer/rotation.
/// On failure output may be partially written; callers must discard the batch.
/// # Safety
/// The module-level pointer and handle contracts apply. Zero-count arrays may be null.
#[no_mangle]
pub unsafe extern "C" fn agent_ee_score_batch_v1(
    handle: *const c_void,
    poses: *const i64,
    count: i64,
    out: *mut i64,
) -> i32 {
    let Some(prepared) = handle.cast::<Prepared>().as_ref() else {
        return 2;
    };
    let Some(len) = array_len(count, 3) else {
        return 2;
    };
    let Some(poses) = input(poses, len) else {
        return 2;
    };
    if count == 0 {
        return 0;
    }
    if out.is_null() {
        return 2;
    }
    let output = slice::from_raw_parts_mut(out, count as usize);
    for (pose, result) in poses.chunks_exact(3).zip(output) {
        if !matches!(pose[2], 0 | 90 | 180 | 270) {
            return 2;
        }
        let score = prepared.score(pose[0], pose[1], pose[2]);
        if score < 0 {
            return 1;
        }
        *result = score;
    }
    0
}

/// Release a prepared handle. Null is accepted.
/// # Safety
/// Nonnull handles must come from prepare and must not be freed or read again.
#[no_mangle]
pub unsafe extern "C" fn agent_ee_score_free_v1(handle: *mut c_void) {
    if !handle.is_null() {
        drop(Box::from_raw(handle.cast::<Prepared>()));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn oracle(
        offsets: &[i64],
        starts: &[i64],
        peers: &[i64],
        first: usize,
        end: usize,
        x: i64,
        y: i64,
    ) -> i64 {
        let score: i128 = (first..end)
            .map(|p| {
                (starts[p] as usize..starts[p + 1] as usize)
                    .map(|j| {
                        (offsets[2 * p] as i128 + x as i128 - peers[2 * j] as i128).abs()
                            + (offsets[2 * p + 1] as i128 + y as i128 - peers[2 * j + 1] as i128)
                                .abs()
                    })
                    .min()
                    .unwrap()
            })
            .sum();
        if score > MAX {
            -1
        } else {
            score as i64
        }
    }

    #[test]
    fn all_rotations_duplicate_terms_and_nearest_peer() {
        let offsets = [3, -4, 3, -4, -4, -3, -3, 4, 4, 3];
        let peers = [0, 0, 9, -9, 0, 0, 9, -9, -1, 8, 3, 5, -6, 2];
        let starts = [0, 2, 4, 5, 6, 7];
        let rotations = [0, 2, 3, 4, 5];
        let prepared = Prepared::new(&offsets, &starts, &peers, &rotations).unwrap();
        for x in -20..21 {
            for y in -20..21 {
                for r in 0..4 {
                    assert_eq!(
                        prepared.score(x, y, r as i64 * 90),
                        oracle(
                            &offsets,
                            &starts,
                            &peers,
                            rotations[r] as usize,
                            rotations[r + 1] as usize,
                            x,
                            y
                        )
                    );
                }
            }
        }
    }

    #[test]
    fn wide_translation_cancellation_and_signed_boundaries() {
        let values = [i64::MIN, i64::MIN + 1, -1, 0, 1, i64::MAX - 1, i64::MAX];
        for &offset in &values {
            for &peer in &values {
                let offsets = [offset, 0];
                let peers = [peer, 0];
                let p = Prepared::new(&offsets, &[0, 1], &peers, &[0, 1, 1, 1, 1]).unwrap();
                for &x in &values {
                    assert_eq!(
                        p.score(x, 0, 0),
                        oracle(&offsets, &[0, 1], &peers, 0, 1, x, 0)
                    );
                }
            }
        }
        // Peer-offset is outside i64, yet query cancellation yields an exact i64 score.
        let p = Prepared::new(&[i64::MIN, 0], &[0, 1], &[1, 0], &[0, 1, 1, 1, 1]).unwrap();
        assert_eq!(p.score(i64::MAX, 0, 0), 2);
    }

    #[test]
    fn narrow_boundary_and_sum_overflow() {
        let p =
            Prepared::new(&[0, 0, 0, 0], &[0, 1, 2], &[1, -1, 1, -1], &[0, 2, 2, 2, 2]).unwrap();
        let b = p.rotations[0].fast_limit;
        for x in [-b - 1, -b, b, b + 1, i64::MIN, i64::MAX] {
            for y in [-b - 1, -b, b, b + 1] {
                assert_eq!(
                    p.score(x, y, 0),
                    oracle(&[0, 0, 0, 0], &[0, 1, 2], &[1, -1, 1, -1], 0, 2, x, y)
                );
            }
        }
        assert_eq!(p.score(i64::MAX, 0, 0), -1);
        let p = Prepared::new(&[0, 0], &[0, 1], &[0, 0], &[0, 1, 1, 1, 1]).unwrap();
        assert_eq!(p.score(i64::MAX, 0, 0), i64::MAX);
        assert_eq!(p.score(i64::MAX, 1, 0), -1);
    }

    #[test]
    fn rejects_invalid_spans_and_supports_empty_rotations() {
        for (starts, rotations) in [
            (vec![1, 1], vec![0, 1, 1, 1, 1]),
            (vec![0, 0], vec![0, 1, 1, 1, 1]),
            (vec![0, 2], vec![0, 1, 1, 1, 1]),
            (vec![0, 1], vec![0, 2, 1, 1, 1]),
            (vec![0, 1], vec![-1, 1, 1, 1, 1]),
        ] {
            assert!(Prepared::new(&[0, 0], &starts, &[0, 0], &rotations).is_none());
        }
        let p = Prepared::new(&[], &[0], &[], &[0; 5]).unwrap();
        for r in [0, 90, 180, 270] {
            assert_eq!(p.score(i64::MIN, i64::MAX, r), 0);
        }
        assert_eq!(p.score(0, 0, 45), -1);
    }

    #[test]
    fn ffi_owns_inputs_and_refuses_invalid_batches() {
        unsafe {
            assert_eq!(agent_ee_score_abi_v1(), 1);
            let handle = {
                let offsets = [0, 0];
                let peers = [3, 4];
                agent_ee_score_prepare_v1(
                    offsets.as_ptr(),
                    [0, 1].as_ptr(),
                    1,
                    peers.as_ptr(),
                    1,
                    [0, 1, 1, 1, 1].as_ptr(),
                )
            };
            assert!(!handle.is_null());
            assert_eq!(agent_ee_score_one_v1(handle, 0, 0, 0), 7);
            let poses = [0, 0, 0, 1, 2, 0, 1, 2, 90];
            let mut out = [0; 3];
            assert_eq!(
                agent_ee_score_batch_v1(handle, poses.as_ptr(), 3, out.as_mut_ptr()),
                0
            );
            assert_eq!(out, [7, 4, 0]);
            assert_eq!(
                agent_ee_score_batch_v1(handle, [0, 0, 45].as_ptr(), 1, out.as_mut_ptr()),
                2
            );
            assert_eq!(
                agent_ee_score_batch_v1(handle, [i64::MIN, 0, 0].as_ptr(), 1, out.as_mut_ptr()),
                1
            );
            assert_eq!(
                agent_ee_score_batch_v1(handle, std::ptr::null(), 0, std::ptr::null_mut()),
                0
            );
            assert_eq!(
                agent_ee_score_batch_v1(handle, std::ptr::null(), -1, out.as_mut_ptr()),
                2
            );
            agent_ee_score_free_v1(handle);
            agent_ee_score_free_v1(std::ptr::null_mut());
            assert_eq!(agent_ee_score_one_v1(std::ptr::null(), 0, 0, 0), -1);
            assert!(agent_ee_score_prepare_v1(
                std::ptr::null(),
                std::ptr::null(),
                i64::MAX,
                std::ptr::null(),
                0,
                std::ptr::null()
            )
            .is_null());
        }
    }
}
