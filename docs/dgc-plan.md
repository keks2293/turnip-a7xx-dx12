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
