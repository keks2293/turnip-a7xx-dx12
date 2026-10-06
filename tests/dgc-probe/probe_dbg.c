/*
 * probe_dbg — SCRATCH (не в PROBES): изоляция «дыра у верхней кромки / T1 не
 * рисуется один».
 *
 * Наблюдение:
 *  - D0 fullscreen {(-1,-1),(-1,3),(3,-1)}: верхние ~40 строк НЕ покрыты
 *    (остальное рисуется).
 *  - T1 {(-1,-1),(3,0),(3,-1)} один (D4): его клин у верха НЕ рисуется вовсе,
 *    хотя (256,10) лежит внутри T1.
 *  - probe11 = T1+T2 (6 вершин): (256,10) рисуется (green).
 *
 * Варианты геометрии (argv[1]):
 *   0 D0 {(-1,-1),(-1,3),(3,-1)}        3v fullscreen
 *   1 D1 {(-1,-0.9),(-1,3),(3,-0.9)}    3v верх чуть внутрь
 *   2 D2 {(-1,-1),(-1,2),(3,-1)}        3v вершина не y=3
 *   3 D3 {(-1,-3),(-1,1),(3,1)}         3v зеркало по y
 *   4 D4 T1 {(-1,-1),(3,0),(3,-1)}      3v верхний клин
 *   5 D5 T2 {(-1,-1),(-1,0),(3,0)}      3v нижняя половина верха
 *   6 D6 T1+T2 (6v) — геометрия probe11
 * FS (argv[2]): 0=bands 1=green
 */
#include <stdlib.h>
#include "pcommon.h"
#include "vs_full_spv.h"
#include "fs_bands_spv.h"
#include "fs_green_spv.h"

static const float g_g0[3][2] = {{-1,-1},{-1,3},{3,-1}};
static const float g_g1[3][2] = {{-1,-0.9f},{-1,3},{3,-0.9f}};
static const float g_g2[3][2] = {{-1,-1},{-1,2},{3,-1}};
static const float g_g3[3][2] = {{-1,-3},{-1,1},{3,1}};
static const float g_g4[3][2] = {{-1,-1},{3,0},{3,-1}};
static const float g_g5[3][2] = {{-1,-1},{-1,0},{3,0}};
static const float g_g6[6][2] = {{-1,-1},{3,0},{3,-1},{-1,-1},{-1,0},{3,0}};

int main(int argc, char **argv)
{
    int v = (argc > 1) ? atoi(argv[1]) : 0;
    int use_green = (argc > 2) ? atoi(argv[2]) : 0;
    int nv = (v == 6) ? 6 : 3;
    static float verts[6][2];
    const float (*src)[2] =
        (v==0)?g_g0:(v==1)?g_g1:(v==2)?g_g2:(v==3)?g_g3:
        (v==4)?g_g4:(v==5)?g_g5:g_g6;
    for (int i = 0; i < nv; i++) { verts[i][0]=src[i][0]; verts[i][1]=src[i][1]; }

    struct env e;
    env_init(&e, "probdbg");

    buf_create(&e, &e.vbo, &e.vmem, sizeof(verts),
               VK_BUFFER_USAGE_VERTEX_BUFFER_BIT, 0);
    buf_write(&e, e.vmem, verts, sizeof(verts));

    const uint32_t *fss = use_green ? FS_GREEN_SPV : FS_BANDS_SPV;
    size_t fssn = use_green ? FS_GREEN_SPV_N * 4 : FS_BANDS_SPV_N * 4;
    struct stagex st[2] = {
        {VK_SHADER_STAGE_VERTEX_BIT, VS_FULL_SPV, VS_FULL_SPV_N * 4},
        {VK_SHADER_STAGE_FRAGMENT_BIT, (const uint32_t *)fss, fssn},
    };
    VkPipeline pipe = make_pipeline(&e, st, 2, NULL, 0, NULL);

    VkCommandBufferBeginInfo cbi = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
    };
    VKCHECK(vkBeginCommandBuffer(e.cmd, &cbi));
    vkCmdBindPipeline(e.cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, pipe);
    VkDeviceSize off = 0;
    vkCmdBindVertexBuffers(e.cmd, 0, 1, &e.vbo, &off);
    begin_renderpass(&e, C_MAGENTA);
    vkCmdDraw(e.cmd, nv, 1, 0, 0);
    end_renderpass(&e);
    VKCHECK(vkEndCommandBuffer(e.cmd));
    submit_wait(&e);

    void *map = readback(&e);
    printf("D%d fs=%s top rows (x step 32):\n", v, use_green?"green":"bands");
    for (int yy = 0; yy <= 48; yy += 8) {
        printf(" y=%2d:", yy);
        for (int xx = 0; xx < W; xx += 32)
            printf(" %06x", px(map, xx, yy) & 0xFFFFFF);
        printf("\n");
    }
    printf("D%d: col x=256, y step 8:\n", v);
    for (int yy = 0; yy < H; yy += 8)
        printf("%06x ", px(map, 256, yy) & 0xFFFFFF);
    printf("\n");
    return 0;
}
