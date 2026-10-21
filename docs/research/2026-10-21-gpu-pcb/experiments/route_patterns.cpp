// Research-only immutable-grid pattern scorer; shared verbatim with CuPy CUDA.
// Avoid host standard-library headers: NVRTC compiles this same translation unit.
typedef unsigned char uint8_t;
typedef int int32_t;
typedef long long int64_t;
static_assert(sizeof(int32_t) == 4 && sizeof(int64_t) == 8, "fixed width required");
#define INF_COST 0x3fffffffffffffffLL

#ifdef __CUDACC__
#define SCORE_FN __device__
#else
#define SCORE_FN
#endif
SCORE_FN int64_t score_one(const uint8_t* blocked, const int32_t* costs,
                          int h, int w, const int32_t* p) {
    int x = p[0], y = p[1];
    if (x < 0 || x >= w || y < 0 || y >= h || blocked[y*w+x]) return INF_COST;
    int64_t total = costs[y*w+x];
    for (int s = 1; s < 4; ++s) {
        int tx = p[2*s], ty = p[2*s+1];
        if (tx < 0 || tx >= w || ty < 0 || ty >= h || (tx != x && ty != y)) return INF_COST;
        int dx = (tx > x) - (tx < x), dy = (ty > y) - (ty < y);
        while (x != tx || y != ty) {
            x += dx; y += dy;
            if (blocked[y*w+x]) return INF_COST;
            total += costs[y*w+x];
        }
    }
    return total;
}
#ifdef __CUDACC__
extern "C" __global__ void score_patterns(const uint8_t* b, const int32_t* c,
                                         int h, int w, const int32_t* paths,
                                         int n, long long* out) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) out[i] = score_one(b,c,h,w,paths+8*i);
}
#else
extern "C" void score_patterns(const uint8_t* b, const int32_t* c,
                               int h, int w, const int32_t* paths,
                               int n, int64_t* out) {
    for (int i=0; i<n; ++i) out[i] = score_one(b,c,h,w,paths+8*i);
}
#endif
