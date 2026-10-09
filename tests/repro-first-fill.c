/* Reproducer for the defect "the first fill loses part of the data"
 * (docs/analysis.md, section 20).
 *
 * One question only: does the driver write the whole image on the first
 * vkCmdCopyBufferToImage into a freshly created vkImage.
 *
 *   ./repro-first-fill [reps] [cold|warm]
 *
 *   reps  how many times to run (default 4)
 *   cold  one fill, then measurement          -- the defect reproduces
 *   warm  two fills in a row, measurement after    -- no defect
 *
 * For every (rep, tiling) a fresh image and fresh memory are created, otherwise
 * the second run lands in a warm image and yields 0 mismatches regardless of
 * the mode. The format is fixed to R8G8B8A8_UNORM: the defect depends neither on the format
 * nor on UBWC (it reproduces on LINEAR as well).
 *
 * Expected on turnip/Adreno 740:
 *   cold: rep0 bad=5056/262144, rep1.. bad=0    (the count is always a multiple of 64)
 *   warm: all reps bad=0
 */
#include <vulkan/vulkan.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define W 512
#define H 512

int main(int argc, char **argv) {
    unsigned reps = argc > 1 ? (unsigned)atoi(argv[1]) : 4;
    int warm = argc > 2 && strcmp(argv[2], "warm") == 0;

    VkApplicationInfo ai = { .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
                             .pApplicationName = "repro-first-fill",
                             .apiVersion = VK_API_VERSION_1_3 };
    VkInstanceCreateInfo ici = { .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
                                 .pApplicationInfo = &ai };
    VkInstance inst;
    if (vkCreateInstance(&ici, NULL, &inst) != VK_SUCCESS) {
        printf("no instance\n");
        return 1;
    }
    uint32_t nd = 0;
    vkEnumeratePhysicalDevices(inst, &nd, NULL);
    VkPhysicalDevice *ds = malloc(nd * sizeof(*ds));
    vkEnumeratePhysicalDevices(inst, &nd, ds);
    if (!nd) {
        printf("no devices\n");
        return 1;
    }
    VkPhysicalDevice pd = ds[0];
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(pd, &mp);

    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = { .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
                                    .queueCount = 1, .pQueuePriorities = &prio };
    VkDeviceCreateInfo dci = { .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
                               .queueCreateInfoCount = 1, .pQueueCreateInfos = &qci };
    VkDevice dev;
    if (vkCreateDevice(pd, &dci, NULL, &dev) != VK_SUCCESS) {
        printf("no device\n");
        return 1;
    }
    VkQueue q;
    vkGetDeviceQueue(dev, 0, 0, &q);

    /* HOST_VISIBLE|HOST_COHERENT: on RP6 this is unified memory, no flush needed. */
    uint32_t fsz = UINT32_MAX;
    for (uint32_t i = 0; i < mp.memoryTypeCount; i++) {
        VkMemoryPropertyFlags want = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                                     VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
        if (mp.memoryTypes[i].propertyFlags & want) {
            fsz = i;
            break;
        }
    }
    if (fsz == UINT32_MAX) {
        printf("no host-visible memory\n");
        return 1;
    }

    VkDeviceSize bytes = (VkDeviceSize)W * H * 4;
    uint32_t *pat = malloc(bytes);
    for (VkDeviceSize i = 0; i < bytes / 4; i++) {
        uint32_t w = 0x5a5a0000u ^ (uint32_t)(i * 2654435761u);
        w ^= (uint32_t)(i >> 11) << 24;
        pat[i] = w;
    }
    /* there are no zero words in pat: a fully zero word is possible only at
     * i == 0 (0x5a5a0000 ^ 0 = 0x5a5a0000), so any got == 0 —
     * is the driver writing a zero, not a match with the pattern. */

    VkBufferCreateInfo bci = { .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = bytes,
                               .usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT |
                                       VK_BUFFER_USAGE_TRANSFER_DST_BIT,
                               .sharingMode = VK_SHARING_MODE_EXCLUSIVE };
    VkBuffer fb, rb;
    VkDeviceMemory fbm, rbm;
    VkMemoryRequirements fr, rr;
    vkCreateBuffer(dev, &bci, NULL, &fb);
    vkCreateBuffer(dev, &bci, NULL, &rb);
    vkGetBufferMemoryRequirements(dev, fb, &fr);
    vkGetBufferMemoryRequirements(dev, rb, &rr);
    VkMemoryAllocateInfo mai = { .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
                                 .memoryTypeIndex = fsz };
    mai.allocationSize = fr.size;
    vkAllocateMemory(dev, &mai, NULL, &fbm);
    mai.allocationSize = rr.size;
    vkAllocateMemory(dev, &mai, NULL, &rbm);
    vkBindBufferMemory(dev, fb, fbm, 0);
    vkBindBufferMemory(dev, rb, rbm, 0);
    {
        void *p;
        vkMapMemory(dev, fbm, 0, fr.size, 0, &p);
        memcpy(p, pat, bytes);
        vkUnmapMemory(dev, fbm);
    }

    VkCommandPoolCreateInfo cpci = { .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
                                     .queueFamilyIndex = 0 };
    VkCommandPool pool;
    vkCreateCommandPool(dev, &cpci, NULL, &pool);
    VkCommandBufferAllocateInfo cbai = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
                                         .commandPool = pool,
                                         .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
                                         .commandBufferCount = 1 };
    VkCommandBuffer cb;
    vkAllocateCommandBuffers(dev, &cbai, &cb);
    VkCommandBufferBeginInfo cbbi = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
                                      .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT };

    VkImageCreateInfo ii = { .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
                             .imageType = VK_IMAGE_TYPE_2D,
                             .format = VK_FORMAT_R8G8B8A8_UNORM,
                             .extent = { W, H, 1 },
                             .mipLevels = 1,
                             .arrayLayers = 1,
                             .samples = VK_SAMPLE_COUNT_1_BIT,
                             .tiling = VK_IMAGE_TILING_OPTIMAL,
                             .usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT |
                                      VK_IMAGE_USAGE_TRANSFER_DST_BIT,
                             .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
                             .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED };
    VkImageCreateInfo ii_lin = ii;
    ii_lin.tiling = VK_IMAGE_TILING_LINEAR;
    const char *names[2] = { "OPTIMAL", "LINEAR" };
    const VkImageCreateInfo *cis[2] = { &ii, &ii_lin };

    printf("mode: %s, %ux%u, R8G8B8A8_UNORM, memoryType=%u\n", warm ? "warm" : "cold", W, H, fsz);

    size_t worst = 0;
    for (unsigned rep = 0; rep < reps; rep++) {
        for (int t = 0; t < 2; t++) {
            VkImage im;
            VkDeviceMemory imm;
            VkMemoryRequirements ir;
            vkCreateImage(dev, cis[t], NULL, &im);
            vkGetImageMemoryRequirements(dev, im, &ir);
            mai.allocationSize = ir.size;
            vkAllocateMemory(dev, &mai, NULL, &imm);
            vkBindImageMemory(dev, im, imm, 0);

            vkResetCommandPool(dev, pool, 0);
            vkBeginCommandBuffer(cb, &cbbi);
            VkImageSubresourceRange full = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
            VkImageMemoryBarrier b1 = { .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
                                        .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
                                        .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED,
                                        .newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                                        .image = im, .subresourceRange = full };
            vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                                 VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 0, NULL, 1, &b1);
            VkBufferImageCopy bic = { .bufferOffset = 0,
                                      .bufferRowLength = W,
                                      .bufferImageHeight = 0,
                                      .imageSubresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 },
                                      .imageOffset = { 0, 0, 0 },
                                      .imageExtent = { W, H, 1 } };
            vkCmdCopyBufferToImage(cb, fb, im, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &bic);
            if (warm) {
                VkImageMemoryBarrier bw = { .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
                                            .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
                                            .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
                                            .oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                                            .newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                                            .image = im, .subresourceRange = full };
                vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT,
                                     VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 0, NULL, 1, &bw);
                vkCmdCopyBufferToImage(cb, fb, im, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &bic);
            }
            VkImageMemoryBarrier b2 = { .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
                                        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
                                        .oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                                        .newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                                        .image = im, .subresourceRange = full };
            vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT,
                                 VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 0, NULL, 1, &b2);
            vkCmdCopyImageToBuffer(cb, im, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, rb, 1, &bic);
            vkEndCommandBuffer(cb);

            VkSubmitInfo si = { .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
                                .commandBufferCount = 1, .pCommandBuffers = &cb };
            vkQueueSubmit(q, 1, &si, NULL);
            vkQueueWaitIdle(q);

            uint32_t *got;
            vkMapMemory(dev, rbm, 0, rr.size, 0, (void **)&got);
            size_t bad = 0, zero = 0, first = (size_t)-1;
            for (size_t i = 0; i < bytes / 4; i++) {
                if (got[i] == 0)
                    zero++;
                if (got[i] != pat[i]) {
                    if (first == (size_t)-1)
                        first = i;
                    bad++;
                }
            }
            printf("rep%u %-8s memreq=0x%llx: bad=%zu/%zu zero=%zu%s\n", rep, names[t],
                   (unsigned long long)ir.size, bad, (size_t)(bytes / 4), zero,
                   first == (size_t)-1 ? "" : " (first bad word present)");
            if (bad > worst)
                worst = bad;
            vkUnmapMemory(dev, rbm);

            vkDestroyImage(dev, im, NULL);
            vkFreeMemory(dev, imm, NULL);
        }
    }
    printf("worst run: %zu mismatches\n", worst);
    return 0;
}
