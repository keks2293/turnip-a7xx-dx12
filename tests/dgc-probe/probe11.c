/*
 * probe11 — тест ориентации вьюпорты (и попутно лицевости):
 * два треугольника покрывают только ВЕРХНЮЮ половину NDC (y ∈ [-1, 0]).
 * По спецификации Vulkan (NDC +y вниз) это верхняя половина кадра:
 * строки памяти 0..127 должны стать зелёными, 128..255 остаться magenta.
 *
 * Интерпретация (ветер по спеку — дефолтный CULL_BACK, PROBE_CULL не нужен):
 *   верх зелёный            → ориентация кадра по спеку (+y вниз);
 *   низ зелёный             → вьюпорта отрисовывает Y наоборот.
 * RC: 0 = по спеку, 5 = Y инвертирован.
 *
 * Обе половины треугольников закрученных визуально ПРОТИВ часовой
 * (area = -1/2 sum(...) > 0 при y-вниз) — иначе CULL_BACK их отсекает
 * (прежний порядок давал spec-area < 0; см. dgc-plan §9).
 */
#include "pcommon.h"
#include "vs_full_spv.h"
#include "fs_green_spv.h"

/* верхняя половина экрана в NDC Vulkan (+y вниз): y ∈ [-1, 0] */
static const float g_top[6][2] = {
    {-1.0f, -1.0f}, { 3.0f,  0.0f}, { 3.0f, -1.0f},
    {-1.0f, -1.0f}, {-1.0f,  0.0f}, { 3.0f,  0.0f},
};

int main(void)
{
    struct env e;
    env_init(&e, "probe11");

    buf_create(&e, &e.vbo, &e.vmem, sizeof(g_top),
               VK_BUFFER_USAGE_VERTEX_BUFFER_BIT, 0);
    buf_write(&e, e.vmem, g_top, sizeof(g_top));

    struct stagex st[2] = {
        {VK_SHADER_STAGE_VERTEX_BIT, VS_FULL_SPV, VS_FULL_SPV_N * 4},
        {VK_SHADER_STAGE_FRAGMENT_BIT, FS_GREEN_SPV, FS_GREEN_SPV_N * 4},
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
    vkCmdDraw(e.cmd, 6, 1, 0, 0);
    end_renderpass(&e);
    VKCHECK(vkEndCommandBuffer(e.cmd));
    submit_wait(&e);

    void *map = readback(&e);
    printf("probe11: rows: y=10 (top half) expect GREEN, y=240 (bottom) expect magenta\n");
    check(&e, "top y=10 green", 256, 10, px(map, 256, 10), C_GREEN);
    check(&e, "bottom y=240 magenta", 256, 240, px(map, 256, 240), C_MAGENTA);
    printf("probe11: top px=%08x bottom px=%08x\n",
           px(map, 256, 10), px(map, 256, 240));

    int flipped = (px(map, 256, 10) != C_GREEN) &&
                  (px(map, 256, 240) == C_GREEN);
    printf("probe11: viewport Y is %s\n", flipped ? "INVERTED (bottom-up)"
                                                   : "correct (top-down, per spec)");
    printf("probe11: %d checks, %d fail\n", e.checks, e.fails);
    return flipped ? 5 : 0;
}
