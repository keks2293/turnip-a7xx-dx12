/*
 * probe12 — решающий тест «машина или пробы?»: тот же fullscreen-треугольник
 * и полосы, что в probe0d, но ветер ЗАДАН ПО СПЕКУ Vulkan:
 * порядок (-1,-1) → (-1,3) → (3,-1) = визуально ПРОТИВ часовой на экране
 * (fb y вниз) → area > 0 по спеку (a = -1/2 sum(...)) → front-facing при
 * frontFace=COUNTER_CLOCKWISE.
 *
 * При НЕВЫКЛЮЧЕННОМ отсечении (дефолт пайплайна = CULL_BACK + FRONT_CCW):
 *   полосы есть    → машина спецификационно-корректна, старые пробы ветрили
 *                    неправильно (GL-привычка) — дефекта нет вообще;
 *   полос нет      → машина инвертирует лицевость (дефект).
 * RC: 0 = полосы есть, 6 = нет.
 */
#include "pcommon.h"
#include "vs_full_spv.h"
#include "fs_bands_spv.h"

static const float g_full[3][2] = {
    {-1.0f, -1.0f},
    {-1.0f,  3.0f},   /* вниз (визуально CCW в системе Vulkan) */
    { 3.0f, -1.0f},
};

int main(void)
{
    struct env e;
    env_init(&e, "probe12");

    buf_create(&e, &e.vbo, &e.vmem, sizeof(g_full),
               VK_BUFFER_USAGE_VERTEX_BUFFER_BIT, 0);
    buf_write(&e, e.vmem, g_full, sizeof(g_full));

    struct stagex st[2] = {
        {VK_SHADER_STAGE_VERTEX_BIT, VS_FULL_SPV, VS_FULL_SPV_N * 4},
        {VK_SHADER_STAGE_FRAGMENT_BIT, FS_BANDS_SPV, FS_BANDS_SPV_N * 4},
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
    vkCmdDraw(e.cmd, 3, 1, 0, 0);
    end_renderpass(&e);
    VKCHECK(vkEndCommandBuffer(e.cmd));
    submit_wait(&e);

    void *map = readback(&e);
    check(&e, "red band", 77, 128, px(map, 77, 128), C_RED);
    check(&e, "green band", 281, 128, px(map, 281, 128), C_GREEN);
    check(&e, "blue band", 435, 128, px(map, 435, 128), C_BLUE);

    int bands_ok = (e.fails == 0);
    printf("probe12: spec-correct winding + CULL_BACK => machine %s\n",
           bands_ok ? "CONFORMANT (old probes were wrongly wound)"
                    : "INVERTS FACING (real machine defect)");
    printf("probe12: %d checks, %d fail\n", e.checks, e.fails);
    return bands_ok ? 0 : 6;
}
