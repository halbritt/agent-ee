#include <cstdint>
#include <cstdlib>
#include <limits>

extern "C" void score_cpu(const int64_t* offsets, const int64_t* peers,
                          const int32_t* starts, int pads,
                          const int64_t* poses, int count, int64_t* out) {
    for (int i = 0; i < count; ++i) {
        int64_t total = 0;
        for (int p = 0; p < pads; ++p) {
            int64_t x = offsets[2*p], y = offsets[2*p+1];
            for (int r = 0; r < poses[3*i+2]; ++r) {
                int64_t old = x; x = y; y = -old;
            }
            x += poses[3*i]; y += poses[3*i+1];
            int64_t best = std::numeric_limits<int64_t>::max();
            for (int j = starts[p]; j < starts[p+1]; ++j) {
                int64_t d = std::llabs(x - peers[2*j]) + std::llabs(y - peers[2*j+1]);
                if (d < best) best = d;
            }
            total += best;
        }
        out[i] = total;
    }
}
