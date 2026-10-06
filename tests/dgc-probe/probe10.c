/*
 * probe10 — дискриминатор растеризатор vs запись в кадр:
 * VS пишет v[0..2] (как probe9), FS пишет v[3] в тот же SSBO.
 *
 * Интерпретация (ветер по спеку, дефолтный CULL_BACK):
 *   v[0..2]=5a5a0000+  → VS жив (контроль, что проба вообще стартовала);
 *   v[3]=5a5a0f00       → фрагменты сгенерированы: PS→RB запись в кадр жива;
 *   v[3]=0              → фрагментов нет: либо мёртв растеризатор VPC→GRAS,
 *                          либо треугольник отсечён/не дошёл до фрагментной
 *                          стадии (тогда гонять с PROBE_CULL=none).
 * RC: 0 = фрагменты есть, 4 = нет.
 *
 * Ветер — по спеку (прежний GL-порядок давал spec-area < 0 → CULL_BACK
 * корректно отсекал треугольник, v[3] оставался 0 — см. dgc-plan §9).
 */
#include "pcommon.h"
#include "vs_ssb_spv.h"
#include "fs_ssb_spv.h"

static const float g_full[3][2] = {
    {-1.0f, -1.0f},
    {-1.0f,  3.0f},
    { 3.0f, -1.0f},
};

int main(void)
{
    struct env e;
    env_init(&e, "probe10");

    buf_create(&e, &e.vbo, &e.vmem, sizeof(g_full),
               VK_BUFFER_USAGE_VERTEX_BUFFER_BIT, 0);
    buf_write(&e, e.vmem, g_full, sizeof(g_full));

    VkBuffer ssbo;
    VkDeviceMemory smem;
    buf_create(&e, &ssbo, &smem, 64, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, 1);
    void *sm;
    VKCHECK(vkMapMemory(e.dev, smem, 0, 64, 0, &sm));
    memset(sm, 0, 64);

    VkDescriptorSetLayoutBinding db = {
        .binding = 0,
        .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
        .descriptorCount = 1,
        .stageFlags = VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT,
    };
    VkDescriptorSetLayoutCreateInfo dsci = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = 1,
        .pBindings = &db,
    };
    VkDescriptorSetLayout dsl;
    VKCHECK(vkCreateDescriptorSetLayout(e.dev, &dsci, NULL, &dsl));
    VkDescriptorSet set = ssbo_set_pool(&e, dsl, ssbo, 64,
                                        VK_SHADER_STAGE_VERTEX_BIT |
                                        VK_SHADER_STAGE_FRAGMENT_BIT);

    struct stagex st[2] = {
        {VK_SHADER_STAGE_VERTEX_BIT, VS_SSB_SPV, VS_SSB_SPV_N * 4},
        {VK_SHADER_STAGE_FRAGMENT_BIT, FS_SSB_SPV, FS_SSB_SPV_N * 4},
    };
    VkPipelineLayout layout = VK_NULL_HANDLE;
    VkPipeline pipe = make_pipeline(&e, st, 2, &dsl, 1, &layout);

    VkCommandBufferBeginInfo cbi = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
    };
    VKCHECK(vkBeginCommandBuffer(e.cmd, &cbi));
    vkCmdBindPipeline(e.cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, pipe);
    VkDeviceSize off = 0;
    vkCmdBindVertexBuffers(e.cmd, 0, 1, &e.vbo, &off);
    vkCmdBindDescriptorSets(e.cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, layout, 0, 1,
                            &set, 0, NULL);
    begin_renderpass(&e, C_MAGENTA);
    vkCmdDraw(e.cmd, 3, 1, 0, 0);
    end_renderpass(&e);
    VKCHECK(vkEndCommandBuffer(e.cmd));
    submit_wait(&e);

    uint32_t *v = sm;
    printf("probe10: ssbo[0..3] = 0x%08x 0x%08x 0x%08x 0x%08x\n",
           v[0], v[1], v[2], v[3]);
    int vs_ran = (v[0] == 0x5a5a0000u) && (v[1] == 0x5a5a0001u) &&
                 (v[2] == 0x5a5a0002u);
    int fs_ran = (v[3] == 0x5a5a0f00u);
    printf("probe10: VS %s, FS %s\n",
           vs_ran ? "EXECUTED" : "NOT EXECUTED",
           fs_ran ? "EXECUTED -> dead segment PS->RB (write/blend)"
                  : "NOT EXECUTED -> rasterizer VPC/GRAS dead");

    void *map = readback(&e);
    check(&e, "frag magenta (fs out)", 256, 128, px(map, 256, 128), C_MAGENTA);
    /* квик «верхние строки» (dgc-plan §9.7): верхние ~32-48 строк не
     * растеризуются → угол остаётся magenta */
    check(&e, "clear magenta", 2, 1, px(map, 2, 1), C_MAGENTA);

    printf("probe10: %d checks, %d fail\n", e.checks, e.fails);
    if (!vs_ran)
        return 3;
    return fs_ran ? 0 : 4;
}
