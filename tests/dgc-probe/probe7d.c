/*
 * probe7d — контроль SQE: чистый compute dispatch пишет 0xdeadbeef в
 * host-visible буфер. Если не видно — compute сломан (тогда и graphics
 * draw не имеет шансов). RC: 0 ok.
 */
#include "pcommon.h"
#include "cp_spv.h"

int main(void)
{
    struct env e;
    env_init(&e, "probe7");

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
        .stageFlags = VK_SHADER_STAGE_ALL,
    };
    VkDescriptorSetLayoutCreateInfo dsci = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = 1,
        .pBindings = &db,
    };
    VkDescriptorSetLayout dsl;
    VKCHECK(vkCreateDescriptorSetLayout(e.dev, &dsci, NULL, &dsl));
    VkDescriptorSet set = ssbo_set_pool(&e, dsl, ssbo, 64, VK_SHADER_STAGE_ALL);

    VkPipelineLayoutCreateInfo pli = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = 1,
        .pSetLayouts = &dsl,
    };
    VkPipelineLayout layout;
    VKCHECK(vkCreatePipelineLayout(e.dev, &pli, NULL, &layout));

    VkShaderModuleCreateInfo mci = {
        .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = CP_SPV_N * 4,
        .pCode = CP_SPV,
    };
    VkShaderModule mod;
    VKCHECK(vkCreateShaderModule(e.dev, &mci, NULL, &mod));
    VkComputePipelineCreateInfo cpci = {
        .sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage = {
            .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .stage = VK_SHADER_STAGE_COMPUTE_BIT,
            .module = mod,
            .pName = "main",
        },
        .layout = layout,
    };
    VkPipeline pipe;
    VKCHECK(vkCreateComputePipelines(e.dev, VK_NULL_HANDLE, 1, &cpci, NULL,
                                     &pipe));

    VkCommandBufferBeginInfo cbi = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
    };
    VKCHECK(vkBeginCommandBuffer(e.cmd, &cbi));
    vkCmdBindPipeline(e.cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipe);
    vkCmdBindDescriptorSets(e.cmd, VK_PIPELINE_BIND_POINT_COMPUTE, layout, 0, 1,
                            &set, 0, NULL);
    vkCmdDispatch(e.cmd, 1, 1, 1);
    VKCHECK(vkEndCommandBuffer(e.cmd));
    submit_wait(&e);

    uint32_t *v = sm;
    check(&e, "compute", 0, 0, v[0], 0xdeadbeefu);
    printf("probe7: %d checks, %d fail (v=0x%08x)\n", e.checks, e.fails, v[0]);
    return e.fails ? 3 : 0;
}
