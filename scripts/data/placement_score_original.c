/* Original agent-ee Stage-A numerical kernel. No placement policy or geometry. */
#include <stdint.h>
#include <limits.h>

int agent_ee_score_v1(const int64_t *offsets, const int64_t *peers,
                      const int64_t *starts, int64_t pads,
                      const int64_t *poses, int64_t count, int64_t *out) {
    for (int64_t i = 0; i < count; ++i) {
        __int128 total = 0;
        for (int64_t p = 0; p < pads; ++p) {
            __int128 x = (__int128)offsets[2*p] + poses[2*i];
            __int128 y = (__int128)offsets[2*p+1] + poses[2*i+1];
            __int128 best = ((__int128)1 << 126);
            for (int64_t j = starts[p]; j < starts[p+1]; ++j) {
                __int128 dx = x - peers[2*j], dy = y - peers[2*j+1];
                __int128 d = (dx < 0 ? -dx : dx) + (dy < 0 ? -dy : dy);
                if (d < best) best = d;
            }
            total += best;
            if (total > INT64_MAX) return 1; /* discard the entire batch */
        }
        out[i] = (int64_t)total;
    }
    return 0;
}
