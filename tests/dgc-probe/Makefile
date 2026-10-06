# tests/dgc-probe — стенд-пробы DGC/Starfield (docs/dgc-plan.md §9).
# Всё собирается в podman (хост без gcc/vulkan-headers); загрузчик Vulkan
# подмонтируется с хоста, чтобы DT_NEEDED=libvulkan.so.1 резолвился при
# запуске на хосте.
POD_GLSLANG = podman run --rm -v $(CURDIR):/src:Z \
              localhost/mesa-build-fedora44-glslang
POD_BUILD = podman run --rm -v $(CURDIR):/src:Z \
              -v /usr/lib64/libvulkan.so.1:/opt/vk/libvulkan.so.1:ro \
              localhost/mesa-build-fedora44-full
CC_IN = gcc -O2 -Wall

ICD ?= /var/home/armada/opencode/build/out/freedreno_icd.json
VKENV = VK_DRIVER_FILES=$(ICD)

PROBES = probe0d probe7d probe9 probe10 probe11 probe12

all: $(PROBES)

# --- шейдеры ---
shaders/%.spv: shaders/%.vert
	$(POD_GLSLANG) glslangValidator -V /src/$< -o /src/$@

shaders/%.spv: shaders/%.comp
	$(POD_GLSLANG) glslangValidator -V /src/$< -o /src/$@

shaders/%.spv: shaders/%.frag
	$(POD_GLSLANG) glslangValidator -V /src/$< -o /src/$@

%_spv.h: shaders/%.spv spv2h.py
	python3 spv2h.py $< $@ $(shell echo $* | tr '[:lower:]' '[:upper:]')_SPV

# --- пробы ---
probe0d: probe0d.c pcommon.h vs_full_spv.h fs_bands_spv.h
	$(POD_BUILD) $(CC_IN) -o /src/$@ /src/probe0d.c /opt/vk/libvulkan.so.1

probe7d: probe7d.c pcommon.h cp_spv.h
	$(POD_BUILD) $(CC_IN) -o /src/$@ /src/probe7d.c /opt/vk/libvulkan.so.1

probe9: probe9.c pcommon.h vs_ssb_spv.h fs_bands_spv.h
	$(POD_BUILD) $(CC_IN) -o /src/$@ /src/probe9.c /opt/vk/libvulkan.so.1

probe10: probe10.c pcommon.h vs_ssb_spv.h fs_ssb_spv.h
	$(POD_BUILD) $(CC_IN) -o /src/$@ /src/probe10.c /opt/vk/libvulkan.so.1

probe11: probe11.c pcommon.h vs_full_spv.h fs_green_spv.h
	$(POD_BUILD) $(CC_IN) -o /src/$@ /src/probe11.c /opt/vk/libvulkan.so.1

# probe12: дискриминатор «машина/пробы» — spec-ветер + дефолтный CULL_BACK
probe12: probe12.c pcommon.h vs_full_spv.h fs_bands_spv.h
	$(POD_BUILD) $(CC_IN) -o /src/$@ /src/probe12.c /opt/vk/libvulkan.so.1

# --- прогоны ---
run0: probe0d
	$(VKENV) ./probe0d

run7: probe7d
	$(VKENV) ./probe7d

run9: probe9
	$(VKENV) ./probe9

run10: probe10
	$(VKENV) ./probe10

run11: probe11
	$(VKENV) ./probe11

run12: probe12
	$(VKENV) ./probe12

clean:
	rm -f $(PROBES) *_spv.h shaders/*.spv

.PHONY: all clean run0 run7 run9 run10 run11 run12
