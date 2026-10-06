/* pcommon.h — минимальный Vulkan-стенд для probe*-тестов Starfield/DGC.
 * ВАЖНО: лежит в репозитории (tests/dgc-probe/), НЕ в /tmp — /tmp стирается
 * после ребута, как это уже случилось 06.10.2026.
 *
 * Стенд: image 512x256 R8G8B8A8 (device-local), clear magenta (0xFFFF00FF),
 * полнoэкранное треугольник, readback через host-visible буфер.
 * Цвета (LE u32 = R | G<<8 | B<<16 | A<<24):
 *   magenta=0xFFFF00FF red=0xFF0000FF green=0xFF00FF00 blue=0xFFFF0000
 */
#ifndef PCOMMON_H
#define PCOMMON_H

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <vulkan/vulkan.h>

#define W 512
#define H 256
#define IMGFMT VK_FORMAT_R8G8B8A8_UNORM

#define C_MAGENTA 0xFFFF00FFu
#define C_RED     0xFF0000FFu
#define C_GREEN   0xFF00FF00u
#define C_BLUE    0xFFFF0000u

#define VKCHECK(x)                                                        \
   do {                                                                   \
      VkResult _r = (x);                                                  \
      if (_r != VK_SUCCESS) {                                             \
         fprintf(stderr, "VKFAIL %s:%d %s -> %d\n", __FILE__, __LINE__,   \
                 #x, (int)_r);                                            \
         exit(2);                                                         \
      }                                                                   \
   } while (0)

struct env {
   const char *name;
   VkInstance inst;
   VkPhysicalDevice phys;
   VkDevice dev;
   uint32_t qfam;
   VkQueue queue;
   VkCommandPool pool;
   VkCommandBuffer cmd;
   VkFence fence;

   VkImage img;
   VkDeviceMemory imgmem;
   VkImageView view;
   VkRenderPass rp;
   VkFramebuffer fb;

   VkBuffer vbo, readbuf;
   VkDeviceMemory vmem, rmem;

   int checks, fails;
};

static inline uint32_t
px(const void *map, int x, int y)
{
   const uint8_t *b = (const uint8_t *)map + ((size_t)y * W + x) * 4;
   return b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) |
          ((uint32_t)b[3] << 24);
}

static inline void
check(struct env *e, const char *what, int x, int y, uint32_t got,
      uint32_t want)
{
   e->checks++;
   if (got != want) {
      e->fails++;
      fprintf(stderr, "FAIL (%d,%d) %-12s got 0x%08x expect 0x%08x\n", x, y,
              what, got, want);
   } else {
      fprintf(stderr, "OK   (%d,%d) %s\n", x, y, what);
   }
}

static inline uint32_t
find_mem_type(struct env *e, uint32_t bits, VkMemoryPropertyFlags want)
{
   VkPhysicalDeviceMemoryProperties mp;
   vkGetPhysicalDeviceMemoryProperties(e->phys, &mp);
   for (uint32_t i = 0; i < mp.memoryTypeCount; i++) {
      if (((bits >> i) & 1) && (mp.memoryTypes[i].propertyFlags & want) == want)
         return i;
   }
   for (uint32_t i = 0; i < mp.memoryTypeCount; i++) {
      if ((bits >> i) & 1)
         return i;
   }
   fprintf(stderr, "no mem type\n");
   exit(2);
}

static inline void
buf_create(struct env *e, VkBuffer *b, VkDeviceMemory *m, VkDeviceSize size,
           VkBufferUsageFlags usage, int host_visible)
{
   VkBufferCreateInfo bci = {
      .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
      .size = size,
      .usage = usage,
      .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
   };
   VKCHECK(vkCreateBuffer(e->dev, &bci, NULL, b));
   VkMemoryRequirements mr;
   vkGetBufferMemoryRequirements(e->dev, *b, &mr);
   VkMemoryPropertyFlags want =
      host_visible ? (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                      VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)
                   : 0;
   VkMemoryAllocateInfo mai = {
      .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
      .allocationSize = mr.size,
      .memoryTypeIndex = find_mem_type(e, mr.memoryTypeBits, want),
   };
   VKCHECK(vkAllocateMemory(e->dev, &mai, NULL, m));
   VKCHECK(vkBindBufferMemory(e->dev, *b, *m, 0));
}

static inline void
buf_write(struct env *e, VkDeviceMemory m, const void *data, VkDeviceSize size)
{
   void *p;
   VKCHECK(vkMapMemory(e->dev, m, 0, size, 0, &p));
   memcpy(p, data, (size_t)size);
   vkUnmapMemory(e->dev, m);
}

static inline void
env_init(struct env *e, const char *name)
{
   memset(e, 0, sizeof(*e));
   e->name = name;

   VkApplicationInfo ai = {
      .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
      .apiVersion = VK_API_VERSION_1_0,
   };
   VkInstanceCreateInfo ici = {
      .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
      .pApplicationInfo = &ai,
   };
   VKCHECK(vkCreateInstance(&ici, NULL, &e->inst));

   uint32_t n = 1;
   VKCHECK(vkEnumeratePhysicalDevices(e->inst, &n, &e->phys));

   uint32_t nf = 0;
   vkGetPhysicalDeviceQueueFamilyProperties(e->phys, &nf, NULL);
   VkQueueFamilyProperties *fp = calloc(nf, sizeof(*fp));
   vkGetPhysicalDeviceQueueFamilyProperties(e->phys, &nf, fp);
   e->qfam = (uint32_t)-1;
   for (uint32_t i = 0; i < nf; i++) {
      if (fp[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) {
         e->qfam = i;
         break;
      }
   }
   free(fp);
   if (e->qfam == (uint32_t)-1) {
      fprintf(stderr, "no graphics queue\n");
      exit(2);
   }

   float prio = 1.0f;
   VkDeviceQueueCreateInfo qci = {
      .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
      .queueFamilyIndex = e->qfam,
      .queueCount = 1,
      .pQueuePriorities = &prio,
   };
   VkDeviceCreateInfo dci = {
      .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
      .queueCreateInfoCount = 1,
      .pQueueCreateInfos = &qci,
   };
   VKCHECK(vkCreateDevice(e->phys, &dci, NULL, &e->dev));
   vkGetDeviceQueue(e->dev, e->qfam, 0, &e->queue);

   VkCommandPoolCreateInfo pci = {
      .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
      .queueFamilyIndex = e->qfam,
   };
   VKCHECK(vkCreateCommandPool(e->dev, &pci, NULL, &e->pool));
   VkCommandBufferAllocateInfo cai = {
      .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
      .commandPool = e->pool,
      .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
      .commandBufferCount = 1,
   };
   VKCHECK(vkAllocateCommandBuffers(e->dev, &cai, &e->cmd));

   VkFenceCreateInfo fci = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
   VKCHECK(vkCreateFence(e->dev, &fci, NULL, &e->fence));

   /* 512x256 device-local color image */
   VkImageCreateInfo ic = {
      .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
      .imageType = VK_IMAGE_TYPE_2D,
      .format = IMGFMT,
      .extent = {W, H, 1},
      .mipLevels = 1,
      .arrayLayers = 1,
      .samples = VK_SAMPLE_COUNT_1_BIT,
      .tiling = VK_IMAGE_TILING_OPTIMAL,
      .usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT |
               VK_IMAGE_USAGE_TRANSFER_SRC_BIT,
      .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
   };
   VKCHECK(vkCreateImage(e->dev, &ic, NULL, &e->img));
   VkMemoryRequirements mr;
   vkGetImageMemoryRequirements(e->dev, e->img, &mr);
   VkMemoryAllocateInfo mai = {
      .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
      .allocationSize = mr.size,
      .memoryTypeIndex = find_mem_type(e, mr.memoryTypeBits,
                                       VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT),
   };
   VKCHECK(vkAllocateMemory(e->dev, &mai, NULL, &e->imgmem));
   VKCHECK(vkBindImageMemory(e->dev, e->img, e->imgmem, 0));

   VkImageViewCreateInfo vci = {
      .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
      .image = e->img,
      .viewType = VK_IMAGE_VIEW_TYPE_2D,
      .format = IMGFMT,
      .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1},
   };
   VKCHECK(vkCreateImageView(e->dev, &vci, NULL, &e->view));

   /* render pass: CLEAR magenta -> STORE */
   VkAttachmentDescription ad = {
      .format = IMGFMT,
      .samples = VK_SAMPLE_COUNT_1_BIT,
      .loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR,
      .storeOp = VK_ATTACHMENT_STORE_OP_STORE,
      .stencilLoadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE,
      .stencilStoreOp = VK_ATTACHMENT_STORE_OP_DONT_CARE,
      .initialLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
      .finalLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
   };
   VkAttachmentReference ar = {0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
   VkSubpassDescription sp = {
      .pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS,
      .colorAttachmentCount = 1,
      .pColorAttachments = &ar,
   };
   VkRenderPassCreateInfo rpci = {
      .sType = VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO,
      .attachmentCount = 1,
      .pAttachments = &ad,
      .subpassCount = 1,
      .pSubpasses = &sp,
   };
   VKCHECK(vkCreateRenderPass(e->dev, &rpci, NULL, &e->rp));

   VkFramebufferCreateInfo fci2 = {
      .sType = VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO,
      .renderPass = e->rp,
      .attachmentCount = 1,
      .pAttachments = &e->view,
      .width = W,
      .height = H,
      .layers = 1,
   };
   VKCHECK(vkCreateFramebuffer(e->dev, &fci2, NULL, &e->fb));

   buf_create(e, &e->readbuf, &e->rmem, (VkDeviceSize)4 * W * H,
              VK_BUFFER_USAGE_TRANSFER_DST_BIT, 1);
}

/* --- barriers ---------------------------------------------------------- */
static inline void
image_barrier(struct env *e, VkImageLayout oldl, VkImageLayout newl,
              VkAccessFlags src, VkAccessFlags dst)
{
   VkImageMemoryBarrier b = {
      .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
      .srcAccessMask = src,
      .dstAccessMask = dst,
      .oldLayout = oldl,
      .newLayout = newl,
      .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
      .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
      .image = e->img,
      .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1},
   };
   vkCmdPipelineBarrier(e->cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                        VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0, NULL, 0,
                        NULL, 1, &b);
}

static inline void
begin_renderpass(struct env *e, uint32_t clear_u32)
{
   image_barrier(e, VK_IMAGE_LAYOUT_UNDEFINED,
                 VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL, 0,
                 VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT);
   VkClearColorValue c;
   /* LE u32 -> RGBA bytes */
   c.float32[0] = ((clear_u32 >> 0) & 0xff) / 255.0f;
   c.float32[1] = ((clear_u32 >> 8) & 0xff) / 255.0f;
   c.float32[2] = ((clear_u32 >> 16) & 0xff) / 255.0f;
   c.float32[3] = ((clear_u32 >> 24) & 0xff) / 255.0f;
   VkClearValue cv = {.color = c};
   VkRenderPassBeginInfo rb = {
      .sType = VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO,
      .renderPass = e->rp,
      .framebuffer = e->fb,
      .renderArea = {{0, 0}, {W, H}},
      .clearValueCount = 1,
      .pClearValues = &cv,
   };
   vkCmdBeginRenderPass(e->cmd, &rb, VK_SUBPASS_CONTENTS_INLINE);
}

static inline void
end_renderpass(struct env *e)
{
   vkCmdEndRenderPass(e->cmd);
   image_barrier(e, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
                 VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                 VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT,
                 VK_ACCESS_TRANSFER_READ_BIT);
   VkBufferImageCopy ic = {
      .bufferOffset = 0,
      .imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
      .imageOffset = {0, 0, 0},
      .imageExtent = {W, H, 1},
   };
   vkCmdCopyImageToBuffer(e->cmd, e->img, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                          e->readbuf, 1, &ic);
}

static inline void
submit_wait(struct env *e)
{
   VkSubmitInfo si = {
      .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
      .commandBufferCount = 1,
      .pCommandBuffers = &e->cmd,
   };
   VKCHECK(vkQueueSubmit(e->queue, 1, &si, e->fence));
   VKCHECK(vkWaitForFences(e->dev, 1, &e->fence, VK_TRUE, ~(uint64_t)0));
   VKCHECK(vkResetFences(e->dev, 1, &e->fence));
   VKCHECK(vkResetCommandBuffer(e->cmd, 0));
}

static inline void *
readback(struct env *e)
{
   void *p;
   VKCHECK(vkMapMemory(e->dev, e->rmem, 0, (VkDeviceSize)4 * W * H, 0, &p));
   return p;
}

/* --- pipeline ---------------------------------------------------------- */
struct stagex {
   VkShaderStageFlagBits stage;
   const void *code;
   uint32_t len; /* bytes */
};

static inline VkPipeline
make_pipeline(struct env *e, const struct stagex *st, uint32_t n,
              VkDescriptorSetLayout *dsls, uint32_t ndsls,
              VkPipelineLayout *out_layout)
{
   VkShaderModule mods[4] = {0};
   VkPipelineShaderStageCreateInfo ssi[4];
   memset(ssi, 0, sizeof(ssi));
   if (n > 4) exit(2);
   for (uint32_t i = 0; i < n; i++) {
      VkShaderModuleCreateInfo mci = {
         .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
         .codeSize = st[i].len,
         .pCode = st[i].code,
      };
      VKCHECK(vkCreateShaderModule(e->dev, &mci, NULL, &mods[i]));
      ssi[i].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
      ssi[i].stage = st[i].stage;
      ssi[i].module = mods[i];
      ssi[i].pName = "main";
   }

   VkPipelineLayoutCreateInfo plci = {
      .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
      .setLayoutCount = ndsls,
      .pSetLayouts = dsls,
   };
   VkPipelineLayout layout;
   VKCHECK(vkCreatePipelineLayout(e->dev, &plci, NULL, &layout));
   if (out_layout)
      *out_layout = layout;

   VkVertexInputBindingDescription vb = {0, 8,
                                         VK_VERTEX_INPUT_RATE_VERTEX};
   VkVertexInputAttributeDescription va = {
      0, 0, VK_FORMAT_R32G32_SFLOAT, 0};
   VkPipelineVertexInputStateCreateInfo vi = {
      .sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO,
      .vertexBindingDescriptionCount = 1,
      .pVertexBindingDescriptions = &vb,
      .vertexAttributeDescriptionCount = 1,
      .pVertexAttributeDescriptions = &va,
   };
   VkPipelineInputAssemblyStateCreateInfo ia = {
      .sType = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO,
      .topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST,
   };
   VkViewport vp = {0, 0, (float)W, (float)H, 0, 1};
   VkRect2D sc = {{0, 0}, {W, H}};
   VkPipelineViewportStateCreateInfo vs = {
      .sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO,
      .viewportCount = 1,
      .pViewports = &vp,
      .scissorCount = 1,
      .pScissors = &sc,
   };
   VkPipelineRasterizationStateCreateInfo rs = {
      .sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO,
      .polygonMode = VK_POLYGON_MODE_FILL,
      .cullMode = VK_CULL_MODE_BACK_BIT,
      .frontFace = VK_FRONT_FACE_COUNTER_CLOCKWISE,
      .lineWidth = 1.0f,
   };
   /* PROBE_CULL=none|front|back — A/B отсечения граней без пересборки.
    * Гипотеза: инвертированный знак лицевости на этой машине → все
    * треугольники в backface → VS жив, фрагментов нет (симптом probe10). */
   const char *cull = getenv("PROBE_CULL");
   if (cull && !strcmp(cull, "none"))
      rs.cullMode = VK_CULL_MODE_NONE;
   else if (cull && !strcmp(cull, "front"))
      rs.cullMode = VK_CULL_MODE_FRONT_BIT;
   VkPipelineMultisampleStateCreateInfo ms = {
      .sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO,
      .rasterizationSamples = VK_SAMPLE_COUNT_1_BIT,
   };
   VkPipelineColorBlendAttachmentState ca = {.colorWriteMask = 0xf};
   VkPipelineColorBlendStateCreateInfo cb = {
      .sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO,
      .attachmentCount = 1,
      .pAttachments = &ca,
   };
   VkGraphicsPipelineCreateInfo gci = {
      .sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO,
      .stageCount = n,
      .pStages = ssi,
      .pVertexInputState = &vi,
      .pInputAssemblyState = &ia,
      .pViewportState = &vs,
      .pRasterizationState = &rs,
      .pMultisampleState = &ms,
      .pColorBlendState = &cb,
      .layout = layout,
      .renderPass = e->rp,
      .subpass = 0,
   };
   VkPipeline pipe;
   VKCHECK(vkCreateGraphicsPipelines(e->dev, VK_NULL_HANDLE, 1, &gci, NULL,
                                     &pipe));
   for (uint32_t i = 0; i < n; i++)
      vkDestroyShaderModule(e->dev, mods[i], NULL);
   return pipe;
}

static inline VkDescriptorSet
ssbo_set(struct env *e, VkDescriptorSetLayout dsl, VkPipelineLayout layout,
         VkBuffer buf, VkDeviceSize size, VkShaderStageFlags stages)
{
   VkDescriptorSetLayout dsls[1] = {dsl};
   VkDescriptorSetAllocateInfo dai = {
      .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
      .descriptorPool = 0,
      .descriptorSetCount = 1,
      .pSetLayouts = dsls,
   };
   (void)layout;
   (void)stages;
   (void)buf;
   (void)size;
   (void)dai;
   return VK_NULL_HANDLE; /* см. pool_create/ssbo_set_pool ниже */
}

struct pool_env {
   VkDescriptorPool pool;
};

static inline VkDescriptorSet
ssbo_set_pool(struct env *e, VkDescriptorSetLayout dsl, VkBuffer buf,
              VkDeviceSize size, VkShaderStageFlags stages)
{
   VkDescriptorPoolSize ps = {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 4};
   VkDescriptorPoolCreateInfo pci = {
      .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
      .maxSets = 4,
      .poolSizeCount = 1,
      .pPoolSizes = &ps,
   };
   static VkDescriptorPool pool = VK_NULL_HANDLE;
   if (!pool)
      VKCHECK(vkCreateDescriptorPool(e->dev, &pci, NULL, &pool));
   VkDescriptorSetLayout l = dsl;
   VkDescriptorSetAllocateInfo dai = {
      .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
      .descriptorPool = pool,
      .descriptorSetCount = 1,
      .pSetLayouts = &l,
   };
   VkDescriptorSet set;
   VKCHECK(vkAllocateDescriptorSets(e->dev, &dai, &set));
   VkDescriptorBufferInfo bi = {buf, 0, size};
   VkWriteDescriptorSet wr = {
      .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
      .dstSet = set,
      .dstBinding = 0,
      .descriptorCount = 1,
      .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
      .pBufferInfo = &bi,
   };
   (void)stages;
   vkUpdateDescriptorSets(e->dev, 1, &wr, 0, NULL);
   return set;
}

#endif /* PCOMMON_H */
