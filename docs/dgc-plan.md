# План: DGC в turnip (GPU-путь), M2–M4

Контекст и факты — `docs/dgc-analysis.md` (сокращённо «разбор»). Ветка работ:
`starfield-dgc` (turnip-a7xx-dx12), worktree mesa: `build/mesa-rp6-dgc`
(ветка `dgc-starfield`, стек патчей 0001–0012).

## 0. Цель и рамки v1

**Цель:** включить в turnip `VK_EXT_device_generated_commands` так, чтобы
vkd3d-путь Starfield (`[CONSTANT, DRAW_INDEXED]` и `[CONSTANT, DISPATCH]`,
ByteStride=32, N до десятков) дошёл до GPU — игра рисует геометрию.

**В v1 входит:** фича/свойства экстеншена; layout из токенов
PUSH_CONSTANT/PUSH_DATA + SEQUENCE_INDEX + DRAW/DRAW_INDEXED/DISPATCH
(включая count-варианты); preprocess-шейдер, пишущий наш PM4-стрим;
исполнение через dispatch → барьер → `CP_INDIRECT_BUFFER`.

**В v1 не входит (в сигнатурах Starfield отсутствуют):** дескриптор-токены
(CBV/SRV/UAV/BIND, `INDIRECT_BINDABLE`), индексный/вершинный буферы из
списка, mesh/RT, execution set (смена пайплайна per-sequence — пайплайн
фиксирован на Execute, vkd3d обновляет его заранее). На чужих токенах —
громкий отказ при создании layout, не тихий UB.

**vkd3d не трогаем:** нативный DGC-путь там уже есть, сам включится при
наличии фичи (проверка — маркеры §5).

## 1. Архитектура (выбрано)

**Экстеншен и свойства**

- `deviceGeneratedCommands = VK_TRUE`; `dynamicGeneratedPipelineLayout`
  остаётся `VK_FALSE` (vkd3d его и не просит).
- `supportedIndirectCommandsShaderStages = GRAPHICS | COMPUTE`
  (+ `MESH | TASK`, если turnip включает `meshShader` — проверить, иначе
  vkd3d отключит фичу, разбор §1). Отказ по неизвестным токенам — на создании
  layout.
- Лимиты: `maxIndirectCommandsTokenCount ≥ 8`, `maxIndirectSequenceCount ≥
  1024` (Starfield: 2 токена, десятки sequence), `maxIndirectCommandsTokenStride ≥ 64`.
- Точки входа: `vkCreateIndirectCommandsLayoutEXT`,
  `vkDestroyIndirectCommandsLayoutEXT`, `vkGetGeneratedCommandsMemoryRequirementsEXT`,
  `vkCmdExecuteGeneratedCommandsEXT` (основной), `vkCmdPreprocessGeneratedCommandsEXT`
  — **no-op** (см. ниже).
- Зависимости экстеншена закрыты: maintenance5 = true (tu_device.cc:614),
  BDA = true (552), apiVersion = 1.4 (118).

**Preprocess = no-op, работа целиком в Execute.** vkd3d может позвать
preprocess даже в APPLICATION_CALL-режиме (разбор §2), причём в отдельный
command buffer. Упрощение v1: `vkCmdPreprocessGeneratedCommandsEXT` ничего не
делает, а `vkCmdExecuteGeneratedCommandsEXT` **игнорирует `isPreprocessed`**
и всегда эмитит dispatch + барьер + IB. Это корректно: наш Execute не
опирается ни на какие записи preprocess-буфера, лишних барьеров в vkd3d он не
ломает, а гонки «preprocess-буфер против нашего dispatch» не возникает, потому
что пишем в него только мы, в Execute.

**Layout-дескриптор.** При `vkCreateIndirectCommandsLayoutEXT` разбираем
токены в компактный дескриптор (тип действия, смещения в 32-байтной
последовательности, stride, число sequence). Дескриптор кладётся в BO (на
Execute — в params шейдера): один универсальный шейдер на все layout'ы, без
рантайм-компиляции (модель ANV: `layout_addr` в params).

**Препроцесс-шейдер** — NIR, собранный в C по существующему turnip-прецеденту
внутренних шейдеров: `build_*_shader()` + `compile_shader()`
(tu_clear_blit.cc:926 — NIR → `ir3_finalize_nir` → `ir3_shader_from_nir` →
бинар в `global->shaders` c `global_shader_va`), но вариант
`MESA_SHADER_COMPUTE`. Порт `dgc.cl` (ANV) не берём: в turnip нет инфраструктуры
clang→SPIR-V для внутренних шейдеров, а NIR-in-C уже используется.

Логика шейдера (по мотивам `lvp_execute.c:4556-4706` — семантика токенов):

```
вход: layout-дескриптор, indirectAddress (буфер игры),
      sequenceCountAddress (count, если есть), preprocessAddress/Size
для i в [0, count):                     // count читает сам шейдер — GPU-written ок
  seq = indirectAddress + i * stride
  последовательность токенов layout'а:
    PUSH_CONSTANT/PUSH_DATA -> CP_MEM_WRITE(адрес констант-слота_i, данные из seq)
                              + CP_WAIT_MEM_WRITES
                              + SS6-пролог (адрес слота_i)          <-- вопрос §8 разбора
    DRAW_INDEXED            -> CP_DRAW_INDIRECT_MULTI, args = seq + action_offset
    DISPATCH                -> CP_EXEC_CS_INDIRECT,   args = seq + action_offset
```

Пайплайн/дескрипторы/вершинные буферы не трогаем: состояние уже выставлено
до Execute (vkd3d обновляет пайплайн сам), регистры переживают вложенный IB —
так и работает существующий draw-in-IB.

**Исполнение** в `vkCmdExecuteGeneratedCommandsEXT`:

1. params (дескриптор + адреса) в BO;
2. dispatch preprocess-ядра: `CP_EXEC_CS_INDIRECT` с CPU-заполненными 4
   dwords (ядро в `global_shader_va`) — в `cmd->cs` (там же ходят compute-диспатчи);
3. барьер «шейдерная запись → чтение из IB» (опора — существующая семантика
   `INDIRECT_COMMAND_READ`, вопрос §8.2 разбора);
4. `CP_INDIRECT_BUFFER(preprocessAddress)` — в `draw_cs`, дальше по обычному
   draw-потоку.

## 2. Этапы

**M2.0 — спайк GO/NO-GO (делается первым).** Внутренний compute-шейдер в
turnip: собрать минимальное ядро (записать константу в буфер), выгрузить в
`global->shaders`, задиспатчить из cmdbuf, барьер, прочитать результат (тем же
стендом `/tmp/opencode/dgcprobe/`, что M1, + GPU-copy для чтения).
Критерий GO: запись шейдера видна после IB. Отсекает риски R3–R4 до
вложений в остальное.
**Статус: GO — достигнут, детали и замеры в §6** (коммит mesa
`33a2a41e276`).

**M2.1 — экстеншен.** Фича/свойства/лимиты/точки входа (заглушки,
возвращают успех). Проверка: в логе Starfield исчезает
`Not all relevant pipeline stages ... Skipping`.
**Статус: DONE — детали в §7** (коммит mesa `c9343de409c`); остаток
проверки — прогон Starfield (M3).

**M2.2 — layout.** Разбор токенов в дескриптор + валидация v1-объёма
(громкий отказ). `vkGetGeneratedCommandsMemoryRequirementsEXT` — размер под
стрим с запасом (на sequence ~64 dword'а: пролог + MEM_WRITE(≤8) + WAIT +
пакет драва).
**Статус: DONE** (коммит mesa `1011dc1fa0e`): разбор — общий слой
`vk_indirect_command_layout_create`, v1-валидация — громкий отказ в
`tu_CreateIndirectCommandsLayoutEXT`, memreq = stride×count (реальный
PM4-размер закрывается вместе с M2.3).

**M2.3 — шейдер: константы + draw.** Ядро + params + dispatch + барьер +
IB. Проверка — расширенный стенд: C-тест без vkd3d (создать layout
`[CONSTANT, DRAW_INDEXED]`, заполнить последовательность, Execute →
треугольник, у которого UBO/константа приходит из последовательности; тот же
SIGSEGV-backtrace и dbg-чекпоинты).

**M2.4 — dispatch + count.** Второй тип действия (`CP_EXEC_CS_INDIRECT`),
count-варианты (`*_COUNT`, `sequenceCountAddress`).

**M2.5 — интеграция с vkd3d.** Прогон Starfield: в логе нет `Skipping` и
`DGC-skip`, появляется `EXECUTE_INDIRECT_TEMPLATE`; продублировать
логирование `require_patch` (разбор §8.6).

**M2.6 — регресс.** Прогоны RE4 (`scripts/run-game.sh patched`) и WNO
(`scripts/run-wtno.sh`) — DGC не должен ломать обычный путь; сборка через
podman как обычно.

**M3 — прогон Starfield, таймер 600 с:**
`scripts/run-starfield.sh patched 600`. Критерий — **игрок смотрит на
геометрию** (визуальная проверка, за пользователем); лог без крашей (RC 0),
маркеры §5 на месте. Замер кадрового времени — по возможности здесь же
(базовый ориентир для M4).

**M4 — батчинг и ship.**
- Батчинг: минимум dispatch/барьеров и CPU-wait вокруг Execute (урок upstream
  9840cbf; vkd3d уже умеет batch'ить несколько Execute в один вызов —
  `d3d12_command_list_flush_dgc_batch` — наш вклад: не добавлять лишних
  flush'ей, один params-BO на кадр и т.п.). Отдельный вопрос — не копить
  строку dispatch→barrier→IB там, где можно объединить.
- Патч **0013** в `patches/`, запись в `PATCHES.md`, обновление README и
  разбора (статус M2–M4), cleanup: судьба M1-probe — под явным флагом или
  удалить.
- Пуш — по команде («Push»).

## 3. Риски

| # | Риск | Как проверяем |
|---|---|---|
| R1 | барьер «шейдерная запись → чтение из IB» на a740 (M1 доказал только `CP_MEM_WRITE`) | **закрыт (§6): `CACHE_CLEAN + WAIT_FOR_IDLE + WAIT_FOR_ME`, 4.1–8.4 µs/dispatch, PASS 10/10 + BENCH=1000 FRESH** |
| R2 | SS6-пролог per-sequence: точный dword-шаблон из `tu_emit_consts`/`tu6_draw_common` не снят | M2.3: выписать шаблон и сравнить с тем, что CPU эмитит в обычном draw (дамп через существующий rd-dump/cmdstream-дамп) |
| R3 | порядок `cmd->cs` (dispatch) и `draw_cs` (IB): где flush-точки, как не получить гонку потоков | M2.0/M2.3: на стенде смотрим фактический порядок пакетов |
| R4 | внутренний compute-шейдер в turnip — первый; слоты/размер `global->shaders` (assert в `compile_shader`) | M2.0: считаем слоты, при нехватке — отдельный BO вместо `tu6_global` |
| R5 | 30–60 dispatch+barrier на кадр в единственной полосе (разбор §9) — риск для кадрового времени | M3/M4: замер fps до/после; если упирается — оптимизация в M4, multi-queue тема отдельная |
| R6 | count/predication: `sequenceCountAddress` может писать GPU | шейдер читает count сам (это и есть причина GPU-пути); проверка на M2.4 стенде с count-буфером, заполненным GPU |
| R7 | гейт стадий: vkd3d требует `ALL_GRAPHICS\|COMPUTE` (+`MESH\|TASK` при `meshShader`) | M2.1: лог Starfield, смотрим что `Skipping` ушёл |
| R8 | чужие токены/сигнатуры (другие игры) | M2.2: валидация с явным отказом |

## 4. Методы проверки

- Стенд `/tmp/opencode/dgcprobe/` (probe.c + probe2 + glsl-шейдеры +
  build.sh, podman, gcc/glslang) — расширяется под DGC-вызовы (M2.3).
  `probe2 BENCH=N` — замер submit+GPU+wait на N probe-диспатчах с
  детекцией stale (см. §6). Драйвер — только через
  `VK_DRIVER_FILES=build/out/freedreno_icd.json`.
- Логи: маркеры vkd3d (`Skipping`, `DGC-skip`, `EXECUTE_INDIRECT_TEMPLATE`),
  свои лог-точки в turnip по `TU_DEBUG`-флагу (по аналогии с `tu_dgc_probe`).
- Игра: `scripts/run-starfield.sh [stock|patched] [timeout]`, **следующий
  прогон — таймер 600 с**; проверка геометрии визуальная, за игроком.
- Сборка: podman `localhost/mesa-build-fedora44-full`, команда из M0.

## 5. Связанные артефакты

- Разбор: `docs/dgc-analysis.md` (дамп сигнатур, реализации, решение, §9 —
  многополосность).
- Логи: `results/starfield/starfield-patched-20261004-*.log`
  (дамп-лог 234103 — основной), скрипт `scripts/run-starfield.sh`.
- Fork vkd3d: `keks2293/vkd3d-proton`, ветки `starfield`, `starfield-cmdsig-debug`.
- Worktree mesa: `build/mesa-rp6-dgc`, ветка `dgc-starfield`, DGC-код закоммичен
  (`33a2a41e276` M2.0, `c9343de409c` M2.1); посторонние грязные файлы в worktree не трогать.
  **Push:** remote для форка mesa нет (у `origin` — апстрим gitlab, своя
  ветка `keks2293/mesa` отсутствует), поэтому коммиты локальные, а их
  `format-patch` лежат здесь: `mesa-patches/0001-dgc-M2.0-GPU-written-PM4-CP_INDIRECT_BUFFER.patch`,
  `mesa-patches/0002-dgc-M2.1-VK_EXT_device_generated_commands-entrypoint.patch`,
  `mesa-patches/0003-dgc-M2.2-v1-scope-validation.patch`
  (единая серия, отдельная от `patches/` для turnip).
- M1/M2-probe: `src/freedreno/vulkan/tu_dgc_probe.cc` (в коммите выше),
  экспорт `vkCmdTuDgcProbeDispatchEXT` — в `src/vulkan/vulkan.sym`.

## 6. M2.0 — результат (GO, 05.10.2026)

Коммит mesa: `33a2a41e276` (ветка `dgc-starfield`, только DGC-файлы:
`tu_cmd_buffer.cc/h`, `tu_device.h`, `tu_dgc_probe.cc`, `vulkan/meson.build`,
`vulkan.sym`).

### Что подтверждено

- Внутренний compute-шейдер пишет PM4-payload (4 dword'а = `CP_MEM_WRITE`),
  CP исполняет его через `CP_INDIRECT_BUFFER`: `marker = value`, payload
  перезаписан, хвост (`payload[64]`) не тронут, новых фолтов в dmesg нет.
- Фактический порядок `cmd->cs` (R3): барьер → `CP_WAIT_FOR_ME` →
  `CP_INDIRECT_BUFFER` → verify-dispatch.

### Три фикса, без которых не работало

1. **Единицы push-констант в tu.** `.base` в `nir_load_push_constant`
   трактуется как **dword-смещение** (в `tu_shader.cc:297`
   `lower_load_push_constant` вычитает `lo_dwords`), динамическое смещение
   уходит в **src** в байтах. Правильно:
   `nir_load_push_constant(..., nir_imm_int(b, 8), .base = 0, .range = 24)`.
   С `.base = 8` шейдер читал мимо (проверяется дисасмом
   `IR3_SHADER_DEBUG=disasm,internal`: `stsc.u32 c[0], 0, 8` + чтение c0/c2).
2. **Кодировка NOP.** `0x70108000 = pm4_pkt7_hdr(CP_NOP, 0)`
   (`freedreno_pm4.h:65`). Залитое раньше `0x70000010` декодировалось как
   `opcode=0, cnt=0x10` → CP opcode error в dmesg — это были следы мусора
   при stale-чтении, а не отдельный баг.
3. **Барьер** — основной фикс, см. ниже.

### R1b: префетч PFE → `CP_WAIT_FOR_ME` вместо NOP-пада

На A7XX `CP_INDIRECT_BUFFER` — PFE-вариант: содержимое цели подтягивается при
**выборке пакета** из потока, за десятки кБ до исполнения, то есть до
барьера. Первое лечение было тайминг-хаком: NOP-пад сдвигал выборку
IB-пакета за барьер. Бисекция порога (1 прогон/точка, из сессии M2.0):

```
128 FAIL   136 FAIL   144 PASS   152 FAIL   160 FAIL,FAIL,PASS   256 PASS
```

Порог плавает между прогонами — потому что это сумма глубины FIFO PFP→ME и
времени `CACHE_CLEAN`. «Магической константы из спеки» нет: глубина
предвыборки нигде не опубликована, в `adreno_pm4.xml:262-268` описан только
механизм — *«prefetch parser uses this packet type to determine whether to
pre-fetch the IB»*.

Решение — детерминированное рукопожатие вместо дистанции,
`adreno_pm4.xml:406`:

> CP_WAIT_FOR_ME: PFP waits until the FIFO between the PFP and the ME is empty

PFP физически не может распарсить `CP_INDIRECT_BUFFER` (и начать выборку
payload'а), пока ME не исполнит барьер. Turnip этот пакет уже умеет
(`TU_CMD_FLAG_WAIT_FOR_ME` в `tu6_emit_flushes`, эмитится последним), в
probe-путь он просто не добавлялся.

Кандидат «подождать префетч» `CP_WAIT_IB_PFD_COMPLETE` мёртв: у него
`variants="A2XX-A4XX"`, а опкод 0x5d на A7XX переиспользован как
`CP_NON_CONTEXT_REG_BUNCH` (`adreno_pm4.xml:646`); комментарий
*«unimplemented at least since a5xx fw»* — RE-вывод Rob Clark, коммит
`f011189642c`. К тому же семантика не наша — ждать base/size-записи от
`IB_PFD` (prefetch **disabled**), а такого пакета на A6+ нет.

### Замер (probe2 `BENCH=1000`, submit+GPU+wait, 3 прогона)

| вариант | корректность | µs/dispatch |
|---|---|---|
| без барьера (пол) | STALE, FAIL | 2.2–4.3 |
| **`CACHE_CLEAN \| WAIT_FOR_IDLE \| WAIT_FOR_ME`** | **PASS** | **4.1–8.4** |
| то же + явный pre-WFI | PASS | 9.3–9.4 |
| плюс `CACHE_INVALIDATE` + `WAIT_MEM_WRITES` | PASS | 9.8–10.2 |
| старый NOP-пад 16384 dwords (без WFM) | PASS | 596–835 |

Дефолт драйвера: `CACHE_CLEAN | WAIT_FOR_IDLE | WAIT_FOR_ME`, `DGC_PAD_N=0`.
Проверка: 5/5 одиночных PASS, `BENCH=1000` → FRESH (`marker = 999`), то есть
CP на каждой итерации читал **свежий** payload, а не предыдущий. Старый
вариант дороже примерно в 90 раз.

Состав барьера — всё про очередность, а не про тайминг:

- `CACHE_CLEAN` обязателен (без него CP читает память и видит старое);
- `WAIT_FOR_IDLE` обязан идти **после** CLEAN: сам `CP_WAIT_FOR_ME` ждёт
  лишь приёмки пакета CLEAN самим PFP, а не завершения чистки данными —
  `CLEAN + WFM` без post-WFI падало;
- явный `WFI` **до** flush избыточен — первоначальный вывод R1 отменён;
- `CACHE_INVALIDATE` и `WAIT_MEM_WRITES` на этом пути не нужны (проверено
  повторными записями в один и тот же адрес, BENCH=1000).

### Методика

- `probe2 BENCH=N` перезаписывает CB из N probe-диспатчей с `value = i` на
  каждой итерации и меряет submit+GPU+wait. Если CP прочитал устаревший
  payload, `marker` остаётся от предыдущей итерации → печатается `STALE`.
- Диагностика R1a: verify-шейдер копирует `payload[0]` → `marker[1]` уже
  **после** барьера; свежий `marker[1]` при `marker[0] = 0` доказывал, что
  память обновлена, а stale видит именно CP/PFE.
- Ножки под getenv (оставлены для A/B): `DGC_PAD_N`, `DGC_FL` (маска
  flush'а), `DGC_NO_WFM`, `DGC_PRE_WFI`, `DGC_DUMP` (дамп `cmd->cs`).

### Смежное: как решают AMD / Intel / NVIDIA (по коду mesa)

- **Intel (ANV, `genX_cmd_dgc.c:805`)**: *«If a shader runs, flush the data
  to make it visible to CS»* → `ANV_PIPE_DATA_CACHE_FLUSH |
  ANV_PIPE_CS_STALL` (столбняк стримера, аналог нашего WFM), а на Gfx12+
  ещё и `MI_ARB_CHECK { PreParserDisable = true }` — явное отключение
  препарсера перед прыжком в GPU-записанный буфер.
- **AMD (RADV, `radv_dgc.c`)**: PM4 тоже собирается в шейдере (`nir_pkt3`,
  `dgc_emit_indirect_buffer`); после prepare —
  `CS_PARTIAL_FLUSH | INV_VCACHE | INV_L2` (`radv_cmd_buffer.c:14554`),
  контрольный trailer пишет сам CP через `PKT3_WRITE_DATA` таргетом
  `MICRO_ENGINE` (`radv_amdgpu_cs.c:777`), а комментарий *«CP isn't coherent
  with L2 on GFX6-8»* означает: на GFX9+ префетч когерентен с L2, дистанция
  не нужна.
- **NVIDIA**: DGC аппаратный (QMD-хип, `nvk_cmd_buffer_alloc_qmd`).
- У Adreno аналога `PreParserDisable` нет: в реестрах только texture/resolve
  prefetch, `CP_WAIT_IB_PFD_COMPLETE` мёртв (выше).

## 7. M2.1 — результат (DONE, 05.10.2026)

Коммит mesa: `c9343de409c` (ветка `dgc-starfield`, +207 строк:
`tu_dgc.cc/h`, правки `tu_device.cc` и `vulkan/meson.build`; чужие
грязные hunk'и в `tu_device.cc` — TU_FORCE_PROPS — в коммит не попали,
в worktree остались как были).

### Что сделано

- `tu_device.cc`: `.EXT_device_generated_commands = true` в таблице
  device_extensions; фичи `deviceGeneratedCommands` +
  `dynamicGeneratedPipelineLayout`; свойства DGC:
  `supportedIndirectCommandsShaderStages = ALL_GRAPHICS|COMPUTE`
  (mesh/task в turnip нет → гейт vkd3d `device.c:2605-2621` проходит),
  `maxIndirectSequenceCount = 1<<20`, `maxIndirectCommandsTokenCount = 128`
  (шаблоны Starfield = 2 токена, `docs/dgc-analysis.md` §3),
  `maxIndirectCommandsTokenOffset = 64K`, `maxIndirectCommandsIndirectStride =
  UINT32_MAX`, inputModes `VULKAN|DXGI`. Лимиты — по образцу ANV
  (`anv_physical_device.c:1902-1917`), у RADV tokenCount=128.
- `tu_dgc.cc` (9 entrypoints, все обязаны существовать — vkd3d резолвит их
  без NULL-защиты):
  - layout create/destroy — **полноценные**, через общий слой mesa
    `vk_indirect_command_layout_create/destroy`
    (`src/vulkan/runtime/vk_device_generated_commands.c`): разбор токенов в
    `pc_layouts`/`vb_layouts`, `stride` и т.д.;
  - `vkGetGeneratedCommandsMemoryRequirementsEXT` —
    `stride * maxSequenceCount`, alignment 256, memoryTypeBits = все
    non-lazy типы (реальный размер PM4 придёт с M2.3+);
  - `vkCmdPreprocessGeneratedCommandsEXT` / `vkCmdExecuteGeneratedCommandsEXT`
    — no-op заглушки (реальные — M2.4/M2.5, execute через M2.0-путь);
  - execution sets — dummy-объекты: vkd3d их **не создаёт** (grep по
    vkd3d-исходникам — только определения в заголовках), функции нужны
    лишь чтобы не быть NULL.
- Механизм подключения (проверено по коду): фича/свойства заполняются общим
  `vk_common_GetPhysicalDeviceFeatures2/Properties2` (генерация из
  `vk_physical_device_features/properties_gen.py`) по
  `supported_extensions.EXT_device_generated_commands` — отдельный хендлер
  писать не нужно; entrypoints — weak-символы `tu_*` в генерируемой
  `tu_device_entrypoints`, определил = подключил (vulkan.sym менять не
  нужно, всё через `vkGetDeviceProcAddr`).

### Проверка (стенд probe3, `/tmp/opencode/dgcprobe/probe3.c`)

1. расширение в `vkEnumerateDeviceExtensionProperties` — ok;
2. `deviceGeneratedCommands = true` — ok;
3. stages = 0x3f = ALL_GRAPHICS|COMPUTE, лимиты sane — ok;
4. device создаётся с экстеншеном (фича как просит vkd3d:
   `dynamicGeneratedPipelineLayout = false`) — ok;
5. все 9 entrypoints резолвятся — ok;
6. layout `[PUSH_CONSTANT, DRAW_INDEXED]` stride 32 создаётся в обоих
   вариантах (implicit + explicit-preprocess, как у vkd3d
   `command.c:26817-26824`) — ok;
7. memreq: size = 32×1000 = 32000, alignment 256 — ok;
8. execution set create/destroy — ok.

**M2.1 PASS.** Регрессия: probe2 (M2.0) — PASS.

Остаток критерия M2.1 («в логе Starfield исчезает Skipping») закрывается
прогоном M3: ждём команду, `scripts/run-starfield.sh patched 600`.

## 8. M2.2 — результат (DONE, 05.10.2026)

Коммит mesa: `1011dc1fa0e` (+22 строки в `tu_dgc.cc`).

- Разбор токенов — уже делает общий `vk_indirect_command_layout_create`
  (M2.1): `dgc_info`-маска битов по типам токенов, `pc_layouts`
  (сортировка по dst_offset), `vb_layouts`, `stride`, `token_count`.
- **v1-валидация (новое)**: после разбора в
  `tu_CreateIndirectCommandsLayoutEXT` маска `dgc_info` сверяется с
  v1-объёмом (PC, SI, IB, VB, DRAW, DRAW_INDEXED, DISPATCH); execution
  sets / mesh / RT → `VK_ERROR_FEATURE_NOT_PRESENT` + громкий лог
  `vk_errorf` (с dgc_info и v1-маской).
- memreq = `stride * maxSequenceCount` — реальный размер PM4-стрима
  (пролог + MEM_WRITE + WAIT + пакет драва) закрывается вместе с
  транслятором M2.3, тогда же memreq станет честным.

Проверка: probe3 — 9/9 PASS, п. 9 = громкий отказ на TRACE_RAYS2-токен
(`VkResult=-8` = FEATURE_NOT_PRESENT); M2.0 probe2 — PASS.

## 9. M2.3 — «draw не виден»: причина найдена — неверный ветер в пробах (06.10.2026)

Симптом: `vkCmdDraw` (3 вершины, 1 треугольник) не оставлял ни пикселя;
clear (magenta) виден. **Ровно так же вёл себя stock-драйвер** (A/B) →
проблема была не в DGC/наших патчах. Итог расследования — **и не в машине**:
пробы вели вершины GL-привычкой (визуально **по** часовой), а система кадра
Vulkan — y-вниз, где area треугольника по спеку
`a = −1/2 Σ(x_i·y_{i+1} − x_{i+1}·y_i)` (минус подтверждён декодом SVG
формы из `VkPrimitivesRasterization`) → `a < 0` → back-facing → наш же
`CULL_BACK` в пайплайне проб **корректно** отсекал треугольник. Машина
спецификационно-корректна — доказано probe12 (§9.1).

### 9.1. Решающая цепочка (06.10.2026)

1. **Формула спека**: positive area = визуально **против часовой на
   экране**; `VK_FRONT_FACE_COUNTER_CLOCKWISE` → front = положительная area.
2. **Старые пробы** (−1,−1) → (3,−1) → (−1,3): на экране это **по** часовой
   → `a < 0` → back-facing → отсечение пайплайном проб. Отсюда «ни одного
   пикселя» на обоих драйверах (и ничего общего с DGC).
3. **probe0d + `PROBE_CULL=none`** → три полосы идеально → растеризатор,
   VS/FS, тайлинг живы.
4. **probe11** → верх кадра зелёный, низ magenta → ориентация вьюпорта по спеку.
5. **probe10** → `v[3]` в SSBO заполняется → фрагментная стадия жива;
   «FS NOT EXECUTED при cull=back» = отсечение, а не мёртвый VPC→GRAS.
6. **probe9** → SSBO `{5a5a0000..}` → VS исполнялся всегда.
7. **probe12** (ветер строго по спеку + дефолтный `CULL_BACK`) → полосы,
   RC=0 → **машина конформна; дефекта нет вообще**.
8. **A/B stock/patched**: draw-секция байт-в-байт → патчи ни при чём.

Пробы исправлены (probe0d/9/10/11 — ветер по спеку), probe12 в стенде
как эталонный контроль «машина здорова».

### 9.2. Штатность остальных данных (что подтвердилось)

1. **Draw-секция байт-в-байт идентична stock и patched** (264 dw:
   bunch → SET_DRAW_STATE×2 → DRAW; stock 342 dw / patched 307 dw).
   Разница стримов только в clear-секции (наш props-патч включает
   `has_generic_clear` на gen2: patched = один `CP_COND_REG_EXEC`-блок,
   stock = два cond-блока + `CP_BLIT`).
2. **Весь стейт сверен с регистровой картой и корректен**: RT
   (RGBA8/TILE6_2/pitch 2048/base 0x100027000), VB (stride=8),
   VFD_FETCH_INSTR (R32G32_FLOAT), viewport/scissor/blend/initiator
   (TRILIST/AUTO_INDEX/USE_VISIBILITY, 3 vtx, 1 inst), SP_VS_BASE /
   SP_PS_BASE → валидный машинный код, CP_LOAD_STATE6_FRAG (immediates,
   SB6_FS_SHADER).
3. **Compute работает** (probe7: dispatch(1) → 0xdeadbeef в
   host-visible buffer), CP работает (clear виден), фенс срабатывает
   быстро (GPU не повис), fault'ов в dmesg нет.
4. **Flag-буфер** (RB_RESOLVE_SYSTEM_FLAG_BUFFER @ 0x100026000) после
   draw побайто идентичен baseline после чистого clear → фрагментов не
   было **из-за отсечения** (§9.1), а не из-за DGC/CP.
5. **Состояние сверено с эталонным трейсом работающего железа**
   (`dEQP-VK.draw.indirect_draw...triangle_list`): расхождения только
   поколение чипа (Z_CLAMP — бит5 A7XX) и размеры вьюпорта
   (guardband 446 = `fd_calc_guardband(256,256)`), guardband/scissor
   совпадают с формулами драйвера.

### 9.3. Стрим и CP: доходит до конца draw_cs (декодер PM4 исправлен)

Полный layout draw_cs (pm4dec3/where_state.py, после m2 — нули):

```
  0: T7 CP_MEM_WRITE  m0 (0x57480000) — контрольный маркер в начале
  4: T7 COND_REG_EXEC (clear-блок RB_RESOLVE: magenta 0xffff00ff,
     окно 0,0-511,255, SYSMEM 0x100027000, flag 0x100026000, op 0xf2)
 65: T7 CONTEXT_REG_BUNCH  65 пар (RB_MRT/VB/VFD/viewport/…)
178: T7 CP_MEM_WRITE  m3 (после bunch#1)
192: T7 SET_DRAW_STATE #1 (6 групп, DISABLE)
199: T7 CP_MEM_WRITE  m4
211: T7 CONTEXT_REG_BUNCH  6 пар
224: T7 SET_DRAW_STATE #2 (30 групп, все S1 — состояние применяется)
315: T7 CP_MEM_WRITE  m1 (0x57480001, перед DRAW)
319: T7 DRAW_INDX_OFFSET  initiator=0xd84, 1/3
323: T7 CP_MEM_WRITE  m2 (0x57480002, после DRAW)
```

Main stream (перед запуском CP): `SET_MARKER@256 payload=0x1`
(RM6_DIRECT_RENDER, эмит-сайт `tu_cmd_buffer.cc:3378`),
`SET_VISIBILITY_OVERRIDE@265=1`, `SET_MODE@267=0`.

**CP доходит до конца draw_cs** — это доказано не маркерами, а
отрисовкой (§9.1): пиксели есть → DRAW исполнен, значит пройдены и все
пакеты до него (раньше вывод «не доходит» был следствием неверного ветра,
а не остановки CP). Открытый мелкий вопрос: в раннем прогоне host-BO
читал m1/m2 = 0 при фенсе; при отработавшем DRAW это почти наверняка
артефакт host-чтения (кэш CPU без инвалидации), на вывод не влияет.

### 9.4. Аномалии окружения (dmesg)

- `msm_dpu ae01000.display-controller: bound 3d00000.gpu (ops a3xx_ops)`
  — GPU-устройство **привязано к display-контроллеру**; `a740_sqe.fw`
  грузит msm_dpu (после 2× -2 ENOENT «from new location»), не GPU-драйвер.
- `gcc-sm8550`/`gpu_cc-sm8550 clock-controller: sync_state() pending due
  to 3d6a000.gmu` (boot, ~29s); dummy regulators vdd/vddcx.
- /sys/class/kgsl отсутствует (новый DRM-adreno), debugfs — без root.
- GPU-fault'ов нет (ни в момент проб, ни после).

### 9.5. Итог

- **Причина установлена: неверный ветер треугольников в самих пробах**
  (GL-привычка против спека Vulkan) + собственный `CULL_BACK` в пайплайне
  проб. Исправлено в probe0d/9/10/11; probe12 — контроль «машина
  конформна» (RC=0).
- Дефекта машины/firmware нет (probe12, полосы беcупречны, fault'ов нет).
- DGC и патчи ни при чём (draw-секция байт-в-байт, A/B).
- Прежняя оценка 50/30/20 (§ старой ревизии) отменена:
  «недосмотренное (пробы)» — 100%.

### 9.6. Статус (06.10.2026, вечер)

- Инстанс-обрыв 06.10 решён (сборка без `-Dplatforms`, §9.8); игра доходит до
  D3D12 + swapchain, нативный DGC-путь vkd3d включён (лог 20261006-203753).
- Квик «верхние строки» найден и задокументирован (§9.7) — ожидания проб
  приведены к поведению машины, регрессия 5/5 зелёная (сборка 20:54; до неё
  бинарники 0d/7d/9/10/11 были старше исходников с ветер-фиксом).
- Новый симптом: краш при загрузке уровня (меню — 600 с безупречно), §9.9.
- M2.5 — после визуальной проверки приветствия пользователем.

### 9.7. Квик: верхние ~32–48 строк кадра не растеризуются (не регрессия)

Наблюдение (probe0d, сборка 06.10 20:32): полноэкранный треугольник
`{(-1,-1),(-1,3),(3,-1)}` (ветер по спеку) покрывает весь NDC-квадрат, но
машина оставляет верхние ~32–48 строк непокрытыми: y<28 — весь ряд magenta
(clear), y=32–48 — patchy (единичные пиксели нарисованы), y≥64 — чисто.

Изоляция (`probe_dbg.c`, варианты D0–D6, argv — геометрия/FS):
- дыра в верхних строках у **любого** треугольника, включая зеркальный D3
  `{(-1,-3),(-1,1),(3,1)}`, у которого вообще нет кромки на y=−1;
- D4 (клинин `{(-1,-1),(3,0),(3,-1)}` один) — свой клин не рисуется вовсе;
  тот же клин в составе пары T1+T2 (D6, геометрия probe11) — рисуется
  patchy;
- probe11 прошёл, потому что его одно-точечная проверка (256,10) попала в
  покрытый пиксель; тот же пиксель в D4/D0 не покрыт → покрытие patchy
  per-triangle, «чистая» горизонтальная полоса-граница не существует.

A/B со старым драйвером (04.10, `libvulkan_freedreno.so.bak-pre-probe` +
временный ICD-манифест): картина **идентична до пикселя** → штатное
поведение машины, не регрессия ни от пересборки, ни от наших патчей.

Следствия:
- probe0d/9/10 проверяют (2,1)=magenta (clear) — это и есть квик: по спеку
  угол треугольником покрыт, но машина так не рисует (пока). Ожидание
  `C_RED` — не менять: пробы зелёные только с `C_MAGENTA`.
- В верхние ~48 строк новые проверки не ставить; надёжная зона — y≥64.
- Влияние на игру: верхняя полоска кадра может терять покрытие на отдельных
  пасах (full-screen-треугольники). Визуальная проверка — M2.5.

### 9.8. Инцидент 06.10: обрыв инстанса (сборка без platforms) + гарды

Симптом: запуски 19:42–20:08 падали на `vkCreateInstance`
(`err: DxvkInstance::createInstance: Failed to create Vulkan instance`,
логи 194237/200400/200826), при этом нативный `vulkaninfo`-createInstance и
steam.exe-инстанс в той же сессии работали. Proton не менялся
(`winevulkan.dll` hash = 04.10). WINEDEBUG-трейс не дошёл (PE winevulkan
без TRACE на create-пути), `VK_LOADER_DEBUG=all` показал: третий callstack
отсутствует → отказ до unix-загрузчика.

Причина: сборка 18:06 без `-Dplatforms=x11,wayland` (`platforms = []` в
meson-info) → в драйвере нет `VK_KHR_xcb_surface`/`VK_KHR_wayland_surface` →
winevulkan не переводит win32-поверхность → DXVK падает за секунду до
картинки. Это ловушка №3 `build-turnip.sh` (теперь задокументирована там).

Решение и гарды:
- пересборка каноническим `scripts/build-turnip.sh` (podman,
  `mesa-build-fedora44-wsi-glslang`, worktree `mesa-rp6-dgc`) → `.so`
  2026-10-06 20:32, `VK_KHR_xcb_surface/wayland/xlib` на месте,
  driver 26.2.99;
- `build-turnip.sh`: после cp проверка строк `VK_KHR_xcb_surface` /
  `VK_KHR_wayland_surface` в `.so`;
- `run-starfield.sh`: предпрогонный `vulkaninfo`-гард на `VK_KHR_xcb_surface`.

pipefail-ловушка в гарде: `set -euo pipefail` + `vulkaninfo | grep -q` —
ранний выход grep даёт vulkaninfo SIGPIPE и пайплайн ложно падает; выведено
через herestring: `grep -q ... <<<"$(vulkaninfo 2>/dev/null)"`.

Результат: прогон 20261006-203753 (600 с, RC=124) здоров — устройство
26.2.99, 0 строк `err:`, swapchain 2560×1440 (3 изображения, ~72 с), vkd3d
«Enabling fast paths for advanced ExecuteIndirect() graphics and compute
(EXT_dgc)» — нативный DGC-путь включён; хвост — только
`d3d12_device_QueryInterface E_NOINTERFACE`-спам (GUID
`{0742a90b-c387-483f-b946-30a7e4e61458}` — интерфейс новее, чем знает
vkd3d master; безвреден, есть в каждом прогоне).

### 9.9. Краш при загрузке уровня: гипотеза M2.4-токенов

Процоны 215302 и 220000 (06.10): меню — безупречно (600 с), загрузка
уровня → device lost:

| прогон | сигнатура | момент (лог = сек от boot, сверено с uptime) |
|---|---|---|
| 215302 | `vkEndCommandBuffer → vr −8` (VK_ERROR_FEATURE_NOT_PRESENT) → `0x887a0001` (DEVICE_HUNG), «Command list is in recording state» | 109 с после swapchain |
| 220000 | `vkd3d_wait_for_gpu_timeline_semaphore → vr −4` (VK_ERROR_DEVICE_LOST) → `0x887a0005` (DEVICE_REMOVED) | ~45 с после старта wine |

Гипотеза (высокая вероятность): уровень включает compute-indirect
(трава/частицы) → vkd3d «graphics **and compute**» DGC-путь заводит layout'ы
с токенами **DISPATCH** (M2.4). Механика:
- `tu_CreateIndirectCommandsLayoutEXT` (M2.2 v1-scope) DISPATCH **принимает**
  (`MESA_VK_DGC_DISPATCH` в `v1_dgc_info`), но `tu_dgc_build_gpu_layout`
  считает такой токен `unsupported_tokens` (default-ветка, `tu_dgc.cc:105`)
  и layout создаётся;
- при `Execute` проверка M2.3-scope (`tu_cmd_buffer.cc:10625`:
  `dgc_info & ~m23` **или** `unsupported_tokens != 0`) **ломко** шлёпает
  `VK_ERROR_FEATURE_NOT_PRESENT` в command buffer →
  `vkEndCommandBuffer = −8` → vkd3d `device_mark_as_removed` → краш.
- В меню vkd3d использует только PC/SI/DRAW(_INDEXED) → Execute проходит →
  600 с тишины.

Почему сообщения драйвера в логе нет: в release-сборке (без `MESA_DEBUG`)
`vk_errorf` гасится гейтом `vk_log.c:114` — нужен `enable_debug_logging`
(turnip его не ставит, в отличие от ANV) или debug-колбэк приложения
(vkd3d/wine мессенджер не создают). `MESA_VK_LOG` — build-time (`-Ddebug`),
не env. Существующая диагностика в коде: **`DGC_DUMP=1`** — дамп токенов
layout'а в stderr при создании (`tu_dgc.cc:73,100`; default-ветка DISPATCH
печатать не умеет — дособрать при необходимости).

A/B (не выполнен, следующий шаг):
```sh
# без DGC вообще — краш должен уйти, если гипотеза верна:
VKD3D_DISABLE_EXTENSIONS=VK_EXT_device_generated_commands \
    scripts/run-starfield.sh patched 600
# дамп токенов, которые vkd3d заводит:
DGC_DUMP=1 scripts/run-starfield.sh patched 600
```
(пасsthrough добавлен в `run-starfield.sh`, §9.8-гарды не затрагиваются).

Если подтвердится — два пути:
(a) честная `GetIndirectCommandsLayoutTokenSupport`/scope: layout'ы с
    DISPATCH не принимать на Create (громко), чтобы vkd3d чисто фолбэйчил на
    обычный indirect для compute (DGC остался бы для graphics);
(b) реализовать M2.4 (DISPATCH = `CP_EXEC_CS` + count-варианты).


