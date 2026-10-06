/*
 * probe9 — решающий дискриминатор фронт-енда графики:
 * VS пишет 0x5a5a0000+gl_VertexIndex в SSBO (host-visible), FS красит
 * полосы, картинка читается обратно.
 *
 * Интерпретация:
 *   SSBO = {5a5a0000, 5a5a0001, 5a5a0002} → VS исполнился: VFD/dispatch жив.
 *   SSBO = {0,0,0}                        → VS не исполнился: мёртв
 *          VFD/вершинный диспатч (фронт-енд вообще не стартует).
 *   Полосы в картинке → фронт- и растеризатор целиком живы (и это так:
 *          ветер задан по спеку, CULL_BACK не должен отсекать).
 * RC: 0 = SSBO заполнился (VS жив), 3 = нет.
 *
 * Ветер — по спеку (y-вниз, area = -1/2 sum): порядок вершин визуально
 * против часовой; прежний GL-порядок давал spec-area < 0 и корректное
 * отсечение нашим CULL_BACK (см. dgc-plan §9, M2.3).
 */
#include "pcommon.h"
#include "vs_ssb_spv.h"
#include "fs_bands_spv.h"

static const float g_full[3][2] = {
    {-1.0f, -1.0f},
    {-1.0f,  3.0f},
    { 3.0f, -1.0f},
};

int main(void)
{
    struct env e;
    env_init(&e, "probe9");

    buf_create(&e, &e.vbo, &e.vmem, sizeof(g_full),
               VK_BUFFER_USAGE_VERTEX_BUFFER_BIT, 0);
    buf_write(&e, e.vmem, g_full, sizeof(g_full));

    /* SSBO: host-visible, замаплен на всё время работы */
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
        .stageFlags = VK_SHADER_STAGE_VERTEX_BIT,
    };
    VkDescriptorSetLayoutCreateInfo dsci = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = 1,
        .pBindings = &db,
    };
    VkDescriptorSetLayout dsl;
    VKCHECK(vkCreateDescriptorSetLayout(e.dev, &dsci, NULL, &dsl));
    VkDescriptorSet set = ssbo_set_pool(&e, dsl, ssbo, 64,
                                        VK_SHADER_STAGE_VERTEX_BIT);

    struct stagex st[2] = {
        {VK_SHADER_STAGE_VERTEX_BIT, VS_SSB_SPV, VS_SSB_SPV_N * 4},
        {VK_SHADER_STAGE_FRAGMENT_BIT, FS_BANDS_SPV, FS_BANDS_SPV_N * 4},
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
    printf("probe9: ssbo[0..2] = 0x%08x 0x%08x 0x%08x 0x%08x\n",
           v[0], v[1], v[2], v[3]);
    int vs_ran = (v[0] == 0x5a5a0000u) && (v[1] == 0x5a5a0001u) &&
                 (v[2] == 0x5a5a0002u);
    printf("probe9: VS %s\n", vs_ran ? "EXECUTED (front-end alive to VPC)"
                                     : "DID NOT EXECUTE (VFD/dispatch dead)");

    void *map = readback(&e);
    check(&e, "red band", 77, 128, px(map, 77, 128), C_RED);
    check(&e, "green band", 281, 128, px(map, 281, 128), C_GREEN);
    check(&e, "blue band", 435, 128, px(map, 435, 128), C_BLUE);
    /* квик «верхние строки» (dgc-plan §9.7): верхние ~32-48 строк не
     * растеризуются → угол остаётся magenta */
    check(&e, "clear magenta", 2, 1, px(map, 2, 1), C_MAGENTA);

    printf("probe9: %d checks, %d fail\n", e.checks, e.fails);
    return vs_ran ? 0 : 3;
}
