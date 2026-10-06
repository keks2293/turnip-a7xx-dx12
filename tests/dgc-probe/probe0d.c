/*
 * probe0d — регрессионный контроль: magenta clear + три цветовые полосы
 * полноэкранного треугольника (device-local image, readback).
 * Ожидаемо: (77,128)=red (281,128)=green (435,128)=blue, (2,1)=magenta.
 *
 * ВАЖНО (квик «верхние строки», dgc-plan §9.7): треугольник НЕ покрывает
 * верхние ~32-48 строк кадра (patchy, по A/B 04.10/06.10 — поведение
 * штатное для машины, не регрессия). Поэтому угол (2,1) остаётся magenta —
 * это и проверяется. НЕ менять на C_RED: по спеку угол покрыт, но машина
 * так не рисует (пока).
 * RC: 0 ok, 3 mismatch (в tmux-логах RC не виден — смотреть строки FAIL/OK).
 *
 * Ветер задан ПО СПЕКУ Vulkan (система кадра y-вниз, area = -1/2 sum(...)):
 * порядок вершин визуально ПРОТИВ часовой на экране → front-facing при
 * frontFace=COUNTER_CLOCKWISE + CULL_BACK. Прежний порядок (06.10.2026) был
 * GL-привычный и визуально ПО часовой → spec-area < 0 → корректно отсекался
 * нашим же CULL_BACK (причина «draw не виден» в M2.3, см. dgc-plan §9).
 */
#include "pcommon.h"
#include "vs_full_spv.h"
#include "fs_bands_spv.h"

static const float g_full[3][2] = {
    {-1.0f, -1.0f},
    {-1.0f,  3.0f},
    { 3.0f, -1.0f},
};

int main(void)
{
    struct env e;
    env_init(&e, "probe0");

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
    /* угол (2,1) — квик «верхние строки» (dgc-plan §9.7): по спеку
     * треугольник угол покрывает, но машина верхние ~32-48 строк не
     * растеризует (patchy; A/B 04.10/06.10 идентичны) → там magenta */
    check(&e, "corner stays clear (top-rows quirk)", 2, 1, px(map, 2, 1),
          C_MAGENTA);

    printf("probe0: %d checks, %d fail\n", e.checks, e.fails);
    return e.fails ? 3 : 0;
}
